package bfrtclient

import (
	"context"
	"net"
	"strings"
	"testing"
	"time"

	pb "github.com/reconfig-net-sim/ocs-go-agent/gen/bfruntime"
	statuspb "google.golang.org/genproto/googleapis/rpc/status"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type fakeRuntime any

type runtimeOptions struct {
	rejectWrite, rejectSubscribe bool
	delay                        time.Duration
	deviceID                     uint32
	info                         string
	emptyReadKey                 bool
	wrongReadAction              bool
}

func startRuntime(t *testing.T, reject bool) (Config, <-chan *pb.WriteRequest) {
	cfg, writes, _ := runtimeWithOptions(t, runtimeOptions{rejectWrite: reject})
	return cfg, writes
}

func runtimeWithOptions(t *testing.T, opts runtimeOptions) (Config, <-chan *pb.WriteRequest, func()) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	gs := grpc.NewServer()
	t.Cleanup(gs.Stop)
	writes := make(chan *pb.WriteRequest, 4)
	unary := func(method string, handle func(context.Context, any) (any, error)) grpc.MethodDesc {
		return grpc.MethodDesc{MethodName: method, Handler: func(srv any, ctx context.Context, decode func(any) error, interceptor grpc.UnaryServerInterceptor) (any, error) {
			var request any
			switch method {
			case "Write":
				request = &pb.WriteRequest{}
			case "GetForwardingPipelineConfig":
				request = &pb.GetForwardingPipelineConfigRequest{}
			default:
				request = &pb.SetForwardingPipelineConfigRequest{}
			}
			if err := decode(request); err != nil {
				return nil, err
			}
			return handle(ctx, request)
		}}
	}
	gs.RegisterService(&grpc.ServiceDesc{ServiceName: "bfrt_proto.BfRuntime", HandlerType: (*fakeRuntime)(nil), Methods: []grpc.MethodDesc{
		unary("GetForwardingPipelineConfig", func(context.Context, any) (any, error) {
			if opts.info != "" {
				return &pb.GetForwardingPipelineConfigResponse{Config: []*pb.ForwardingPipelineConfig{{P4Name: "ocs", BfruntimeInfo: []byte(opts.info)}}}, nil
			}
			return &pb.GetForwardingPipelineConfigResponse{Config: []*pb.ForwardingPipelineConfig{{P4Name: "ocs", BfruntimeInfo: []byte(`{"tables":[{"name":"SwitchIngress.ocs_mapping","id":17,"key":[{"name":"in","id":1,"type":{"width":16}},{"name":"out","id":2,"type":{"width":16}}],"action_specs":[{"name":"permit","id":9}]}]}`)}}}, nil
		}),
		unary("SetForwardingPipelineConfig", func(_ context.Context, request any) (any, error) {
			if request.(*pb.SetForwardingPipelineConfigRequest).GetDeviceId() != opts.deviceID {
				return nil, status.Error(codes.InvalidArgument, "wrong bind device")
			}
			return &pb.SetForwardingPipelineConfigResponse{}, nil
		}),
		unary("Write", func(ctx context.Context, r any) (any, error) {
			writes <- r.(*pb.WriteRequest)
			if opts.delay > 0 {
				select {
				case <-time.After(opts.delay):
				case <-ctx.Done():
					return nil, ctx.Err()
				}
			}
			if opts.rejectWrite {
				return &pb.WriteResponse{Status: []*pb.Error{{CanonicalCode: int32(codes.InvalidArgument), Message: "update rejected"}}}, nil
			}
			return &pb.WriteResponse{}, nil
		}),
	}, Streams: []grpc.StreamDesc{
		{StreamName: "StreamChannel", ServerStreams: true, ClientStreams: true, Handler: func(_ any, s grpc.ServerStream) error {
			if err := s.RecvMsg(&pb.StreamMessageRequest{}); err != nil {
				return err
			}
			var code int32
			if opts.rejectSubscribe {
				code = int32(codes.AlreadyExists)
			}
			if err := s.SendMsg(&pb.StreamMessageResponse{Update: &pb.StreamMessageResponse_Subscribe{Subscribe: &pb.Subscribe{Status: &statuspb.Status{Code: code}}}}); err != nil {
				return err
			}
			<-s.Context().Done()
			return s.Context().Err()
		}},
		{StreamName: "Read", ServerStreams: true, Handler: func(_ any, s grpc.ServerStream) error {
			r := &pb.ReadRequest{}
			if err := s.RecvMsg(r); err != nil {
				return err
			}
			if !r.Entities[0].GetTableEntry().GetTableReadFlag().GetFromHw() {
				return status.Error(codes.InvalidArgument, "read must request hardware")
			}
			if opts.wrongReadAction {
				return s.SendMsg(&pb.ReadResponse{Entities: []*pb.Entity{{Entity: &pb.Entity_TableEntry{TableEntry: &pb.TableEntry{TableId: 17, Data: &pb.TableData{ActionId: 42}}}}}})
			}
			if opts.emptyReadKey {
				return s.SendMsg(&pb.ReadResponse{Entities: []*pb.Entity{{Entity: &pb.Entity_TableEntry{TableEntry: &pb.TableEntry{TableId: 17, Data: &pb.TableData{ActionId: 9}, Value: &pb.TableEntry_Key{Key: &pb.TableKey{Fields: []*pb.KeyField{
					{FieldId: 1, MatchType: &pb.KeyField_Exact_{Exact: &pb.KeyField_Exact{}}},
					{FieldId: 2, MatchType: &pb.KeyField_Exact_{Exact: &pb.KeyField_Exact{Value: []byte{0, 20}}}},
				}}}}}}}})
			}
			return s.SendMsg(&pb.ReadResponse{Entities: []*pb.Entity{{Entity: &pb.Entity_TableEntry{TableEntry: &pb.TableEntry{TableId: 17, Data: &pb.TableData{ActionId: 9}, Value: &pb.TableEntry_Key{Key: &pb.TableKey{Fields: []*pb.KeyField{
				{FieldId: 1, MatchType: &pb.KeyField_Exact_{Exact: &pb.KeyField_Exact{Value: []byte{0, 10}}}},
				{FieldId: 2, MatchType: &pb.KeyField_Exact_{Exact: &pb.KeyField_Exact{Value: []byte{0, 20}}}},
			}}}}}}}})
		}},
	}}, struct{}{})
	go gs.Serve(listener)
	return Config{DeviceID: opts.deviceID, Target: listener.Addr().String(), P4Name: "ocs", TableName: "SwitchIngress.ocs_mapping", ActionName: "permit", IngressField: "in", EgressField: "out", LogicalToDevice: map[int]int{1: 10, 2: 20}, Timeout: time.Second}, writes, gs.Stop
}

