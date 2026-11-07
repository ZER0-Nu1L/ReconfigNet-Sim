package agent_test

import (
	"net"
	"os/exec"
	"path/filepath"
	"slices"
	"testing"
	"time"

	ocsclient "github.com/reconfig-net-sim/ocs-go-agent/client"
	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/agent"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/config"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/model"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/server"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func TestIndependentExampleRestoresOriginalPermutation(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "remote-switch")
	build := exec.CommandContext(t.Context(), "go", "build", "-o", binary, "../../examples/remote-switch")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build public example: %v\n%s", err, out)
	}
	w := &fakeWorker{entries: make(map[model.Pair]struct{}), generation: 1}
	b, stop := startWorker(t, w)
	defer stop()
	inv := inventory(t)
	pi := []uint32{2, 1, 4, 3}
	initial, err := model.FromPermutation(inv, pi)
	if err != nil {
		t.Fatal(err)
	}
	a, err := agent.New(t.Context(), inv, initial, b, "test", "CACHED_SYNC", 30*time.Second, time.Hour, "REAPPLY_DESIRED")
	if err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	go server.ServeFramed(t.Context(), listener, a)
	command := exec.CommandContext(t.Context(), binary, "--target", "framed://"+listener.Addr().String(), "--pi", "3,4,1,2")
	if out, err := command.CombinedOutput(); err != nil {
		t.Fatalf("independent experiment failed: %v\n%s", err, out)
	}
	actual, err := a.Connections().Permutation()
	if err != nil || !slices.Equal(actual, pi) {
		t.Fatal("example did not restore original connections", actual, err)
	}
}

func TestReusableTransportsShareAuthority(t *testing.T) {
	w := &fakeWorker{entries: make(map[model.Pair]struct{}), generation: 1}
	backend, stop := startWorker(t, w)
	defer stop()
	inv := inventory(t)
	initial, err := model.FromPermutation(inv, []uint32{2, 1, 4, 3})
	if err != nil {
		t.Fatal(err)
	}
	a, err := agent.New(t.Context(), inv, initial, backend, "test", "CACHED_SYNC", 30*time.Second, time.Hour, "REAPPLY_DESIRED")
	if err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	gs := server.NewGRPC(a, config.CapabilityProfile{}, grpc.StatsHandler(server.DiagnosticStats{}))
	defer gs.Stop()
	go gs.Serve(listener)
	fast, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer fast.Close()
	go server.ServeFramed(t.Context(), fast, a)
	for index, target := range []string{listener.Addr().String(), "framed://" + fast.Addr().String()} {
		t.Run(target, func(t *testing.T) {
			conn, err := ocsclient.Dial(t.Context(), target)
			if err != nil {
				t.Fatal(err)
			}
			defer conn.Close()
			client := ocsv1.NewOcsOperationsClient(conn)
			lease, err := client.AcquireControl(t.Context(), &ocsv1.AcquireControlRequest{ClientId: "transport-test"})
			if err != nil {
				t.Fatal(err)
			}
			ctx := metadata.AppendToOutgoingContext(t.Context(), "x-ocs-control-lease", lease.LeaseToken, "x-ocs-compact-reply", "true")
			pi := []uint32{3, 4, 1, 2}
			if index%2 == 1 {
				pi = []uint32{2, 1, 4, 3}
			}
			request := &ocsv1.ApplyBatchRequest{Intent: &ocsv1.ApplyBatchRequest_Permutation{Permutation: &ocsv1.Permutation{Pi: pi}}, HasExpectedRevision: true, ExpectedRevision: lease.Revision}
			var trailer metadata.MD
			var opts []grpc.CallOption
			if index == 0 {
				ctx = metadata.AppendToOutgoingContext(ctx, "x-ocs-diagnostic", "true")
				opts = []grpc.CallOption{grpc.Trailer(&trailer)}
			}
			reply, err := client.ApplyBatch(ctx, request, opts...)
			if err != nil {
				t.Fatal(err)
			}
			if reply.Result != "updated" || reply.State.Revision != lease.Revision+1 {
				t.Fatalf("transport did not commit a real change: %v", reply)
			}
			if index == 0 {
				for _, key := range []string{"x-ocs-handler-ns", "x-ocs-core-call-ns", "x-ocs-pre-core-ns", "x-ocs-reply-ns", "x-ocs-input-ns", "x-ocs-dispatch-ns", "x-ocs-output-ns"} {
					if len(trailer.Get(key)) != 1 {
						t.Fatalf("missing diagnostic interval %s: %v", key, trailer)
					}
				}
			}
			request.ExpectedRevision = reply.State.Revision
			repeat, err := client.ApplyBatch(ctx, request)
			if err != nil || repeat.Result != "unchanged" || repeat.State.Revision != reply.State.Revision {
				t.Fatalf("idempotency: %v %v", repeat, err)
			}
			if a.Snapshot().Revision != reply.State.Revision {
				t.Fatal("separate authority")
			}
			// Invalid revisions are rejected without changing the backend.
			request.ExpectedRevision = 999999
			if _, err := client.ApplyBatch(ctx, request); status.Code(err) != codes.Aborted {
				t.Fatalf("conflict: %v", err)
			}
			if _, err := client.ReleaseControl(t.Context(), &ocsv1.ReleaseControlRequest{LeaseToken: lease.LeaseToken}); err != nil {
				t.Fatal(err)
			}
		})
	}
}
