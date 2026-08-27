package agent_test

import (
	"context"
	"maps"
	"net"
	"sync"
	"testing"
	"time"

	backendv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/backendv1"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/agent"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/apierr"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/backend"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/model"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
)

type fakeWorker struct {
	backendv1.UnimplementedDeviceBackendServer
	mu         sync.Mutex
	entries    map[model.Pair]struct{}
	inFlight   int
	maxFlight  int
	delay      time.Duration
	generation uint64
}

func (w *fakeWorker) Capabilities(
	context.Context,
	*backendv1.Empty,
) (*backendv1.BackendCapabilities, error) {
	return &backendv1.BackendCapabilities{
		Backend:     "test",
		Readback:    true,
		NativeBatch: true,
		Transports:  []string{"SEQUENTIAL", "NATIVE_BATCH"},
	}, nil
}

func (w *fakeWorker) ReadEntries(
	context.Context,
	*backendv1.Empty,
) (*backendv1.ReadEntriesResponse, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	return &backendv1.ReadEntriesResponse{
		Entries: protoPairs(w.entries), Generation: w.generation,
		CacheStatus: "READY",
	}, nil
}

func (w *fakeWorker) ApplyTransition(
	ctx context.Context,
	request *backendv1.ApplyTransitionRequest,
) (*backendv1.ApplyTransitionResponse, error) {
	w.mu.Lock()
	w.inFlight++
	w.maxFlight = max(w.maxFlight, w.inFlight)
	if !maps.Equal(w.entries, modelPairs(request.GetExpectedEntries())) {
		w.inFlight--
		w.mu.Unlock()
		return &backendv1.ApplyTransitionResponse{
			Restored: true, ErrorCode: "ABORTED",
			Error: "device state does not match expected entries",
		}, nil
	}
	if request.GetExpectedGeneration() != w.generation {
		w.inFlight--
		w.mu.Unlock()
		return &backendv1.ApplyTransitionResponse{
			Restored: true, ErrorCode: "FAILED_PRECONDITION",
			Error: "generation mismatch", Generation: w.generation,
			CacheStatus: "READY",
		}, nil
	}
	w.mu.Unlock()
	select {
	case <-time.After(w.delay):
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	w.mu.Lock()
	w.entries = modelPairs(request.GetTargetEntries())
	w.generation++
	w.inFlight--
	w.mu.Unlock()
	return &backendv1.ApplyTransitionResponse{
		Success:     true,
		Restored:    true,
		Generation:  w.generation,
		CacheStatus: "READY",
		Timing: &backendv1.OperationTiming{
			Strategy:            request.GetStrategy(),
			Transport:           request.GetTransport(),
			ActiveEntries:       uint32(len(request.GetTargetEntries())),
			DeviceWorkerTotalUs: 10,
		},
	}, nil
}

func (w *fakeWorker) Reconcile(
	context.Context,
	*backendv1.ReconcileRequest,
) (*backendv1.ReadEntriesResponse, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	return &backendv1.ReadEntriesResponse{
		Entries: protoPairs(w.entries), Generation: w.generation,
		CacheStatus: "READY",
	}, nil
}

func (w *fakeWorker) Recover(
	ctx context.Context,
	request *backendv1.RecoverRequest,
) (*backendv1.ApplyTransitionResponse, error) {
	w.mu.Lock()
	w.entries = modelPairs(request.GetDesiredEntries())
	w.generation++
	generation := w.generation
	w.mu.Unlock()
	return &backendv1.ApplyTransitionResponse{
		Success: true, Restored: true, Generation: generation,
		CacheStatus: "READY", WriteVerification: "SOFTWARE_READBACK",
		Timing: &backendv1.OperationTiming{
			Strategy: request.GetStrategy(), Transport: request.GetTransport(),
			WriteVerification: "SOFTWARE_READBACK",
		},
	}, nil
}

func TestAgentSerializesCommitsAndChecksRevision(t *testing.T) {
	worker := &fakeWorker{entries: make(map[model.Pair]struct{}), delay: 10 * time.Millisecond, generation: 1}
	client, stop := startWorker(t, worker)
	defer stop()
	inventory := inventory(t)
	initial, err := model.FromPermutation(inventory, []uint32{2, 1, 4, 3})
	if err != nil {
		t.Fatal(err)
	}
	ocsAgent, err := agent.New(
		t.Context(), inventory, initial, client, "test", "CACHED_SYNC",
		30*time.Second, time.Hour, "REAPPLY_DESIRED")
	if err != nil {
		t.Fatal(err)
	}

	permutations := [][]uint32{{3, 4, 1, 2}, {4, 3, 2, 1}}
	lease, err := ocsAgent.AcquireControl("test", 0)
	if err != nil {
		t.Fatal(err)
	}
	revision := ocsAgent.Snapshot().Revision
	results := make(chan error, len(permutations))
	var wg sync.WaitGroup
	for _, permutation := range permutations {
		wg.Go(func() {
			_, err := ocsAgent.ApplyPermutation(
				t.Context(), permutation, "DELTA", "SEQUENTIAL", 0,
				&revision, lease.LeaseToken)
			results <- err
		})
	}
	wg.Wait()
	close(results)
	successes := 0
	conflicts := 0
	for err := range results {
		if err == nil {
			successes++
		} else if apierr.As(err).Code == codes.Aborted {
			conflicts++
		} else {
			t.Fatal(err)
		}
	}
	if worker.maxFlight != 1 {
		t.Fatalf("max concurrent worker calls = %d, want 1", worker.maxFlight)
	}
	if successes != 1 || conflicts != 1 {
		t.Fatalf("successes=%d conflicts=%d, want 1/1", successes, conflicts)
	}
	if ocsAgent.Snapshot().Revision != 1 {
		t.Fatalf("revision = %d, want 1", ocsAgent.Snapshot().Revision)
	}
	stale := uint64(0)
	_, err = ocsAgent.ApplyPermutation(
		t.Context(), []uint32{4, 3, 2, 1}, "DELTA", "SEQUENTIAL", 0,
		&stale, lease.LeaseToken)
	if apierr.As(err).Code != codes.Aborted {
		t.Fatalf("error = %v, want ABORTED", err)
	}
}

func TestRequireMatchNeedsExplicitRecovery(t *testing.T) {
	worker := &fakeWorker{
		entries: map[model.Pair]struct{}{
			{Ingress: 1, Egress: 3}: {},
			{Ingress: 3, Egress: 1}: {},
		},
		generation: 1,
	}
	client, stop := startWorker(t, worker)
	defer stop()
	inventory := inventory(t)
	initial, err := model.FromPermutation(inventory, []uint32{2, 1, 4, 3})
	if err != nil {
		t.Fatal(err)
	}
	ocsAgent, err := agent.New(
		t.Context(), inventory, initial, client, "test", "CACHED_ACK",
		30*time.Second, time.Hour, "REQUIRE_MATCH")
	if err != nil {
		t.Fatal(err)
	}
	if snapshot := ocsAgent.Snapshot(); snapshot.Status != "error" {
		t.Fatalf("startup status = %q, want error", snapshot.Status)
	}
	lease, err := ocsAgent.AcquireControl("startup-test", 0)
	if err != nil {
		t.Fatal(err)
	}
	revision := uint64(0)
	_, err = ocsAgent.ApplyPermutation(
		t.Context(), []uint32{4, 3, 2, 1}, "DELTA", "SEQUENTIAL", 0,
		&revision, lease.LeaseToken)
	if apierr.As(err).Code != codes.FailedPrecondition {
		t.Fatalf("apply error = %v, want FAILED_PRECONDITION", err)
	}
	if _, err := ocsAgent.ReconcileDeviceState(t.Context()); err != nil {
		t.Fatal(err)
	}
	if snapshot := ocsAgent.Snapshot(); snapshot.Status != "error" {
		t.Fatalf("post-reconcile status = %q, want error", snapshot.Status)
	}
	result, err := ocsAgent.RecoverDeviceState(
		t.Context(), &revision, lease.LeaseToken)
	if err != nil {
		t.Fatal(err)
	}
	if result.Result != "recovered" || ocsAgent.Snapshot().Status != "ready" {
		t.Fatalf("recovery result = %+v snapshot = %+v", result, ocsAgent.Snapshot())
	}
}

func startWorker(t *testing.T, worker *fakeWorker) (*backend.Client, func()) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	server := grpc.NewServer()
	backendv1.RegisterDeviceBackendServer(server, worker)
	go func() {
		_ = server.Serve(listener)
	}()
	client, err := backend.New(t.Context(), listener.Addr().String(), time.Second)
	if err != nil {
		server.Stop()
		listener.Close()
		t.Fatal(err)
	}
	return client, func() {
		client.Close()
		server.Stop()
		listener.Close()
	}
}

func inventory(t *testing.T) model.Inventory {
	t.Helper()
	value, err := model.NewInventory([]model.Port{
		{Name: "port-1", Index: 1}, {Name: "port-2", Index: 2},
		{Name: "port-3", Index: 3}, {Name: "port-4", Index: 4},
	})
	if err != nil {
		t.Fatal(err)
	}
	return value
}

func modelPairs(values []*backendv1.PortPair) map[model.Pair]struct{} {
	result := make(map[model.Pair]struct{}, len(values))
	for _, value := range values {
		result[model.Pair{Ingress: value.GetIngressPort(), Egress: value.GetEgressPort()}] = struct{}{}
	}
	return result
}

func protoPairs(values map[model.Pair]struct{}) []*backendv1.PortPair {
	result := make([]*backendv1.PortPair, 0, len(values))
	for value := range values {
		result = append(result, &backendv1.PortPair{
			IngressPort: value.Ingress, EgressPort: value.Egress,
		})
	}
	return result
}
