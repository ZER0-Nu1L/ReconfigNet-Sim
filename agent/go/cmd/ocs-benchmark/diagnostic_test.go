package main

import (
	"context"
	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/proto"
	"net"
	"strconv"
	"sync/atomic"
	"testing"
	"time"
)

func TestDiagnosticCodecKeepsProtobufContract(t *testing.T) {
	d := &northboundDiagnostic{}
	c := newTimedCodec(d)
	original := &ocsv1.ApplyBatchRequest{HasExpectedRevision: true, ExpectedRevision: 42}
	data, err := c.Marshal(original)
	if err != nil {
		t.Fatal(err)
	}
	defer data.Free()
	var decoded ocsv1.ApplyBatchRequest
	if err := c.Unmarshal(data, &decoded); err != nil {
		t.Fatal(err)
	}
	if !proto.Equal(original, &decoded) || d.MarshalNS == 0 || d.UnmarshalNS == 0 {
		t.Fatalf("changed wire contract or no timings: %v, %+v", &decoded, d)
	}
}

func TestDiagnosticRequiresCompleteUnsignedIntervals(t *testing.T) {
	for _, md := range []metadata.MD{
		{},
		metadata.Pairs("x-ocs-handler-ns", "-1"),
		metadata.Pairs("x-ocs-handler-ns", "1", "x-ocs-handler-ns", "2"),
	} {
		if err := (&northboundDiagnostic{}).readTrailer(md); err == nil {
			t.Fatal("accepted incomplete or malformed timings")
		}
	}
	d := &northboundDiagnostic{}
	if err := d.readTrailer(metadata.Pairs("x-ocs-handler-ns", "12", "x-ocs-core-call-ns", "9", "x-ocs-pre-core-ns", "1", "x-ocs-reply-ns", "2")); err != nil {
		t.Fatal(err)
	}
	if d.HandlerNS != d.CoreCallNS+d.PreCoreNS+d.ReplyNS {
		t.Fatal("lost interval values")
	}
}

type diagnosticOperations struct {
	ocsv1.UnimplementedOcsOperationsServer
	calls    atomic.Int32
	trailers bool
}

func (s *diagnosticOperations) ApplyBatch(ctx context.Context, _ *ocsv1.ApplyBatchRequest) (*ocsv1.OperationReply, error) {
	s.calls.Add(1)
	if s.trailers {
		grpc.SetTrailer(ctx, metadata.Pairs("x-ocs-handler-ns", "12", "x-ocs-core-call-ns", "9", "x-ocs-pre-core-ns", "1", "x-ocs-reply-ns", "2"))
	}
	return &ocsv1.OperationReply{State: &ocsv1.RuntimeState{Revision: 1}, Result: "updated"}, nil
}

func TestDiagnosticUnaryEventsAndFailureStop(t *testing.T) {
	for _, trailers := range []bool{false, true} {
		t.Run(strconv.FormatBool(trailers), func(t *testing.T) {
			listener, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			service := &diagnosticOperations{trailers: trailers}
			gs := grpc.NewServer()
			ocsv1.RegisterOcsOperationsServer(gs, service)
			go gs.Serve(listener)
			defer gs.Stop()
			c, err := newConfiguredGRPCClient(listener.Addr().String(), -1, true)
			if err != nil {
				t.Fatal(err)
			}
			defer c.Close()
			c.diagnostic = true
			ctx, cancel := context.WithTimeout(t.Context(), 2*time.Second)
			defer cancel()
			sample, err := c.Apply(ctx, []uint32{2, 1}, "DELTA", "NATIVE_BATCH")
			if !trailers {
				if err == nil || c.revision != 1 {
					t.Fatal("successful commit was not retained before rejecting incomplete diagnostics", err)
				}
				if _, err := c.Apply(ctx, []uint32{2, 1}, "DELTA", "NATIVE_BATCH"); err == nil || service.calls.Load() != 1 {
					t.Fatal("write replayed after diagnostic failure", err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			d := sample.Diagnostic
			if d == nil || d.SendNS == 0 || d.ReceiveNS < d.SendNS || d.RPCNS < d.ReceiveNS || d.MarshalNS == 0 || d.UnmarshalNS == 0 {
				t.Fatalf("incomplete client markers: %+v", d)
			}
		})
	}
}
