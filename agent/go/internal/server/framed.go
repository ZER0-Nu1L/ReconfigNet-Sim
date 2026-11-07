package server

import (
	"context"
	"net"
	"strings"
	"time"

	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/agent"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/framed"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

func dispatch[Q proto.Message, R proto.Message](ctx context.Context, payload []byte, request Q, method func(context.Context, Q) (R, error)) (proto.Message, error) {
	if err := proto.Unmarshal(payload, request); err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}
	return method(ctx, request)
}

func ServeFramed(ctx context.Context, listener net.Listener, a *agent.Agent) error {
	stop := context.AfterFunc(ctx, func() { listener.Close() })
	defer stop()
	s := &operationsServer{agent: a}
	handle := func(ctx context.Context, method string, payload []byte) (proto.Message, error) {
		const prefix = "/reconfig_net.ocs.v1.OcsOperations/"
		if !strings.HasPrefix(method, prefix) {
			return nil, status.Error(codes.Unimplemented, "unknown service")
		}
		switch strings.TrimPrefix(method, prefix) {
		case "AcquireControl":
			return dispatch(ctx, payload, &ocsv1.AcquireControlRequest{}, s.AcquireControl)
		case "RenewControl":
			return dispatch(ctx, payload, &ocsv1.RenewControlRequest{}, s.RenewControl)
		case "ReleaseControl":
			return dispatch(ctx, payload, &ocsv1.ReleaseControlRequest{}, s.ReleaseControl)
		case "GetControlState":
			return dispatch(ctx, payload, &ocsv1.Empty{}, s.GetControlState)
		case "GetRuntime":
			return dispatch(ctx, payload, &ocsv1.Empty{}, s.GetRuntime)
		case "GetPermutation":
			return dispatch(ctx, payload, &ocsv1.Empty{}, s.GetPermutation)
		case "ApplyBatch":
			return dispatch(ctx, payload, &ocsv1.ApplyBatchRequest{}, s.ApplyBatch)
		case "SetMode":
			return dispatch(ctx, payload, &ocsv1.SetModeRequest{}, s.SetMode)
		case "RecoverDeviceState":
			return dispatch(ctx, payload, &ocsv1.RecoverDeviceStateRequest{}, s.RecoverDeviceState)
		default:
			return nil, status.Error(codes.Unimplemented, "unknown method")
		}
	}
	for {
		conn, err := listener.Accept()
		if err != nil {
			return err
		}
		if tcp, ok := conn.(*net.TCPConn); ok {
			// The framed path is one small request and one small reply: never
			// let Nagle batch them.
			tcp.SetNoDelay(true)
		}
		go framed.ServeConn(ctx, conn, handle, 10*time.Second)
	}
}
