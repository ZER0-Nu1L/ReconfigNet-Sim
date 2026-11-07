package server

import (
	"context"
	"strconv"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/stats"
)

// applyDiagnostic is opt-in and reports intervals, never cross-host timestamps.
// The handler interval excludes protobuf decoding, encoding and trailer setup.
// All work through synchronous device completion remains inside the Core call.
type applyDiagnostic struct {
	admitted    time.Time
	decoded     time.Time
	ended       time.Time
	ctx         context.Context
	started     time.Time
	coreStarted time.Time
	coreEnded   time.Time
}

type diagnosticKey struct{}

// DiagnosticStats observes only explicitly marked ApplyBatch requests.
// Input includes payload wait/decode after admission; output includes protobuf
// encode and queuing to the HTTP/2 transport, not remote receipt.
type DiagnosticStats struct{}

func (DiagnosticStats) TagRPC(ctx context.Context, info *stats.RPCTagInfo) context.Context {
	md, _ := metadata.FromIncomingContext(ctx)
	if info.FullMethodName != "/reconfig_net.ocs.v1.OcsOperations/ApplyBatch" || len(md.Get("x-ocs-diagnostic")) == 0 || md.Get("x-ocs-diagnostic")[0] != "true" {
		return ctx
	}
	return context.WithValue(ctx, diagnosticKey{}, &applyDiagnostic{admitted: time.Now()})
}
func (DiagnosticStats) HandleRPC(ctx context.Context, event stats.RPCStats) {
	d, _ := ctx.Value(diagnosticKey{}).(*applyDiagnostic)
	if d == nil {
		return
	}
	switch event.(type) {
	case *stats.InPayload:
		d.decoded = time.Now()
	case *stats.OutPayload:
		if d.ended.IsZero() || d.ctx == nil {
			return
		}
		ended := time.Now()
		_ = grpc.SetTrailer(d.ctx, metadata.Pairs(
			"x-ocs-input-ns", strconv.FormatInt(d.decoded.Sub(d.admitted).Nanoseconds(), 10),
			"x-ocs-dispatch-ns", strconv.FormatInt(d.started.Sub(d.decoded).Nanoseconds(), 10),
			"x-ocs-output-ns", strconv.FormatInt(ended.Sub(d.ended).Nanoseconds(), 10),
		))
	}
}
func (DiagnosticStats) TagConn(ctx context.Context, _ *stats.ConnTagInfo) context.Context { return ctx }
func (DiagnosticStats) HandleConn(context.Context, stats.ConnStats)                       {}

func beginApplyDiagnostic(ctx context.Context) *applyDiagnostic {
	if d, _ := ctx.Value(diagnosticKey{}).(*applyDiagnostic); d != nil {
		d.ctx, d.started = ctx, time.Now()
		return d
	}
	md, _ := metadata.FromIncomingContext(ctx)
	if values := md.Get("x-ocs-diagnostic"); len(values) == 0 || values[0] != "true" {
		return nil
	}
	return &applyDiagnostic{started: time.Now()}
}

func (d *applyDiagnostic) beginCore() {
	if d != nil {
		d.coreStarted = time.Now()
	}
}

func (d *applyDiagnostic) endCore() {
	if d != nil {
		d.coreEnded = time.Now()
	}
}

func (d *applyDiagnostic) finish(ctx context.Context) {
	if d == nil || d.coreEnded.IsZero() {
		return
	}
	ended := time.Now()
	d.ended = ended
	// SetTrailer does not flush headers or acknowledge before completion.
	// Framed has no diagnostic metadata transport; this is gRPC-only.
	_ = grpc.SetTrailer(ctx, metadata.Pairs(
		"x-ocs-handler-ns", strconv.FormatInt(ended.Sub(d.started).Nanoseconds(), 10),
		"x-ocs-core-call-ns", strconv.FormatInt(d.coreEnded.Sub(d.coreStarted).Nanoseconds(), 10),
		"x-ocs-pre-core-ns", strconv.FormatInt(d.coreStarted.Sub(d.started).Nanoseconds(), 10),
		"x-ocs-reply-ns", strconv.FormatInt(ended.Sub(d.coreEnded).Nanoseconds(), 10),
	))
}