func TestBFRTProtocolDiscoversFieldsAndReportsEmbeddedFailures(t *testing.T) {
	for _, reject := range []bool{false, true} {
		t.Run(map[bool]string{false: "success", true: "embedded_failure"}[reject], func(t *testing.T) {
			cfg, writes := startRuntime(t, reject)
			c, err := Dial(t.Context(), cfg)
			if err != nil {
				t.Fatal(err)
			}
			defer c.Close()
			pairs, err := c.ReadPairs(t.Context())
			if err != nil || len(pairs) != 1 || pairs[0] != [2]int{1, 2} {
				t.Fatal(pairs, err)
			}
			err = c.WriteAtomic(t.Context(), [][2]int{{1, 2}}, [][2]int{{2, 1}})
			if (err != nil) != reject {
				t.Fatalf("embedded status ignored: %v", err)
			}
			request := <-writes
			if request.Atomicity != pb.WriteRequest_ROLLBACK_ON_ERROR || len(request.Updates) != 2 || request.Updates[0].Type != pb.Update_DELETE || request.Updates[1].Type != pb.Update_INSERT {
				t.Fatal("wrong write contract", request)
			}
			if _, err := c.buildRequest([][2]int{{3, 1}}, pb.Update_INSERT); err == nil {
				t.Fatal("accepted unmapped port")
			}
		})
	}
}

