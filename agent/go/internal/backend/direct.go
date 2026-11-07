package backend

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"maps"
	"os"
	"slices"
	"sync"
	"time"

	"github.com/reconfig-net-sim/ocs-go-agent/internal/bfrtclient"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/model"
	"golang.org/x/sys/unix"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// PairDevice separates BFRT I/O from state management for deterministic tests.
type PairDevice interface {
	ReadPairs(context.Context) ([][2]int, error)
	WriteAtomic(context.Context, [][2]int, [][2]int) error
	Close() error
}

// DirectOptions contains deployment settings, without experimental environment overrides.
type DirectOptions struct {
	BFRT        bfrtclient.Config
	LockFile    string
	Consistency string
	TwoPhase    bool
}

// Direct serializes access to the same pair table as the Python backend.
// A successful Apply waits for BFRT; it never queues an early acknowledgement.
type Direct struct {
	mu          sync.Mutex
	client      PairDevice
	timeout     time.Duration
	consistency string
	twoPhase    bool
	entries     map[model.Pair]struct{}
	state       DeviceState
	lock        *os.File
	closed      bool
}

func NewDirect(ctx context.Context, options DirectOptions) (*Direct, error) {
	lock, err := os.OpenFile(options.LockFile, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	if err = unix.Flock(int(lock.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		lock.Close()
		return nil, fmt.Errorf("another Agent owns the device lock: %w", err)
	}
	client, err := bfrtclient.Dial(ctx, options.BFRT)
	if err != nil {
		lock.Close()
		return nil, err
	}
	d := &Direct{client: client, timeout: options.BFRT.Timeout, consistency: options.Consistency, twoPhase: options.TwoPhase, lock: lock, state: DeviceState{CacheStatus: "UNKNOWN", WriteVerification: "ACK", ReadbackSource: "BFRT_HARDWARE"}}
	if _, err := d.Read(ctx); err != nil {
		d.Close()
		return nil, err
	}
	return d, nil
}

func (d *Direct) Close() error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.closed {
		return nil
	}
	d.closed = true
	err := d.client.Close()
	if d.lock != nil {
		err = errors.Join(err, d.lock.Close())
	}
	return err
}

func (*Direct) Capabilities() Capabilities {
	return Capabilities{Backend: "bfrt", Readback: true, NativeBatch: true, DataplaneAtomic: false, Transports: []string{"SEQUENTIAL", "NATIVE_BATCH"}, WriteVerifications: []string{"ACK", "HARDWARE_READBACK"}, ReadbackSources: []string{"BFRT_HARDWARE"}}
}

func (d *Direct) DeviceState() DeviceState { d.mu.Lock(); defer d.mu.Unlock(); return d.state }

func (d *Direct) read(ctx context.Context) (map[model.Pair]struct{}, error) {
	if d.closed {
		return nil, fmt.Errorf("BFRT backend is closed")
	}
	ctx, cancel := context.WithTimeout(ctx, d.timeout)
	defer cancel()
	pairs, err := d.client.ReadPairs(ctx)
	if err != nil {
		d.state.CacheStatus = "UNKNOWN"
		return nil, err
	}
	result := make(map[model.Pair]struct{}, len(pairs))
	for _, p := range pairs {
		result[model.Pair{Ingress: uint32(p[0]), Egress: uint32(p[1])}] = struct{}{}
	}
	d.state.LastVerifiedUnixNS = uint64(time.Now().UnixNano())
	return result, nil
}

func (d *Direct) Read(ctx context.Context) (map[model.Pair]struct{}, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	observed, err := d.read(ctx)
	if err != nil {
		return nil, err
	}
	// A read must not silently clear an earlier drift or unknown outcome.
	if d.entries == nil {
		d.entries = maps.Clone(observed)
		d.state.CacheStatus = "READY"
	}
	return observed, nil
}

func pairDelta(previous, target map[model.Pair]struct{}, full bool) ([][2]int, [][2]int) {
	var deletes, inserts [][2]int
	if maps.Equal(previous, target) {
		return nil, nil
	}
	for p := range previous {
		if _, ok := target[p]; full || !ok {
			deletes = append(deletes, [2]int{int(p.Ingress), int(p.Egress)})
		}
	}
	for p := range target {
		if _, ok := previous[p]; full || !ok {
			inserts = append(inserts, [2]int{int(p.Ingress), int(p.Egress)})
		}
	}
	order := func(a, b [2]int) int { return cmp.Or(cmp.Compare(a[0], b[0]), cmp.Compare(a[1], b[1])) }
	slices.SortFunc(deletes, order)
	slices.SortFunc(inserts, order)
	return deletes, inserts
}

func (d *Direct) write(ctx context.Context, previous, target map[model.Pair]struct{}, strategy, transport string, gap uint64, timing *Timing) error {
	started := time.Now()
	deletes, inserts := pairDelta(previous, target, strategy == "FULL")
	timing.PlanningUS = uint64(time.Since(started).Microseconds())
	timing.DeleteEntries = uint32(len(deletes))
	timing.InsertEntries = uint32(len(inserts))
	timing.ActiveEntries = uint32(len(target))
	timing.UnchangedEntries = uint32(len(target) - len(inserts))
	timing.RequestedGapUS = gap
	ctx, cancel := context.WithTimeout(ctx, d.timeout)
	defer cancel()
	call := func(del, ins [][2]int, install bool) error {
		if len(del)+len(ins) == 0 {
			return nil
		}
		start := time.Now()
		timing.DeviceWriteRequests++
		err := d.client.WriteAtomic(ctx, del, ins)
		elapsed := uint64(time.Since(start).Microseconds())
		timing.ProgrammingTotalUS += elapsed
		if install {
			timing.InstallCommitUS += elapsed
		} else {
			timing.DeleteCommitUS += elapsed
		}
		return err
	}
	if transport == "NATIVE_BATCH" && !d.twoPhase && gap == 0 {
		return call(deletes, inserts, true)
	}
	if transport == "SEQUENTIAL" {
		for _, p := range deletes {
			if err := call([][2]int{p}, nil, false); err != nil {
				return err
			}
		}
	} else if err := call(deletes, nil, false); err != nil {
		return err
	}
	if gap > 0 {
		start := time.Now()
		timer := time.NewTimer(time.Duration(gap) * time.Microsecond)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-timer.C:
		}
		timing.ActualGapUS = uint64(time.Since(start).Microseconds())
		timing.ProgrammingTotalUS += timing.ActualGapUS
	}
	if transport == "SEQUENTIAL" {
		for _, p := range inserts {
			if err := call(nil, [][2]int{p}, true); err != nil {
				return err
			}
		}
		return nil
	}
	return call(nil, inserts, true)
}

