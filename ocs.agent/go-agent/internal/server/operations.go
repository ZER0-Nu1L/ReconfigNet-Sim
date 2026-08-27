package server

import (
	"context"
	"strconv"

	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/agent"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/apierr"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/model"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
)

const leaseMetadataKey = "x-ocs-control-lease"

func leaseFromContext(ctx context.Context) string {
	if metadata, ok := metadata.FromIncomingContext(ctx); ok {
		values := metadata.Get(leaseMetadataKey)
		if len(values) > 0 {
			return values[0]
		}
	}
	return ""
}

func expectedRevisionFromContext(ctx context.Context) (*uint64, error) {
	if incoming, ok := metadata.FromIncomingContext(ctx); ok {
		values := incoming.Get("x-ocs-expected-revision")
		if len(values) > 0 {
			value, err := strconv.ParseUint(values[0], 10, 64)
			if err != nil {
				return nil, apierr.New(codes.InvalidArgument,
					"x-ocs-expected-revision must be a non-negative integer", nil)
			}
			return &value, nil
		}
	}
	return nil, apierr.New(codes.FailedPrecondition,
		"x-ocs-expected-revision metadata is required", nil)
}

type operationsServer struct {
	ocsv1.UnimplementedOcsOperationsServer
	agent *agent.Agent
}

func (s *operationsServer) AcquireControl(
	_ context.Context,
	request *ocsv1.AcquireControlRequest,
) (*ocsv1.ControlLease, error) {
	lease, err := s.agent.AcquireControl(
		request.GetClientId(), request.GetRequestedLeaseSeconds())
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	return controlLeaseProto(lease), nil
}

func (s *operationsServer) RenewControl(
	ctx context.Context,
	request *ocsv1.RenewControlRequest,
) (*ocsv1.ControlLease, error) {
	token := request.GetLeaseToken()
	if token == "" {
		token = leaseFromContext(ctx)
	}
	lease, err := s.agent.RenewControl(token, request.GetRequestedLeaseSeconds())
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	return controlLeaseProto(lease), nil
}

func (s *operationsServer) ReleaseControl(
	ctx context.Context,
	request *ocsv1.ReleaseControlRequest,
) (*ocsv1.ControlState, error) {
	token := request.GetLeaseToken()
	if token == "" {
		token = leaseFromContext(ctx)
	}
	state, err := s.agent.ReleaseControl(token)
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	return controlStateProto(state), nil
}

func (s *operationsServer) GetControlState(
	context.Context,
	*ocsv1.Empty,
) (*ocsv1.ControlState, error) {
	return controlStateProto(s.agent.ControlState()), nil
}

func (s *operationsServer) GetRuntime(
	context.Context,
	*ocsv1.Empty,
) (*ocsv1.GetRuntimeResponse, error) {
	snapshot := s.agent.Snapshot()
	return &ocsv1.GetRuntimeResponse{
		State:      runtimeProto(snapshot),
		LastTiming: timingProto(snapshot.LastTiming),
	}, nil
}

func (s *operationsServer) GetPermutation(
	context.Context,
	*ocsv1.Empty,
) (*ocsv1.GetPermutationResponse, error) {
	permutation, err := s.agent.Permutation()
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	return &ocsv1.GetPermutationResponse{
		State:       runtimeProto(s.agent.Snapshot()),
		Permutation: &ocsv1.Permutation{Pi: permutation},
	}, nil
}

func (s *operationsServer) ApplyBatch(
	ctx context.Context,
	request *ocsv1.ApplyBatchRequest,
) (*ocsv1.OperationReply, error) {
	if !request.GetHasExpectedRevision() {
		return nil, apierr.GRPC(apierr.New(
			codes.FailedPrecondition,
			"expected_revision is required for ApplyBatch", nil))
	}
	strategy, err := strategyName(request.GetStrategy())
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	transport, err := transportName(request.GetTransport())
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	var expectedRevision *uint64
	if request.GetHasExpectedRevision() {
		value := request.GetExpectedRevision()
		expectedRevision = &value
	}

	var result agent.Result
	switch intent := request.GetIntent().(type) {
	case *ocsv1.ApplyBatchRequest_Permutation:
		result, err = s.agent.ApplyPermutation(
			ctx, intent.Permutation.GetPi(), strategy, transport,
			request.GetDelayUs(), expectedRevision, leaseFromContext(ctx))
	case *ocsv1.ApplyBatchRequest_ConnectionSet:
		connections := make([]model.Connection, 0, len(intent.ConnectionSet.GetConnections()))
		for _, connection := range intent.ConnectionSet.GetConnections() {
			if connection.GetStatus() != "" {
				return nil, apierr.GRPC(apierr.New(
					codes.Unimplemented,
					"connection status is read-only and cannot be set by ApplyBatch", nil))
			}
			connections = append(connections, model.Connection{
				Name:          connection.GetConnectionName(),
				Bidirectional: connection.GetBidirectional(),
				NearPortName:  connection.GetNearPortName(),
				FarPortName:   connection.GetFarPortName(),
			})
		}
		result, err = s.agent.ReplaceConnections(
			ctx, connections, strategy, transport,
			request.GetDelayUs(), expectedRevision, leaseFromContext(ctx))
	default:
		err = apierr.New(
			codes.InvalidArgument,
			"ApplyBatch requires connection_set or permutation", nil)
	}
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	return operationReply(result, s.agent.Snapshot()), nil
}

