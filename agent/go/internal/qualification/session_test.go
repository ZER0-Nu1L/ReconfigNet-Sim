package qualification

import (
	"context"
	"errors"
	"net"
	"slices"
	"strings"
	"testing"
	"time"

	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/framed"
	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/proto"
)

type fake struct {
	ocsv1.OcsOperationsClient
	revision                             uint64
	pi                                   []uint32
	writes, renews                       int
	writeError, renewError, releaseError error
	badAck                               string
}

func options() Options {
	return Options{Initial: []uint32{2, 1, 4, 3}, Alternate: []uint32{3, 4, 1, 2}, Duration: 30 * time.Millisecond, Interval: 5 * time.Millisecond, WriteInterval: 10 * time.Millisecond, Timeout: time.Second, Compact: true}
}
func (f *fake) state() *ocsv1.RuntimeState {
	return &ocsv1.RuntimeState{Revision: f.revision, Mode: ocsv1.Mode_MODE_OCS, Status: ocsv1.RuntimeStatus_RUNTIME_STATUS_READY, DeviceState: &ocsv1.DeviceState{CacheStatus: "READY"}}
}
func (f *fake) AcquireControl(context.Context, *ocsv1.AcquireControlRequest, ...grpc.CallOption) (*ocsv1.ControlLease, error) {
	return &ocsv1.ControlLease{LeaseToken: "lease", Revision: f.revision}, nil
}
func (f *fake) RenewControl(context.Context, *ocsv1.RenewControlRequest, ...grpc.CallOption) (*ocsv1.ControlLease, error) {
	f.renews++
	return &ocsv1.ControlLease{LeaseToken: "lease", Revision: f.revision}, f.renewError
}
func (f *fake) ReleaseControl(context.Context, *ocsv1.ReleaseControlRequest, ...grpc.CallOption) (*ocsv1.ControlState, error) {
	return &ocsv1.ControlState{}, f.releaseError
}
func (f *fake) GetPermutation(context.Context, *ocsv1.Empty, ...grpc.CallOption) (*ocsv1.GetPermutationResponse, error) {
	return &ocsv1.GetPermutationResponse{Permutation: &ocsv1.Permutation{Pi: f.pi}, State: f.state()}, nil
}
func (f *fake) ApplyBatch(ctx context.Context, r *ocsv1.ApplyBatchRequest, _ ...grpc.CallOption) (*ocsv1.OperationReply, error) {
	f.writes++
	md, _ := metadata.FromOutgoingContext(ctx)
	if !slices.Equal(md.Get("x-ocs-control-lease"), []string{"lease"}) || !slices.Equal(md.Get("x-ocs-compact-reply"), []string{"true"}) {
		return nil, errors.New("missing metadata")
	}
	if !r.HasExpectedRevision || r.ExpectedRevision != f.revision {
		return nil, errors.New("revision lost")
	}
	if f.writeError != nil {
		return nil, f.writeError
	}
	f.pi = slices.Clone(r.GetPermutation().GetPi())
	f.revision++
	reply := &ocsv1.OperationReply{Result: "updated", State: f.state(), RequestId: uint64(f.writes), Timing: &ocsv1.OperationTiming{DeviceWriteRequests: 1}}
	switch f.badAck {
	case "revision":
		reply.State.Revision--
	case "unknown":
		reply.State.DeviceState.CacheStatus = "UNKNOWN"
	case "noop":
		reply.Result = "no-op"
	case "split":
		reply.Timing.DeviceWriteRequests = 2
	}
	return reply, nil
}
func newFake() *fake { return &fake{pi: slices.Clone(options().Initial), revision: 10} }
func TestFailureNeverRestoresOrAllowsMoreWrites(t *testing.T) {
	for _, kind := range []string{"rpc", "revision", "unknown", "noop", "split", "record"} {
		t.Run(kind, func(t *testing.T) {
			f := newFake()
			f.badAck = kind
			if kind == "rpc" {
				f.writeError = errors.New("response lost")
			}
			s, _ := New(f, options())
			if err := s.Acquire(t.Context()); err != nil {
				t.Fatal(err)
			}
			emit := func(Event) error {
				if kind == "record" {
					return errors.New("disk full")
				}
				return nil
			}
			if err := s.Pair(t.Context(), emit); err == nil {
				t.Fatal("accepted unexpected outcome")
			}
			if err := s.Pair(t.Context(), emit); err == nil || f.writes != 1 {
				t.Fatalf("replayed/restored failed write: %d %v", f.writes, err)
			}
		})
	}
}
func TestLeaseRenewalFailureStopsWrites(t *testing.T) {
	f := newFake()
	s, _ := New(f, options())
	_ = s.Acquire(t.Context())
	f.renewError = errors.New("expired")
	if err := s.Renew(t.Context()); err == nil {
		t.Fatal("ignored renewal")
	}
	if err := s.Pair(t.Context(), func(Event) error { return nil }); err == nil || f.writes != 0 {
		t.Fatal("wrote after lease loss")
	}
}
func TestCancellationAndReleaseFailureCannotComplete(t *testing.T) {
	for _, kind := range []string{"cancel", "release"} {
		t.Run(kind, func(t *testing.T) {
			f := newFake()
			s, _ := New(f, options())
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			if kind == "release" {
				f.releaseError = errors.New("release failed")
			}
			var events []Event
			err := s.Run(ctx, func(e Event) error {
				events = append(events, e)
				if kind == "cancel" && e.Kind == "sample" {
					cancel()
				}
				return nil
			})
			if err == nil || events[len(events)-1].Kind != "failed" {
				t.Fatalf("false complete: %v %+v", err, events)
			}
		})
	}
}
func TestPersistentFramedRunTracksRevisionsAndRenews(t *testing.T) {
	f := newFake()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	done := make(chan struct{})
	go func() {
		defer close(done)
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		framed.ServeConn(t.Context(), conn, func(ctx context.Context, method string, payload []byte) (proto.Message, error) {
			// ServeConn supplies incoming metadata; adapt to the fake client boundary.
			md, _ := metadata.FromIncomingContext(ctx)
			ctx = metadata.NewOutgoingContext(ctx, md)
			switch {
			case strings.HasSuffix(method, "/AcquireControl"):
				return f.AcquireControl(ctx, &ocsv1.AcquireControlRequest{})
			case strings.HasSuffix(method, "/RenewControl"):
				return f.RenewControl(ctx, &ocsv1.RenewControlRequest{})
			case strings.HasSuffix(method, "/ReleaseControl"):
				return f.ReleaseControl(ctx, &ocsv1.ReleaseControlRequest{})
			case strings.HasSuffix(method, "/GetPermutation"):
				return f.GetPermutation(ctx, &ocsv1.Empty{})
			case strings.HasSuffix(method, "/ApplyBatch"):
				r := new(ocsv1.ApplyBatchRequest)
				if err := proto.Unmarshal(payload, r); err != nil {
					return nil, err
				}
				return f.ApplyBatch(ctx, r)
			default:
				return nil, errors.New("unexpected method")
			}
		}, time.Second)
	}()
	conn, err := framed.Dial(t.Context(), "framed://"+listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	s, _ := New(ocsv1.NewOcsOperationsClient(conn), options())
	var events []Event
	err = s.Run(t.Context(), func(e Event) error { events = append(events, e); return nil })
	conn.Close()
	<-done
	if err != nil {
		t.Fatal(err)
	}
	if f.writes < 4 || f.writes%2 != 0 || f.renews < 2 || !slices.Equal(f.pi, options().Initial) {
		t.Fatalf("incomplete exercise: %+v", f)
	}
	last := events[len(events)-1]
	if last.Kind != "complete" || last.Revision != 10+uint64(f.writes) || last.Pair != uint64(f.writes/2) {
		t.Fatalf("bad completion: %+v", last)
	}
}