func (d *Direct) Apply(ctx context.Context, previous, target map[model.Pair]struct{}, strategy, transport string, gap uint64) (Timing, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	t := Timing{Strategy: strategy, Transport: transport}
	if d.closed || d.state.CacheStatus != "READY" || !maps.Equal(previous, d.entries) {
		return t, &TransitionError{Message: "device state must be reconciled or recovered", Code: "FAILED_PRECONDITION", Timing: t}
	}
	if d.consistency == "STRICT_DEVICE" {
		start := time.Now()
		observed, err := d.read(ctx)
		t.PreconditionReadbackUS = uint64(time.Since(start).Microseconds())
		if err != nil {
			return t, err
		}
		if !maps.Equal(observed, previous) {
			d.state.CacheStatus = "DRIFTED"
			d.state.DriftCount++
			return t, &TransitionError{Message: "device has drifted", Code: "FAILED_PRECONDITION", Timing: t}
		}
	}
	if err := d.write(ctx, previous, target, strategy, transport, gap, &t); err != nil {
		return d.failedTransition(ctx, previous, t, err)
	}
	t.WriteVerification = "ACK"
	t.ReadbackSource = "BFRT_HARDWARE"
	if d.consistency != "CACHED_ACK" {
		start := time.Now()
		observed, err := d.read(ctx)
		t.ReadbackUS = uint64(time.Since(start).Microseconds())
		t.ProgrammingTotalUS += t.ReadbackUS
		if err != nil {
			return t, err
		}
		if !maps.Equal(observed, target) {
			return d.failedTransition(ctx, previous, t, fmt.Errorf("write readback mismatch"))
		}
		t.WriteVerification = "HARDWARE_READBACK"
	}
	d.entries = maps.Clone(target)
	d.state.Generation++
	d.state.CacheStatus = "READY"
	d.state.WriteVerification = t.WriteVerification
	d.state.LastWriteAckUnixNS = uint64(time.Now().UnixNano())
	return t, nil
}

