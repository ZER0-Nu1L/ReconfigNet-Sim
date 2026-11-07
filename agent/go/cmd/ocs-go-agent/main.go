package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"runtime"
	"syscall"
	"time"

	"github.com/reconfig-net-sim/ocs-go-agent/internal/agent"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/backend"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/bfrtclient"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/config"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/observe"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/server"
	"google.golang.org/grpc"
)

type runtimeOptions struct {
	writeBuffer   int
	workers       uint
	procs         int
	tracePrefix   string
	traceDuration time.Duration
}

func main() {
	configPath := flag.String("config", "", "path to Agent JSON configuration")
	diagnostic := flag.Bool("diagnostic", false, "enable opt-in northbound gRPC stage diagnostics")
	var tuning runtimeOptions
	flag.IntVar(&tuning.writeBuffer, "grpc-write-buffer-bytes", -1, "server write buffer; -1 preserves gRPC default")
	flag.UintVar(&tuning.workers, "grpc-stream-workers", 0, "experimental gRPC worker pool; 0 preserves default")
	flag.IntVar(&tuning.procs, "gomaxprocs", 0, "experimental runtime parallelism; 0 preserves default")
	flag.StringVar(&tuning.tracePrefix, "trace-prefix", "", "opt-in absolute prefix for bounded SIGUSR1 runtime captures")
	flag.DurationVar(&tuning.traceDuration, "trace-duration", 10*time.Second, "runtime capture duration, maximum 30s")
	checkConfig := flag.Bool("check-config", false, "validate configuration without connecting to a device")
	flag.Parse()
	if tuning.writeBuffer < -1 || tuning.workers > 1024 || tuning.procs < 0 || tuning.procs > 128 {
		log.Fatal("invalid experimental runtime option")
	}
	if tuning.procs > 0 {
		runtime.GOMAXPROCS(tuning.procs)
	}
	if *configPath == "" {
		log.Fatal("--config is required")
	}
	if *checkConfig {
		if _, err := config.Load(*configPath); err != nil {
			log.Fatal(err)
		}
		fmt.Println("configuration valid; no device connection opened")
		return
	}
	if err := run(*configPath, *diagnostic, tuning); err != nil {
		log.Fatal(err)
	}
}

func run(configPath string, diagnostic bool, tuning runtimeOptions) error {
	loaded, err := config.Load(configPath)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(
		context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if tuning.tracePrefix != "" {
		closeTrace, err := observe.OnSignal(ctx, tuning.tracePrefix, tuning.traceDuration)
		if err != nil {
			return err
		}
		defer closeTrace()
	}
	var deviceBackend backend.Device
	if b := loaded.DirectBFRT; b != nil {
		deviceBackend, err = backend.NewDirect(ctx, backend.DirectOptions{
			LockFile: b.OwnershipLockFile, Consistency: loaded.Device.ConsistencyMode, TwoPhase: b.TwoPhase,
			BFRT: bfrtclient.Config{Target: b.Target, ClientID: b.ClientID, DeviceID: b.DeviceID, PipeID: b.PipeID, P4Name: b.P4Name, TableName: b.TableName, ActionName: b.ActionName, IngressField: b.IngressField, EgressField: b.EgressField, LogicalToDevice: b.PortMap, Timeout: time.Duration(b.TimeoutSeconds * float64(time.Second))},
		})
	} else {
		deviceBackend, err = backend.New(ctx, loaded.Worker.Target, time.Duration(loaded.Worker.TimeoutSeconds*float64(time.Second)))
	}
	if err != nil {
		return err
	}
	defer func() { stop(); deviceBackend.Close() }()
	ocsAgent, err := agent.New(
		ctx, loaded.Model.Inventory, loaded.Model.Connections,
		deviceBackend, loaded.Model.Profile,
		loaded.Device.ConsistencyMode,
		time.Duration(loaded.Control.LeaseSeconds*float64(time.Second)),
		time.Duration(loaded.Control.ReconcileIntervalSeconds*float64(time.Second)),
		loaded.StartupPolicy)
	if err != nil {
		return err
	}

	address := fmt.Sprintf("%s:%d", loaded.GRPCAPI.Host, loaded.GRPCAPI.Port)
	listener, err := server.Listen(address)
	if err != nil {
		return err
	}
	var grpcOptions []grpc.ServerOption
	if tuning.writeBuffer >= 0 {
		grpcOptions = append(grpcOptions, grpc.WriteBufferSize(tuning.writeBuffer))
	}
	if tuning.workers > 0 {
		grpcOptions = append(grpcOptions, grpc.NumStreamWorkers(uint32(tuning.workers)))
	}
	if diagnostic {
		grpcOptions = append(grpcOptions, grpc.StatsHandler(server.DiagnosticStats{}))
	}
	grpcServer := server.NewGRPC(ocsAgent, loaded.CapabilityProfile, grpcOptions...)
	defer grpcServer.Stop()
	defer listener.Close()
	errCh := make(chan error, 2)
	if loaded.FramedAPI != nil {
		fast, err := net.Listen("tcp", net.JoinHostPort(loaded.FramedAPI.Host, fmt.Sprint(loaded.FramedAPI.Port)))
		if err != nil {
			return err
		}
		defer fast.Close()
		go func() { errCh <- server.ServeFramed(ctx, fast, ocsAgent) }()
	}
	go func() {
		log.Printf("Starting %s gRPC API on %s", loaded.DeploymentProfile, address)
		if err := grpcServer.Serve(listener); err != nil {
			errCh <- err
		}
	}()

	select {
	case <-ctx.Done():
	case err := <-errCh:
		return err
	}
	drained := make(chan struct{})
	go func() {
		grpcServer.GracefulStop()
		close(drained)
	}()
	select {
	case <-drained:
	case <-time.After(10 * time.Second):
		grpcServer.Stop()
		<-drained
	}
	return nil
}
