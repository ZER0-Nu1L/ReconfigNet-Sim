// Package client connects independent experiments to an OCS Agent.
// Use the generated OcsOperations client with the returned connection. Writes
// are never replayed automatically; after an uncertain outcome query state.
package client

import (
	"context"
	"strings"

	"github.com/reconfig-net-sim/ocs-go-agent/internal/framed"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// Connection supports the generated unary OcsOperations stubs.
type Connection interface {
	grpc.ClientConnInterface
	Close() error
}

// Dial opens framed://host:port, framed-unix:///path, grpc://host:port or a
// plain gRPC endpoint. Endpoints use the existing trusted management network;
// this helper does not add transport authentication or encryption.
func Dial(ctx context.Context, target string) (Connection, error) {
	if strings.HasPrefix(target, "framed://") || strings.HasPrefix(target, "framed-unix://") {
		return framed.Dial(ctx, target)
	}
	return grpc.NewClient(strings.TrimPrefix(target, "grpc://"), grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithDisableRetry())
}