// Roll back only after a determinate rejection. A lost or timed-out write may
// still be executing and must be resolved by explicit state inspection/recovery.
func (d *Direct) failedTransition(ctx context.Context, previous map[model.Pair]struct{}, t Timing, cause error) (Timing, error) {
	d.state.CacheStatus = "UNKNOWN"
	failure := &TransitionError{Message: cause.Error(), Code: "INTERNAL", Timing: t}
	code := status.Code(cause)
	if ctx.Err() != nil || errors.Is(cause, context.Canceled) || errors.Is(cause, context.DeadlineExceeded) || code == codes.Canceled || code == codes.DeadlineExceeded || code == codes.Unavailable {
		return t, failure
	}
	if code != codes.InvalidArgument && code != codes.NotFound && code != codes.AlreadyExists && code != codes.PermissionDenied && code != codes.Unauthenticated && code != codes.FailedPrecondition && code != codes.OutOfRange && !bfrtclient.IsDuplicate(cause) {
		// Generic INTERNAL/UNKNOWN errors do not establish that the server
		// rejected the write before completing it. Never repair on that guess.
		return t, failure
	}
	start := time.Now()
	recovery, cancel := context.WithTimeout(context.WithoutCancel(ctx), d.timeout)
	defer cancel()
	observed, err := d.read(recovery)
	if err == nil && !maps.Equal(observed, previous) {
		discarded := Timing{}
		err = d.write(recovery, observed, previous, "DELTA", "NATIVE_BATCH", 0, &discarded)
	}
	if err == nil {
		observed, err = d.read(recovery)
		if err == nil && !maps.Equal(observed, previous) {
			err = fmt.Errorf("rollback readback mismatch")
		}
	}
	t.RollbackUS = uint64(time.Since(start).Microseconds())
	t.ProgrammingTotalUS += t.RollbackUS
	if err == nil {
		d.entries = maps.Clone(previous)
		d.state.CacheStatus = "READY"
		failure.Restored = true
	} else {
		d.state.CacheStatus = "UNKNOWN"
		failure.RollbackError = err.Error()
	}
	failure.Timing = t
	return t, failure
}

func (d *Direct) Reconcile(ctx context.Context, desired map[model.Pair]struct{}) (DeviceState, map[model.Pair]struct{}, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	observed, err := d.read(ctx)
	d.state.LastReconcileUnixNS = uint64(time.Now().UnixNano())
	if err == nil && (!maps.Equal(observed, desired) || !maps.Equal(observed, d.entries)) {
		d.state.CacheStatus = "DRIFTED"
		d.state.DriftCount++
	}
	return d.state, observed, err
}

func (d *Direct) Recover(ctx context.Context, desired map[model.Pair]struct{}, strategy, transport string, gap uint64) (Timing, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	t := Timing{Strategy: strategy, Transport: transport}
	observed, err := d.read(ctx)
	if err != nil {
		return t, err
	}
	if err = d.write(ctx, observed, desired, strategy, transport, gap, &t); err != nil {
		d.state.CacheStatus = "UNKNOWN"
		return t, err
	}
	start := time.Now()
	verified, err := d.read(ctx)
	t.ReadbackUS = uint64(time.Since(start).Microseconds())
	t.ProgrammingTotalUS += t.ReadbackUS
	if err != nil {
		return t, err
	}
	if !maps.Equal(verified, desired) {
		d.state.CacheStatus = "UNKNOWN"
		return t, fmt.Errorf("recovery readback does not match desired state")
	}
	d.entries = maps.Clone(desired)
	d.state.CacheStatus = "READY"
	d.state.Generation++
	d.state.WriteVerification = "HARDWARE_READBACK"
	d.state.LastWriteAckUnixNS = uint64(time.Now().UnixNano())
	t.WriteVerification = d.state.WriteVerification
	t.ReadbackSource = d.state.ReadbackSource
	return t, nil
}
