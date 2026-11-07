package server

import (
	"net"

	"github.com/openconfig/gnmi/proto/gnmi"
	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/agent"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/config"
	"google.golang.org/grpc"
)

func NewGRPC(
	ocsAgent *agent.Agent,
	capability config.CapabilityProfile,
	options ...grpc.ServerOption,
) *grpc.Server {
	server := grpc.NewServer(options...)
	gnmi.RegisterGNMIServer(server, &gnmiServer{
		agent: ocsAgent, capability: capability,
	})
	ocsv1.RegisterOcsOperationsServer(server, &operationsServer{agent: ocsAgent})
	return server
}

func Listen(address string) (net.Listener, error) {
	return net.Listen("tcp", address)
}
