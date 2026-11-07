// remote-switch is a minimal independent experiment, with no benchmark imports.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/reconfig-net-sim/ocs-go-agent/client"
	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"google.golang.org/grpc/metadata"
)

func main() {
	target := flag.String("target", "", "external Agent endpoint, e.g. framed://device:9340")
	pi := flag.String("pi", "", "new permutation, e.g. 3,4,1,2,6,5")
	flag.Parse()
	if err := run(*target, *pi); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(target, pi string) error {
	if target == "" || pi == "" {
		return fmt.Errorf("--target and --pi are required")
	}
	var values []uint32
	for token := range strings.SplitSeq(pi, ",") {
		v, err := strconv.ParseUint(strings.TrimSpace(token), 10, 32)
		if err != nil {
			return err
		}
		values = append(values, uint32(v))
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	conn, err := client.Dial(ctx, target)
	if err != nil {
		return err
	}
	defer conn.Close()
	api := ocsv1.NewOcsOperationsClient(conn)
	lease, err := api.AcquireControl(ctx, &ocsv1.AcquireControlRequest{ClientId: "independent-remote-switch", RequestedLeaseSeconds: 30})
	if err != nil {
		return err
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		if _, err := api.ReleaseControl(cleanup, &ocsv1.ReleaseControlRequest{LeaseToken: lease.LeaseToken}); err != nil {
			fmt.Fprintln(os.Stderr, "release control:", err)
		}
	}()
	before, err := api.GetPermutation(ctx, &ocsv1.Empty{})
	if err != nil {
		return err
	}
	writeCtx := metadata.AppendToOutgoingContext(ctx, "x-ocs-control-lease", lease.LeaseToken, "x-ocs-compact-reply", "true")
	apply := func(p []uint32, revision uint64) (*ocsv1.OperationReply, error) {
		return api.ApplyBatch(writeCtx, &ocsv1.ApplyBatchRequest{Intent: &ocsv1.ApplyBatchRequest_Permutation{Permutation: &ocsv1.Permutation{Pi: p}}, Strategy: ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_DELTA, Transport: ocsv1.Transport_TRANSPORT_NATIVE_BATCH, HasExpectedRevision: true, ExpectedRevision: revision})
	}
	result, err := apply(values, lease.Revision)
	if err != nil {
		return fmt.Errorf("apply failed; inspect authoritative state before another write: %w", err)
	}
	fmt.Printf("result=%s revision=%d SDK=%d us\n", result.Result, result.State.Revision, result.GetTiming().GetProgrammingTotalUs())
	restored, err := apply(before.GetPermutation().GetPi(), result.State.Revision)
	if err != nil {
		return fmt.Errorf("restore failed; inspect authoritative state: %w", err)
	}
	fmt.Printf("restored revision=%d\n", restored.State.Revision)
	return nil
}
