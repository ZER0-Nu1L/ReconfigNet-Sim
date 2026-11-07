package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/reconfig-net-sim/ocs-go-agent/internal/agent"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/backend"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/bfrtclient"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/config"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/server"
)

func main() {
	configPath := flag.String("config", "", "path to Agent JSON configuration")
	checkConfig := flag.Bool("check-config", false, "validate configuration without connecting to a device")
	flag.Parse()
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
	if err := run(*configPath); err != nil {
		log.Fatal(err)
	}
}

func run(configPath string) error {
	loaded, err := config.Load(configPath)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(
		context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
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
	grpcServer := server.NewGRPC(ocsAgent, loaded.CapabilityProfile)
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
