// Package qualification exercises paired real updates over one owned connection.
// Any failed or unexpected acknowledgement permanently stops this session. It
// never retries, reconnects, switches protocol, or restores an uncertain write.
package qualification

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"time"

	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"google.golang.org/grpc/metadata"
)

type Event struct {
	Kind           string                 `json:"kind"`
	At             time.Time              `json:"at"`
	ElapsedSeconds float64                `json:"elapsed_seconds"`
	Revision       uint64                 `json:"revision"`
	Pair           uint64                 `json:"pair"`
	RequestID      uint64                 `json:"request_id,omitzero"`
	LatencyUS      int64                  `json:"latency_us,omitzero"`
	Timing         *ocsv1.OperationTiming `json:"timing,omitempty"`
	Error          string                 `json:"error,omitempty"`
}

type Options struct {
	Initial, Alternate                         []uint32
	Duration, Interval, WriteInterval, Timeout time.Duration
	Compact                                    bool
}

func Validate(o Options) error {
	if o.Duration <= 0 || o.Interval <= 0 || o.WriteInterval <= 0 || o.Timeout <= 0 || o.Interval > 5*time.Second {
		return fmt.Errorf("positive durations required, sample interval must be <=5s")
	}
	if len(o.Initial) != len(o.Alternate) || slices.Equal(o.Initial, o.Alternate) {
		return fmt.Errorf("initial and alternate must be different permutations of the same size")
	}
	for _, pi := range [][]uint32{o.Initial, o.Alternate} {
		if len(pi) == 0 || len(pi)%2 != 0 {
			return fmt.Errorf("even nonempty permutations required")
		}
		for i, peer := range pi {
			if peer == 0 || int(peer) > len(pi) || peer == uint32(i+1) || pi[peer-1] != uint32(i+1) {
				return fmt.Errorf("permutation must be symmetric with no fixed point")
			}
		}
	}
	return nil
}

type Session struct {
	api      ocsv1.OcsOperationsClient
	options  Options
	token    string
	revision uint64
	pairs    uint64
	stopped  error
}

func New(api ocsv1.OcsOperationsClient, o Options) (*Session, error) {
	if err := Validate(o); err != nil {
		return nil, err
	}
	return &Session{api: api, options: o}, nil
}

func (s *Session) fail(err error) error {
	if err != nil && s.stopped == nil {
		s.stopped = err
	}
	return err
}

func (s *Session) Acquire(ctx context.Context) error {
	if s.stopped != nil {
		return s.stopped
	}
	lease, err := s.api.AcquireControl(ctx, &ocsv1.AcquireControlRequest{ClientId: "ocs-qualify", RequestedLeaseSeconds: 30})
	if err != nil {
		return s.fail(err)
	}
	s.token, s.revision = lease.GetLeaseToken(), lease.GetRevision()
	if s.token == "" {
		return s.fail(fmt.Errorf("empty lease token"))
	}
	return s.Check(ctx)
}

func (s *Session) Renew(ctx context.Context) error {
	if s.stopped != nil {
		return s.stopped
	}
	lease, err := s.api.RenewControl(ctx, &ocsv1.RenewControlRequest{LeaseToken: s.token, RequestedLeaseSeconds: 30})
	if err != nil {
		return s.fail(err)
	}
	if lease.GetLeaseToken() != s.token || lease.GetRevision() != s.revision {
		return s.fail(fmt.Errorf("lease identity or revision changed"))
	}
	return nil
}

func checkState(state *ocsv1.RuntimeState, revision uint64) error {
	if state.GetRevision() != revision || state.GetMode() != ocsv1.Mode_MODE_OCS || state.GetStatus() != ocsv1.RuntimeStatus_RUNTIME_STATUS_READY || state.GetDeviceState().GetCacheStatus() != "READY" || state.GetDeviceState().GetDriftCount() != 0 {
		return fmt.Errorf("unexpected revision or device is not READY without drift: %v", state)
	}
	return nil
}

func (s *Session) Check(ctx context.Context) error {
	if s.stopped != nil {
		return s.stopped
	}
	reply, err := s.api.GetPermutation(ctx, &ocsv1.Empty{})
	if err != nil {
		return s.fail(err)
	}
	if err := checkState(reply.GetState(), s.revision); err != nil {
		return s.fail(err)
	}
	if !slices.Equal(reply.GetPermutation().GetPi(), s.options.Initial) {
		return s.fail(fmt.Errorf("initial mapping mismatch"))
	}
	return nil
}

