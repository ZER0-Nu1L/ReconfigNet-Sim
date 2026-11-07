package framed

import (
	"context"
	"net"
	"testing"
	"time"

	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

func TestPersistentConnectionRefreshesCallDeadline(t *testing.T) {
	a, b := net.Pipe()
	c := &Client{conn: a}
	defer c.Close()
	go ServeConn(t.Context(), b, func(context.Context, string, []byte) (proto.Message, error) { return &ocsv1.Empty{}, nil }, time.Second)
	ctx, cancel := context.WithTimeout(t.Context(), 20*time.Millisecond)
	defer cancel()
	if err := c.Invoke(ctx, "test", &ocsv1.Empty{}, &ocsv1.Empty{}); err != nil {
		t.Fatal(err)
	}
	<-ctx.Done()
	if err := c.Invoke(t.Context(), "test", &ocsv1.Empty{}, &ocsv1.Empty{}); err != nil {
		t.Fatalf("old deadline killed reusable connection: %v", err)
	}
}

func TestLostResponsePoisonsConnectionAndDoesNotReplay(t *testing.T) {
	a, b := net.Pipe()
	c := &Client{conn: a}
	defer c.Close()
	done := make(chan struct{})
	go func() {
		defer close(done)
		defer b.Close()
		if _, err := Read(b); err != nil {
			return
		}
	}()
	err := c.Invoke(t.Context(), "write", &ocsv1.Empty{}, &ocsv1.Empty{})
	if status.Code(err) != codes.Unavailable {
		t.Fatal(err)
	}
	<-done
	if err := c.Invoke(t.Context(), "write", &ocsv1.Empty{}, &ocsv1.Empty{}); status.Code(err) != codes.Unavailable {
		t.Fatal("reused uncertain connection", err)
	}
}
