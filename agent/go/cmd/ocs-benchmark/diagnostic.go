package main

import (
	"fmt"
	"strconv"
	"time"

	"google.golang.org/grpc/encoding"
	"google.golang.org/grpc/mem"
	"google.golang.org/grpc/metadata"
)

type northboundDiagnostic struct {
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
