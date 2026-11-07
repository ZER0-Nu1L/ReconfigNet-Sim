package backend

import (
	"context"
	"errors"
	"maps"
	"testing"
	"time"

	"github.com/reconfig-net-sim/ocs-go-agent/internal/model"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type pairFake struct {
	pairs    map[model.Pair]struct{}
	writes   int
	fail     error
	entered  chan struct{}
	release  chan struct{}
	mismatch bool
}

func (f *pairFake) Close() error { return nil }
func (f *pairFake) ReadPairs(context.Context) ([][2]int, error) {
	var result [][2]int
	for p := range f.pairs {
		result = append(result, [2]int{int(p.Ingress), int(p.Egress)})
	}
	return result, nil
}
func (f *pairFake) WriteAtomic(ctx context.Context, del, ins [][2]int) error {
	f.writes++
	if f.entered != nil {
		close(f.entered)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-f.release:
		}
	}
	if f.fail != nil {
		return f.fail
	}
	if f.mismatch {
		return nil
	}
	for _, p := range del {
		delete(f.pairs, model.Pair{Ingress: uint32(p[0]), Egress: uint32(p[1])})
	}
	for _, p := range ins {
		f.pairs[model.Pair{Ingress: uint32(p[0]), Egress: uint32(p[1])}] = struct{}{}
	}
	return nil
}
func oldPairs() map[model.Pair]struct{} {
	return map[model.Pair]struct{}{{Ingress: 1, Egress: 2}: {}, {Ingress: 2, Egress: 1}: {}}
}
func newPairs() map[model.Pair]struct{} {
	return map[model.Pair]struct{}{{Ingress: 1, Egress: 3}: {}, {Ingress: 3, Egress: 1}: {}}
}
func directForTest(f *pairFake, mode string) *Direct {
	return &Direct{client: f, timeout: time.Second, consistency: mode, entries: maps.Clone(f.pairs), state: DeviceState{CacheStatus: "READY", ReadbackSource: "BFRT_HARDWARE"}}
}

func TestDirectAcknowledgesAfterWriteAndCountsActualRequests(t *testing.T) {
	for _, two := range []bool{false, true} {
		t.Run(map[bool]string{false: "single", true: "two_phase"}[two], func(t *testing.T) {
			f := &pairFake{pairs: oldPairs(), entered: make(chan struct{}), release: make(chan struct{})}
			d := directForTest(f, "CACHED_ACK")
			d.twoPhase = two
			type outcome struct {
				timing Timing
				err    error
			}
			done := make(chan outcome, 1)
			go func() {
				tt, err := d.Apply(t.Context(), oldPairs(), newPairs(), "DELTA", "NATIVE_BATCH", 0)
				done <- outcome{tt, err}
			}()
			<-f.entered
			select {
			case <-done:
				t.Fatal("acknowledged before BFRT completion")
			default:
			}
			// Only the first call blocks; a two-phase transition may issue a second.
			f.entered = nil
			close(f.release)
			result := <-done
			if result.err != nil {
				t.Fatal(result.err)
			}
			want := uint32(1)
			if two {
				want = 2
			}
			if result.timing.DeviceWriteRequests != want || uint32(f.writes) != want {
				t.Fatalf("requests: %v / %d", result.timing, f.writes)
			}
			if !maps.Equal(f.pairs, newPairs()) {
				t.Fatal("not applied")
			}
			if d.Capabilities().DataplaneAtomic {
				t.Fatal("rollback does not guarantee dataplane atomicity")
			}
		})
	}
}

func TestDirectUnknownOutcomeBlocksWritesUntilExplicitRecovery(t *testing.T) {
	f := &pairFake{pairs: oldPairs(), fail: context.DeadlineExceeded}
	d := directForTest(f, "CACHED_ACK")
	if _, err := d.Apply(t.Context(), oldPairs(), newPairs(), "DELTA", "NATIVE_BATCH", 0); err == nil {
		t.Fatal("expected failure")
	}
	if d.DeviceState().CacheStatus != "UNKNOWN" {
		t.Fatal("lost outcome treated as ready")
	}
	writes := f.writes
	if _, err := d.Apply(t.Context(), oldPairs(), newPairs(), "DELTA", "NATIVE_BATCH", 0); err == nil || f.writes != writes {
		t.Fatal("wrote after unknown outcome")
	}
	f.fail = nil
	if _, _, err := d.Reconcile(t.Context(), oldPairs()); err != nil {
		t.Fatal(err)
	}
	if d.DeviceState().CacheStatus != "UNKNOWN" {
		t.Fatal("background read cleared unknown outcome")
	}
	if _, err := d.Recover(t.Context(), oldPairs(), "FULL", "NATIVE_BATCH", 0); err != nil {
		t.Fatal(err)
	}
	if _, err := d.Apply(t.Context(), oldPairs(), newPairs(), "DELTA", "NATIVE_BATCH", 0); err != nil {
		t.Fatal(err)
	}
}

