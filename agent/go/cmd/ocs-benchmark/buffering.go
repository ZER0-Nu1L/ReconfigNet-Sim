package main

import (
	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"strings"
)

// This client changes buffering only; authentication and no-replay behavior are
// identical to the default benchmark connection. It uses a persistent channel.
func newConfiguredGRPCClient(target string, bytes int, diagnostic bool) (*grpcClient, error) {
	options := []grpc.DialOption{grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithDisableRetry()}
	if bytes >= 0 {
		options = append(options, grpc.WithWriteBufferSize(bytes))
	}
	if diagnostic {
		options = append(options, grpc.WithStatsHandler(clientDiagnosticStats{}))
	}
	connection, err := grpc.NewClient(strings.TrimPrefix(target, "grpc://"), options...)
	if err != nil {
		return nil, err
	}
	return &grpcClient{connection: connection, client: ocsv1.NewOcsOperationsClient(connection)}, nil
}
