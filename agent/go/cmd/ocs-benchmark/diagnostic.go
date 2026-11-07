package main

import (
	"context"
	"fmt"
	"google.golang.org/grpc/stats"
	"strconv"
	"sync/atomic"
	"time"

	"google.golang.org/grpc/encoding"
	"google.golang.org/grpc/mem"
	"google.golang.org/grpc/metadata"
)

type northboundDiagnostic struct {
	RPCNS       uint64 `json:"client_rpc_ns"`
	SendNS      uint64 `json:"client_send_elapsed_ns"`
	ReceiveNS   uint64 `json:"client_receive_elapsed_ns"`
	HeaderNS    uint64 `json:"client_header_elapsed_ns"`
	rpcStarted  time.Time
	sent        atomic.Int64
	received    atomic.Int64
	header      atomic.Int64
	InputNS     uint64 `json:"server_input_ns"`
	DispatchNS  uint64 `json:"server_dispatch_ns"`
	OutputNS    uint64 `json:"server_output_ns"`
	MarshalNS   uint64 `json:"client_marshal_ns"`
	UnmarshalNS uint64 `json:"client_unmarshal_ns"`
	HandlerNS   uint64 `json:"server_handler_ns"`
	CoreCallNS  uint64 `json:"server_core_call_ns"`
	PreCoreNS   uint64 `json:"server_pre_core_ns"`
	ReplyNS     uint64 `json:"server_reply_ns"`
}

func (d *northboundDiagnostic) readTrailer(md metadata.MD) error {
	for name, dst := range map[string]*uint64{
		"x-ocs-handler-ns": &d.HandlerNS, "x-ocs-core-call-ns": &d.CoreCallNS,
		"x-ocs-pre-core-ns": &d.PreCoreNS, "x-ocs-reply-ns": &d.ReplyNS,
	} {
		values := md.Get(name)
		if len(values) != 1 {
			return fmt.Errorf("successful write missing diagnostic trailer %s; inspect state before another write", name)
		}
		value, err := strconv.ParseUint(values[0], 10, 64)
		if err != nil {
			return fmt.Errorf("invalid diagnostic trailer %s: %w", name, err)
		}
		*dst = value
	}
	for name, dst := range map[string]*uint64{
		"x-ocs-input-ns": &d.InputNS, "x-ocs-dispatch-ns": &d.DispatchNS, "x-ocs-output-ns": &d.OutputNS,
	} {
		values := md.Get(name)
		if len(values) == 0 {
			continue
		}
		if len(values) != 1 {
			return fmt.Errorf("duplicate diagnostic trailer %s", name)
		}
		value, err := strconv.ParseUint(values[0], 10, 64)
		if err != nil {
			return fmt.Errorf("invalid diagnostic trailer %s: %w", name, err)
		}
		*dst = value
	}
	return nil
}

// Each codec instance belongs to one serialized unary call, with retries
// disabled. Delegate to the installed proto codec so buffer pooling is kept.
type timedCodec struct {
	encoding.CodecV2
	timing *northboundDiagnostic
}

func newTimedCodec(d *northboundDiagnostic) *timedCodec {
	return &timedCodec{CodecV2: encoding.GetCodecV2("proto"), timing: d}
}

func (c *timedCodec) Marshal(value any) (mem.BufferSlice, error) {
	started := time.Now()
	out, err := c.CodecV2.Marshal(value)
	c.timing.MarshalNS += uint64(time.Since(started).Nanoseconds())
	return out, err
}

func (c *timedCodec) Unmarshal(data mem.BufferSlice, value any) error {
	started := time.Now()
	err := c.CodecV2.Unmarshal(data, value)
	c.timing.UnmarshalNS += uint64(time.Since(started).Nanoseconds())
	return err
}

type clientDiagnosticKey struct{}
type clientDiagnosticStats struct{}

func (clientDiagnosticStats) TagRPC(ctx context.Context, _ *stats.RPCTagInfo) context.Context {
	return ctx
}
func (clientDiagnosticStats) HandleRPC(ctx context.Context, event stats.RPCStats) {
	d, _ := ctx.Value(clientDiagnosticKey{}).(*northboundDiagnostic)
	if d == nil {
		return
	}
	switch e := event.(type) {
	case *stats.OutPayload:
		d.sent.Store(e.SentTime.Sub(d.rpcStarted).Nanoseconds())
	case *stats.InPayload:
		d.received.Store(e.RecvTime.Sub(d.rpcStarted).Nanoseconds())
	case *stats.InHeader:
		d.header.Store(time.Since(d.rpcStarted).Nanoseconds())
	}
}
func (clientDiagnosticStats) TagConn(ctx context.Context, _ *stats.ConnTagInfo) context.Context {
	return ctx
}
func (clientDiagnosticStats) HandleConn(context.Context, stats.ConnStats) {}
func (d *northboundDiagnostic) finishClient(elapsed time.Duration) {
	d.RPCNS = uint64(elapsed.Nanoseconds())
	d.SendNS = uint64(d.sent.Load())
	d.ReceiveNS = uint64(d.received.Load())
	d.HeaderNS = uint64(d.header.Load())
}