func TestInvalidInsertDoesNotSendDeletes(t *testing.T) {
	cfg, writes := startRuntime(t, false)
	c, err := Dial(t.Context(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if err := c.WritePairs(t.Context(), [][2]int{{1, 2}}, [][2]int{{3, 1}}); err == nil {
		t.Fatal("accepted unmapped insert")
	}
	if len(writes) != 0 {
		t.Fatal("deleted before validating inserts")
	}
}
func TestWriteTimeoutAndSubscriptionFailure(t *testing.T) {
	t.Run("timeout", func(t *testing.T) {
		cfg, writes, _ := runtimeWithOptions(t, runtimeOptions{delay: time.Second})
		cfg.Timeout = 50 * time.Millisecond
		c, err := Dial(t.Context(), cfg)
		if err != nil {
			t.Fatal(err)
		}
		defer c.Close()
		err = c.WriteAtomic(t.Context(), nil, [][2]int{{1, 2}})
		if err == nil || len(writes) != 1 {
			t.Fatal(err, len(writes))
		}
	})
	t.Run("duplicate_subscription", func(t *testing.T) {
		cfg, _, _ := runtimeWithOptions(t, runtimeOptions{rejectSubscribe: true})
		if c, err := Dial(t.Context(), cfg); err == nil {
			c.Close()
			t.Fatal("accepted rejected subscription")
		}
	})
	t.Run("disconnect", func(t *testing.T) {
		cfg, _, stop := runtimeWithOptions(t, runtimeOptions{})
		c, err := Dial(t.Context(), cfg)
		if err != nil {
			t.Fatal(err)
		}
		defer c.Close()
		stop()
		if err := c.WriteAtomic(t.Context(), nil, [][2]int{{1, 2}}); err == nil {
			t.Fatal("accepted write after disconnect")
		}
	})
}

func TestDuplicateErrorClassification(t *testing.T) {
	nested, err := status.New(codes.Unknown, "write failed").WithDetails(&pb.Error{CanonicalCode: int32(codes.AlreadyExists)})
	if err != nil {
		t.Fatal(err)
	}
	if !IsDuplicate(nested.Err()) || !IsDuplicate(status.Error(codes.AlreadyExists, "duplicate")) {
		t.Fatal("did not decode duplicate")
	}
	if IsDuplicate(status.Error(codes.DeadlineExceeded, "timeout")) || IsDuplicate(status.Error(codes.Unknown, "unknown")) {
		t.Fatal("treated uncertain result as rollback evidence")
	}
}

func TestBindHonorsNonzeroDeviceID(t *testing.T) {
	cfg, _, _ := runtimeWithOptions(t, runtimeOptions{deviceID: 2})
	c, err := Dial(t.Context(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	c.Close()
}

func TestAmbiguousActionPreventsWrites(t *testing.T) {
	info := `{"tables":[{"name":"SwitchIngress.ocs_mapping","id":17,"key":[{"name":"in","id":1,"type":{"width":16}},{"name":"out","id":2,"type":{"width":16}}],"action_specs":[{"name":"a.permit","id":9},{"name":"b.permit","id":10}]}]}`
	cfg, writes, _ := runtimeWithOptions(t, runtimeOptions{info: info})
	c, err := Dial(t.Context(), cfg)
	if err == nil {
		c.Close()
		t.Fatal("accepted ambiguous action")
	}
	if !strings.Contains(err.Error(), "exactly one BFRT action") || len(writes) != 0 {
		t.Fatal(err, len(writes))
	}
}

func TestEmptyReadKeyCannotMasqueradeAsMappedPortZero(t *testing.T) {
	cfg, _, _ := runtimeWithOptions(t, runtimeOptions{emptyReadKey: true})
	cfg.LogicalToDevice[1] = 0
	c, err := Dial(t.Context(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if _, err := c.ReadPairs(t.Context()); err == nil {
		t.Fatal("empty key decoded as device port zero")
	}
}

func TestUnexpectedReadActionIsNotTreatedAsPermit(t *testing.T) {
	cfg, _, _ := runtimeWithOptions(t, runtimeOptions{wrongReadAction: true})
	c, err := Dial(t.Context(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if _, err := c.ReadPairs(t.Context()); err == nil {
		t.Fatal("unexpected action treated as permit")
	}
}
