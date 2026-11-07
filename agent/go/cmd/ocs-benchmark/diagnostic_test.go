package main

import (
	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/proto"
	"testing"
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
