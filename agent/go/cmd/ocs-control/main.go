package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

type output struct {
	Operation string          `json:"operation"`
	Target    string          `json:"target"`
	Reply     json.RawMessage `json:"reply"`
}

func main() {
	operation := flag.String("operation", "get", "get, apply, mode, or recover")
	target := flag.String("target", "127.0.0.1:9339", "OcsOperations gRPC target")
	piText := flag.String("pi", "", "comma-separated permutation for apply")
	strategyText := flag.String("strategy", "delta", "full or delta")
	transportText := flag.String("transport", "native-batch", "sequential or native-batch")
	delayUS := flag.Uint64("delay-us", 0, "delete-to-install delay in microseconds")
	modeText := flag.String("mode", "ocs", "ocs or debug")
	timeout := flag.Duration("timeout", 10*time.Second, "RPC timeout")
	flag.Parse()

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	connection, err := grpc.NewClient(
		*target, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		fatal(err)
	}
	defer connection.Close()
	client := ocsv1.NewOcsOperationsClient(connection)

	var reply any
	switch strings.ToLower(*operation) {
	case "get":
		reply, err = client.GetPermutation(ctx, &ocsv1.Empty{})
	case "apply":
		var pi []uint32
		pi, err = parsePI(*piText)
		var selectedStrategy ocsv1.ExecutionStrategy
		if err == nil {
			selectedStrategy, err = strategy(*strategyText)
		}
		var selectedTransport ocsv1.Transport
		if err == nil {
			selectedTransport, err = transport(*transportText)
		}
		if err == nil {
			reply, err = withLease(ctx, client, func(writeCtx context.Context, revision uint64) (any, error) {
				return client.ApplyBatch(writeCtx, &ocsv1.ApplyBatchRequest{
					Intent: &ocsv1.ApplyBatchRequest_Permutation{
						Permutation: &ocsv1.Permutation{Pi: pi},
					},
					Strategy:            selectedStrategy,
					Transport:           selectedTransport,
					DelayUs:             *delayUS,
					HasExpectedRevision: true,
					ExpectedRevision:    revision,
				})
			})
		}
	case "mode":
		var selectedMode ocsv1.Mode
		selectedMode, err = mode(*modeText)
		var selectedTransport ocsv1.Transport
		if err == nil {
			selectedTransport, err = transport(*transportText)
		}
		if err == nil {
			reply, err = withLease(ctx, client, func(writeCtx context.Context, revision uint64) (any, error) {
				return client.SetMode(writeCtx, &ocsv1.SetModeRequest{
					Mode:                selectedMode,
					Transport:           selectedTransport,
					DelayUs:             *delayUS,
					HasExpectedRevision: true,
					ExpectedRevision:    revision,
				})
			})
		}
	case "recover":
		reply, err = withLease(ctx, client, func(writeCtx context.Context, revision uint64) (any, error) {
			return client.RecoverDeviceState(writeCtx, &ocsv1.RecoverDeviceStateRequest{
				Mode:                ocsv1.RecoveryMode_RECOVERY_MODE_REAPPLY_DESIRED,
				HasExpectedRevision: true,
				ExpectedRevision:    revision,
			})
		})
	default:
		err = fmt.Errorf("operation must be get, apply, mode, or recover")
	}
	if err != nil {
		fatal(err)
	}
	raw, err := json.Marshal(reply)
	if err != nil {
		fatal(err)
	}
	encoded, err := json.MarshalIndent(output{
		Operation: strings.ToLower(*operation), Target: *target, Reply: raw,
	}, "", "  ")
	if err != nil {
		fatal(err)
	}
	fmt.Println(string(encoded))
}

func withLease(
	ctx context.Context,
	client ocsv1.OcsOperationsClient,
	operation func(context.Context, uint64) (any, error),
) (any, error) {
	current, err := client.GetRuntime(ctx, &ocsv1.Empty{})
	if err != nil {
		return nil, err
	}
	lease, err := client.AcquireControl(ctx, &ocsv1.AcquireControlRequest{
		ClientId: "ocs-control",
	})
	if err != nil {
		return nil, err
	}
	defer client.ReleaseControl(ctx, &ocsv1.ReleaseControlRequest{
		LeaseToken: lease.GetLeaseToken(),
	})
	writeCtx := metadata.AppendToOutgoingContext(
		ctx, "x-ocs-control-lease", lease.GetLeaseToken())
	return operation(writeCtx, current.GetState().GetRevision())
}

func parsePI(value string) ([]uint32, error) {
	parts := strings.Split(value, ",")
	if value == "" || len(parts)%2 != 0 {
		return nil, fmt.Errorf("pi must contain an even number of comma-separated ports")
	}
	pi := make([]uint32, len(parts))
	for index, part := range parts {
		parsed, err := strconv.ParseUint(strings.TrimSpace(part), 10, 32)
		if err != nil {
			return nil, fmt.Errorf("invalid pi entry %q", part)
		}
		pi[index] = uint32(parsed)
	}
	return pi, nil
}

func strategy(value string) (ocsv1.ExecutionStrategy, error) {
	switch strings.ToLower(value) {
	case "full":
		return ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_FULL, nil
	case "delta":
		return ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_DELTA, nil
	default:
		return ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_UNSPECIFIED,
			fmt.Errorf("strategy must be full or delta")
	}
}

func transport(value string) (ocsv1.Transport, error) {
	switch strings.ToLower(strings.ReplaceAll(value, "-", "_")) {
	case "native_batch":
		return ocsv1.Transport_TRANSPORT_NATIVE_BATCH, nil
	case "sequential":
		return ocsv1.Transport_TRANSPORT_SEQUENTIAL, nil
	default:
		return ocsv1.Transport_TRANSPORT_UNSPECIFIED,
			fmt.Errorf("transport must be sequential or native-batch")
	}
}

func mode(value string) (ocsv1.Mode, error) {
	switch strings.ToLower(value) {
	case "debug":
		return ocsv1.Mode_MODE_DEBUG, nil
	case "ocs":
		return ocsv1.Mode_MODE_OCS, nil
	default:
		return ocsv1.Mode_MODE_UNSPECIFIED,
			fmt.Errorf("mode must be ocs or debug")
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