func TestDirectReadbackAndDriftChecks(t *testing.T) {
	f := &pairFake{pairs: oldPairs()}
	d := directForTest(f, "STRICT_DEVICE")
	f.pairs = newPairs()
	if _, err := d.Apply(t.Context(), oldPairs(), newPairs(), "DELTA", "NATIVE_BATCH", 0); err == nil || f.writes != 0 {
		t.Fatal("failed to reject drift before write")
	}
	if d.DeviceState().CacheStatus != "DRIFTED" {
		t.Fatal("drift not recorded")
	}
	f = &pairFake{pairs: oldPairs(), mismatch: true}
	d = directForTest(f, "CACHED_SYNC")
	if _, err := d.Apply(t.Context(), oldPairs(), newPairs(), "DELTA", "NATIVE_BATCH", 0); err == nil {
		t.Fatal("accepted mismatched readback")
	}
	if _, err := d.Recover(t.Context(), newPairs(), "FULL", "NATIVE_BATCH", 0); err == nil {
		t.Fatal("claimed recovery without verification")
	}
	if d.DeviceState().CacheStatus == "READY" {
		t.Fatal("mismatch treated as ready")
	}
}

func TestDirectNoOpAndSequential(t *testing.T) {
	f := &pairFake{pairs: oldPairs()}
	d := directForTest(f, "CACHED_ACK")
	tt, err := d.Apply(t.Context(), oldPairs(), oldPairs(), "DELTA", "NATIVE_BATCH", 0)
	if err != nil || tt.DeviceWriteRequests != 0 || f.writes != 0 {
		t.Fatal(tt, err)
	}
	tt, err = d.Apply(t.Context(), oldPairs(), newPairs(), "FULL", "SEQUENTIAL", 0)
	if err != nil || tt.DeviceWriteRequests != 4 {
		t.Fatal(tt, err)
	}
	f.fail = status.Error(codes.InvalidArgument, "device rejected request")
	if _, err := d.Apply(t.Context(), newPairs(), oldPairs(), "FULL", "NATIVE_BATCH", 0); err == nil {
		t.Fatal("ignored error")
	} else {
		var failure *TransitionError
		if !errors.As(err, &failure) || !failure.Restored || d.DeviceState().CacheStatus != "READY" {
			t.Fatal("determinate failure did not verify previous state", err)
		}
	}
}

func TestDirectCancellationDoesNotAttemptRollback(t *testing.T) {
	f := &pairFake{pairs: oldPairs(), fail: context.Canceled}
	d := directForTest(f, "CACHED_ACK")
	if _, err := d.Apply(t.Context(), oldPairs(), newPairs(), "DELTA", "NATIVE_BATCH", 0); err == nil {
		t.Fatal("cancelled write was accepted")
	}
	if f.writes != 1 || d.DeviceState().CacheStatus != "UNKNOWN" {
		t.Fatal("cancelled operation was replayed or treated as complete", f.writes, d.DeviceState())
	}
	if _, err := d.Read(t.Context()); err != nil {
		t.Fatal(err)
	}
	if d.DeviceState().CacheStatus != "UNKNOWN" {
		t.Fatal("read silently cleared an uncertain outcome")
	}
}

func TestDirectGenericFailureDoesNotAttemptRepair(t *testing.T) {
	for _, failure := range []error{errors.New("lost result"), status.Error(codes.Internal, "runtime failure")} {
		f := &pairFake{pairs: oldPairs(), fail: failure}
		d := directForTest(f, "CACHED_ACK")
		if _, err := d.Apply(t.Context(), oldPairs(), newPairs(), "DELTA", "NATIVE_BATCH", 0); err == nil {
			t.Fatal("uncertain write succeeded")
		}
		if f.writes != 1 || d.DeviceState().CacheStatus != "UNKNOWN" {
			t.Fatal("generic failure triggered recovery", f.writes, d.DeviceState())
		}
	}
}

func TestDirectClosedBackendCannotWriteOrRecover(t *testing.T) {
	f := &pairFake{pairs: oldPairs()}
	d := directForTest(f, "CACHED_ACK")
	if err := d.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := d.Apply(t.Context(), oldPairs(), newPairs(), "DELTA", "NATIVE_BATCH", 0); err == nil {
		t.Fatal("closed backend accepted apply")
	}
	if _, err := d.Recover(t.Context(), newPairs(), "DELTA", "NATIVE_BATCH", 0); err == nil {
		t.Fatal("closed backend accepted recovery")
	}
	if f.writes != 0 {
		t.Fatal("wrote after ownership was released")
	}
}
