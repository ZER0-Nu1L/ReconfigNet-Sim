package main

import (
	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"strings"
)

// This client changes buffering only; authentication and no-replay behavior are
// identical to the default benchmark connection. It uses a persistent channel.
func newBufferedGRPCClient(target string, bytes int) (*grpcClient, error) {
	connection, err := grpc.NewClient(strings.TrimPrefix(target, "grpc://"),
		grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithDisableRetry(), grpc.WithWriteBufferSize(bytes))
	if err != nil {
		return nil, err
	}
	return &grpcClient{connection: connection, client: ocsv1.NewOcsOperationsClient(connection)}, nil
}