func (s *operationsServer) SetMode(
	ctx context.Context,
	request *ocsv1.SetModeRequest,
) (*ocsv1.OperationReply, error) {
	if !request.GetHasExpectedRevision() {
		return nil, apierr.GRPC(apierr.New(
			codes.FailedPrecondition,
			"expected_revision is required for SetMode", nil))
	}
	var mode string
	switch request.GetMode() {
	case ocsv1.Mode_MODE_OCS:
		mode = "ocs"
	case ocsv1.Mode_MODE_DEBUG:
		mode = "debug"
	default:
		return nil, apierr.GRPC(apierr.New(
			codes.InvalidArgument,
			"SetMode requires MODE_OCS or MODE_DEBUG", nil))
	}
	transport, err := transportName(request.GetTransport())
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	var expectedRevision *uint64
	if request.GetHasExpectedRevision() {
		value := request.GetExpectedRevision()
		expectedRevision = &value
	}
	result, err := s.agent.SetMode(
		ctx, mode, request.GetDelayUs(), transport, expectedRevision,
		leaseFromContext(ctx))
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	return operationReply(result, s.agent.Snapshot()), nil
}

func (s *operationsServer) RecoverDeviceState(
	ctx context.Context,
	request *ocsv1.RecoverDeviceStateRequest,
) (*ocsv1.OperationReply, error) {
	if request.GetMode() != ocsv1.RecoveryMode_RECOVERY_MODE_REAPPLY_DESIRED {
		return nil, apierr.GRPC(apierr.New(codes.InvalidArgument,
			"RecoverDeviceState requires REAPPLY_DESIRED", nil))
	}
	if !request.GetHasExpectedRevision() {
		return nil, apierr.GRPC(apierr.New(codes.FailedPrecondition,
			"expected_revision is required for RecoverDeviceState", nil))
	}
	revision := request.GetExpectedRevision()
	result, err := s.agent.RecoverDeviceState(
		ctx, &revision, leaseFromContext(ctx))
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	return operationReply(result, s.agent.Snapshot()), nil
}

func controlLeaseProto(value agent.ControlLease) *ocsv1.ControlLease {
	return &ocsv1.ControlLease{
		LeaseToken: value.LeaseToken, LeaseEpoch: value.LeaseEpoch,
		ExpiresUnixNs: value.ExpiresUnixNS, Revision: value.Revision,
	}
}

func controlStateProto(value agent.ControlState) *ocsv1.ControlState {
	return &ocsv1.ControlState{
		Active: value.Active, ClientId: value.ClientID,
		LeaseEpoch: value.LeaseEpoch, ExpiresUnixNs: value.ExpiresUnixNS,
		Revision: value.Revision,
	}
}

func runtimeProto(snapshot agent.Snapshot) *ocsv1.RuntimeState {
	state := &ocsv1.RuntimeState{
		Mode:          modeProto(snapshot.Mode),
		Status:        statusProto(snapshot.Status),
		Revision:      snapshot.Revision,
		RequestId:     snapshot.RequestID,
		ActiveEntries: snapshot.ActiveEntries,
		Profile:       snapshot.Profile,
		LastError:     snapshot.LastError,
		DeviceState: &ocsv1.DeviceState{
			ConsistencyMode:     snapshot.DeviceState.ConsistencyMode,
			CacheStatus:         snapshot.DeviceState.CacheStatus,
			Generation:          snapshot.DeviceState.Generation,
			LastVerifiedUnixNs:  snapshot.DeviceState.LastVerifiedUnixNS,
			LastReconcileUnixNs: snapshot.DeviceState.LastReconcileUnixNS,
			DriftCount:          snapshot.DeviceState.DriftCount,
			WriteVerification:   snapshot.DeviceState.WriteVerification,
			ReadbackSource:      snapshot.DeviceState.ReadbackSource,
			LastWriteAckUnixNs:  snapshot.DeviceState.LastWriteAckUnixNS,
		},
		ConnectionSet: &ocsv1.ConnectionSet{},
		BackendCapabilities: &ocsv1.BackendCapabilities{
			Backend:            snapshot.BackendCapabilities.Backend,
			Readback:           snapshot.BackendCapabilities.Readback,
			NativeBatch:        snapshot.BackendCapabilities.NativeBatch,
			DataplaneAtomic:    snapshot.BackendCapabilities.DataplaneAtomic,
			Transports:         snapshot.BackendCapabilities.Transports,
			WriteVerifications: snapshot.BackendCapabilities.WriteVerifications,
			ReadbackSources:    snapshot.BackendCapabilities.ReadbackSources,
		},
	}
	for _, connection := range snapshot.Connections {
		state.ConnectionSet.Connections = append(
			state.ConnectionSet.Connections, &ocsv1.Connection{
				ConnectionName: connection.Name,
				Bidirectional:  connection.Bidirectional,
				NearPortName:   connection.NearPortName,
				FarPortName:    connection.FarPortName,
				Status:         connection.Status,
			})
	}
	return state
}

