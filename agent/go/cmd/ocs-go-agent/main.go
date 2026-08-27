package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/reconfig-net-sim/ocs-go-agent/internal/agent"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/backend"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/config"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/server"
)

func main() {
	configPath := flag.String("config", "", "path to p4app JSON configuration")
	flag.Parse()
	if *configPath == "" {
		log.Fatal("--config is required")
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
	deviceBackend, err := backend.New(
		ctx, loaded.Worker.Target,
		time.Duration(loaded.Worker.TimeoutSeconds*float64(time.Second)))
	if err != nil {
		return err
	}
	defer deviceBackend.Close()
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
	errCh := make(chan error, 1)
	go func() {
		log.Printf("Starting Go split gRPC API on %s", address)
		if err := grpcServer.Serve(listener); err != nil {
			errCh <- err
		}
	}()

	select {
	case <-ctx.Done():
	case err := <-errCh:
		return err
	}
	grpcServer.GracefulStop()
	return nil
}