// Pair restores only after a validated acknowledgement of the alternate write.
// Failure to durably record a known success also stops the session.
func (s *Session) Pair(ctx context.Context, emit func(Event) error) error {
	if s.stopped != nil {
		return s.stopped
	}
	for i, pi := range [][]uint32{s.options.Alternate, s.options.Initial} {
		callCtx, cancel := context.WithTimeout(ctx, s.options.Timeout)
		callCtx = metadata.AppendToOutgoingContext(callCtx, "x-ocs-control-lease", s.token)
		if s.options.Compact {
			callCtx = metadata.AppendToOutgoingContext(callCtx, "x-ocs-compact-reply", "true")
		}
		started := time.Now()
		reply, err := s.api.ApplyBatch(callCtx, &ocsv1.ApplyBatchRequest{
			Intent:   &ocsv1.ApplyBatchRequest_Permutation{Permutation: &ocsv1.Permutation{Pi: pi}},
			Strategy: ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_DELTA, Transport: ocsv1.Transport_TRANSPORT_NATIVE_BATCH,
			HasExpectedRevision: true, ExpectedRevision: s.revision,
		})
		cancel()
		if err != nil {
			return s.fail(fmt.Errorf("write outcome requires inspection; no replay or restore: %w", err))
		}
		if err := checkState(reply.GetState(), s.revision+1); err != nil {
			return s.fail(err)
		}
		if reply.GetResult() != "updated" || reply.GetTiming().GetDeviceWriteRequests() != 1 {
			return s.fail(fmt.Errorf("expected one acknowledged real mixed write: %v", reply))
		}
		s.revision++
		kind := "apply"
		if i == 1 {
			kind = "restore"
		}
		if err := emit(Event{Kind: kind, Revision: s.revision, Pair: s.pairs + 1, RequestID: reply.GetRequestId(), LatencyUS: time.Since(started).Microseconds(), Timing: reply.GetTiming()}); err != nil {
			return s.fail(err)
		}
	}
	s.pairs++
	checkCtx, cancel := context.WithTimeout(ctx, s.options.Timeout)
	defer cancel()
	return s.Check(checkCtx)
}

// Run returns only after the requested duration and a final matching read.
// A complete short run is useful for smoke testing; it is not a 24h certificate.
func (s *Session) Run(ctx context.Context, emit func(Event) error) (err error) {
	started := time.Now()
	record := func(e Event) error {
		e.At, e.ElapsedSeconds = time.Now().UTC(), time.Since(started).Seconds()
		return emit(e)
	}
	defer func() {
		if s.token != "" {
			cleanup, cancel := context.WithTimeout(context.Background(), s.options.Timeout)
			defer cancel()
			_, releaseErr := s.api.ReleaseControl(cleanup, &ocsv1.ReleaseControlRequest{LeaseToken: s.token})
			if releaseErr != nil {
				err = errors.Join(err, fmt.Errorf("release lease: %w", releaseErr))
			}
		}
		kind, message := "complete", ""
		if err != nil {
			kind, message = "failed", err.Error()
		}
		err = errors.Join(err, record(Event{Kind: kind, Revision: s.revision, Pair: s.pairs, Error: message}))
	}()
	call := func(f func(context.Context) error) error {
		callCtx, cancel := context.WithTimeout(ctx, s.options.Timeout)
		defer cancel()
		return f(callCtx)
	}
	if err = call(s.Acquire); err != nil {
		return err
	}
	if err = record(Event{Kind: "start", Revision: s.revision}); err != nil {
		return err
	}
	nextWrite := started
	lastTick := started
	for {
		if time.Since(lastTick) > s.options.Interval+2*s.options.Timeout {
			return fmt.Errorf("sampling continuity lost")
		}
		if err = call(s.Renew); err != nil {
			return err
		}
		if err = call(s.Check); err != nil {
			return err
		}
		if !time.Now().Before(nextWrite) {
			if err = s.Pair(ctx, record); err != nil {
				return err
			}
			nextWrite = time.Now().Add(s.options.WriteInterval)
		}
		if err = record(Event{Kind: "sample", Revision: s.revision, Pair: s.pairs}); err != nil {
			return err
		}
		lastTick = time.Now()
		if time.Since(started) >= s.options.Duration {
			return nil
		}
		timer := time.NewTimer(min(s.options.Interval, s.options.Duration-time.Since(started)))
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}