func operationReply(result agent.Result, snapshot agent.Snapshot) *ocsv1.OperationReply {
	return &ocsv1.OperationReply{
		State:                 runtimeProto(snapshot),
		Result:                result.Result,
		RequestId:             result.RequestID,
		RequestReceivedUnixNs: result.RequestReceivedUnixNS,
		Timing:                timingProto(&result.Timing),
	}
}

func timingProto(timing *agent.Timing) *ocsv1.OperationTiming {
	if timing == nil {
		return &ocsv1.OperationTiming{}
	}
	return &ocsv1.OperationTiming{
		Strategy:               timing.Strategy,
		Transport:              timing.Transport,
		QueueWaitUs:            timing.QueueWaitUS,
		ValidationUs:           timing.ValidationUS,
		PlanningUs:             timing.PlanningUS,
		DeleteCommitUs:         timing.DeleteCommitUS,
		RequestedGapUs:         timing.RequestedGapUS,
		ActualGapUs:            timing.ActualGapUS,
		InstallCommitUs:        timing.InstallCommitUS,
		ReadbackUs:             timing.ReadbackUS,
		RollbackUs:             timing.RollbackUS,
		ProgrammingTotalUs:     timing.ProgrammingTotalUS,
		ServerTotalUs:          timing.ServerTotalUS,
		DeleteEntries:          timing.DeleteEntries,
		InsertEntries:          timing.InsertEntries,
		UnchangedEntries:       timing.UnchangedEntries,
		ActiveEntries:          timing.ActiveEntries,
		DeviceWriteRequests:    timing.DeviceWriteRequests,
		DeviceWorkerRpcUs:      timing.DeviceWorkerRPCUS,
		DeviceWorkerTotalUs:    timing.DeviceWorkerTotalUS,
		PreconditionReadbackUs: timing.PreconditionReadbackUS,
		LeaseRevisionCheckUs:   timing.LeaseRevisionCheckUS,
		CachePreconditionUs:    timing.CachePreconditionUS,
		SouthboundQueueWaitUs:  timing.SouthboundQueueWaitUS,
		WriteVerification:      timing.WriteVerification,
		ReadbackSource:         timing.ReadbackSource,
	}
}

func strategyName(strategy ocsv1.ExecutionStrategy) (string, error) {
	switch strategy {
	case ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_UNSPECIFIED,
		ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_FULL:
		return "FULL", nil
	case ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_DELTA:
		return "DELTA", nil
	default:
		return "", apierr.New(codes.InvalidArgument, "unknown execution strategy", nil)
	}
}

func transportName(transport ocsv1.Transport) (string, error) {
	switch transport {
	case ocsv1.Transport_TRANSPORT_UNSPECIFIED,
		ocsv1.Transport_TRANSPORT_SEQUENTIAL:
		return "SEQUENTIAL", nil
	case ocsv1.Transport_TRANSPORT_NATIVE_BATCH:
		return "NATIVE_BATCH", nil
	default:
		return "", apierr.New(codes.InvalidArgument, "unknown transport", nil)
	}
}

func modeProto(mode string) ocsv1.Mode {
	if mode == "debug" {
		return ocsv1.Mode_MODE_DEBUG
	}
	return ocsv1.Mode_MODE_OCS
}

func statusProto(runtimeStatus string) ocsv1.RuntimeStatus {
	switch runtimeStatus {
	case "ready":
		return ocsv1.RuntimeStatus_RUNTIME_STATUS_READY
	case "updating":
		return ocsv1.RuntimeStatus_RUNTIME_STATUS_UPDATING
	case "error":
		return ocsv1.RuntimeStatus_RUNTIME_STATUS_ERROR
	default:
		return ocsv1.RuntimeStatus_RUNTIME_STATUS_UNSPECIFIED
	}
}
