package backend

import (
	"context"
	"fmt"
	"maps"
	"slices"
	"sync"
	"time"

	backendv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/backendv1"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/model"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

type Capabilities struct {
	Backend            string   `json:"backend"`
	Readback           bool     `json:"readback"`
	NativeBatch        bool     `json:"native_batch"`
	DataplaneAtomic    bool     `json:"dataplane_atomic"`
	Transports         []string `json:"transports"`
	WriteVerifications []string `json:"write_verifications"`
	ReadbackSources    []string `json:"readback_sources"`
}

type Timing struct {
	Strategy               string
	Transport              string
	PlanningUS             uint64
	DeleteCommitUS         uint64
	RequestedGapUS         uint64
	ActualGapUS            uint64
	InstallCommitUS        uint64
	ReadbackUS             uint64
	RollbackUS             uint64
	ProgrammingTotalUS     uint64
	DeleteEntries          uint32
	InsertEntries          uint32
	UnchangedEntries       uint32
	ActiveEntries          uint32
	DeviceWriteRequests    uint32
	DeviceWorkerRPCUS      uint64
	DeviceWorkerTotalUS    uint64
	PreconditionReadbackUS uint64
	CachePreconditionUS    uint64
	SouthboundQueueWaitUS  uint64
	WriteVerification      string
	ReadbackSource         string
}

type DeviceState struct {
	Generation          uint64 `json:"generation"`
	CacheStatus         string `json:"cache_status"`
	LastVerifiedUnixNS  uint64 `json:"last_verified_unix_ns"`
	LastReconcileUnixNS uint64 `json:"last_reconcile_unix_ns"`
	DriftCount          uint64 `json:"drift_count"`
	WriteVerification   string `json:"write_verification"`
	ReadbackSource      string `json:"readback_source"`
	LastWriteAckUnixNS  uint64 `json:"last_write_ack_unix_ns"`
}

type TransitionError struct {
	Message       string
	RollbackError string
	Restored      bool
	Code          string
	Timing        Timing
}

func (e *TransitionError) Error() string {
	if e.RollbackError != "" {
		return fmt.Sprintf("%s; %s", e.Message, e.RollbackError)
	}
	return e.Message
}

type Client struct {
	connection   *grpc.ClientConn
	client       backendv1.DeviceBackendClient
	timeout      time.Duration
	capabilities Capabilities
	mu           sync.RWMutex
	state        DeviceState
}

func New(ctx context.Context, target string, timeout time.Duration) (*Client, error) {
	connection, err := grpc.NewClient(
		target, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, fmt.Errorf("create device worker client: %w", err)
	}
	client := &Client{
		connection: connection,
		client:     backendv1.NewDeviceBackendClient(connection),
		timeout:    timeout,
	}
	requestContext, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	response, err := client.client.Capabilities(requestContext, &backendv1.Empty{})
	if err != nil {
		connection.Close()
		return nil, fmt.Errorf("device worker capabilities: %w", err)
	}
	client.capabilities = Capabilities{
		Backend:            response.GetBackend(),
		Readback:           response.GetReadback(),
		NativeBatch:        response.GetNativeBatch(),
		DataplaneAtomic:    response.GetDataplaneAtomic(),
		Transports:         slices.Clone(response.GetTransports()),
		WriteVerifications: slices.Clone(response.GetWriteVerifications()),
		ReadbackSources:    slices.Clone(response.GetReadbackSources()),
	}
	if _, err := client.Read(ctx); err != nil {
		connection.Close()
		return nil, err
	}
	return client, nil
}

func (c *Client) Close() error {
	return c.connection.Close()
}

func (c *Client) Capabilities() Capabilities {
	capabilities := c.capabilities
	capabilities.Transports = slices.Clone(capabilities.Transports)
	capabilities.WriteVerifications = slices.Clone(capabilities.WriteVerifications)
	capabilities.ReadbackSources = slices.Clone(capabilities.ReadbackSources)
	return capabilities
}

func (c *Client) DeviceState() DeviceState {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.state
}

func (c *Client) captureState(response interface {
	GetGeneration() uint64
	GetCacheStatus() string
	GetLastVerifiedUnixNs() uint64
	GetLastReconcileUnixNs() uint64
	GetDriftCount() uint64
	GetWriteVerification() string
	GetReadbackSource() string
	GetLastWriteAckUnixNs() uint64
}) DeviceState {
	state := DeviceState{
		Generation:          response.GetGeneration(),
		CacheStatus:         response.GetCacheStatus(),
		LastVerifiedUnixNS:  response.GetLastVerifiedUnixNs(),
		LastReconcileUnixNS: response.GetLastReconcileUnixNs(),
		DriftCount:          response.GetDriftCount(),
		WriteVerification:   response.GetWriteVerification(),
		ReadbackSource:      response.GetReadbackSource(),
		LastWriteAckUnixNS:  response.GetLastWriteAckUnixNs(),
	}
	c.mu.Lock()
	c.state = state
	c.mu.Unlock()
	return state
}

func (c *Client) markUnknown() {
	c.mu.Lock()
	c.state.CacheStatus = "UNKNOWN"
	c.mu.Unlock()
}

func (c *Client) Read(ctx context.Context) (map[model.Pair]struct{}, error) {
	requestContext, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	response, err := c.client.ReadEntries(requestContext, &backendv1.Empty{})
	if err != nil {
		c.markUnknown()
		return nil, fmt.Errorf("device worker read: %w", err)
	}
	c.captureState(response)
	return pairsFromProto(response.GetEntries()), nil
}

func (c *Client) Apply(
	ctx context.Context,
	previous map[model.Pair]struct{},
	target map[model.Pair]struct{},
	strategy string,
	transport string,
	delayUS uint64,
) (Timing, error) {
	request := &backendv1.ApplyTransitionRequest{
		ExpectedEntries:    pairsToProto(previous),
		TargetEntries:      pairsToProto(target),
		Strategy:           strategy,
		Transport:          transport,
		DelayUs:            delayUS,
		ExpectedGeneration: c.DeviceState().Generation,
	}
	requestContext, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	started := time.Now()
	response, err := c.client.ApplyTransition(requestContext, request)
	rpcUS := uint64(time.Since(started).Microseconds())
	if err != nil {
		c.markUnknown()
		return Timing{DeviceWorkerRPCUS: rpcUS}, fmt.Errorf("device worker apply: %w", err)
	}
	timing := timingFromProto(response.GetTiming())
	c.captureState(response)
	timing.DeviceWorkerRPCUS = rpcUS
	if response.GetSuccess() {
		return timing, nil
	}
	return timing, &TransitionError{
		Message:       response.GetError(),
		RollbackError: response.GetRollbackError(),
		Restored:      response.GetRestored(),
		Code:          response.GetErrorCode(),
		Timing:        timing,
	}
}

func (c *Client) Reconcile(
	ctx context.Context,
	desired map[model.Pair]struct{},
) (DeviceState, map[model.Pair]struct{}, error) {
	request := &backendv1.ReconcileRequest{DesiredEntries: pairsToProto(desired)}
	requestContext, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	response, err := c.client.Reconcile(requestContext, request)
	if err != nil {
		c.markUnknown()
		return DeviceState{}, nil, fmt.Errorf("device worker reconcile: %w", err)
	}
	state := c.captureState(response)
	return state, pairsFromProto(response.GetEntries()), nil
}

func (c *Client) Recover(
	ctx context.Context,
	desired map[model.Pair]struct{},
	strategy string,
	transport string,
	delayUS uint64,
) (Timing, error) {
	request := &backendv1.RecoverRequest{
		DesiredEntries: pairsToProto(desired), Strategy: strategy,
		Transport: transport, DelayUs: delayUS,
	}
	requestContext, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	started := time.Now()
	response, err := c.client.Recover(requestContext, request)
	rpcUS := uint64(time.Since(started).Microseconds())
	if err != nil {
		c.markUnknown()
		return Timing{DeviceWorkerRPCUS: rpcUS}, fmt.Errorf("device worker recover: %w", err)
	}
	c.captureState(response)
	timing := timingFromProto(response.GetTiming())
	timing.DeviceWorkerRPCUS = rpcUS
	if response.GetSuccess() {
		return timing, nil
	}
	return timing, &TransitionError{
		Message: response.GetError(), RollbackError: response.GetRollbackError(),
		Restored: response.GetRestored(), Code: response.GetErrorCode(), Timing: timing,
	}
}

func pairsToProto(pairs map[model.Pair]struct{}) []*backendv1.PortPair {
	ordered := slices.SortedFunc(maps.Keys(pairs), func(a, b model.Pair) int {
		if a.Ingress != b.Ingress {
			return int(a.Ingress) - int(b.Ingress)
		}
		return int(a.Egress) - int(b.Egress)
	})
	result := make([]*backendv1.PortPair, 0, len(ordered))
	for _, pair := range ordered {
		result = append(result, &backendv1.PortPair{
			IngressPort: pair.Ingress,
			EgressPort:  pair.Egress,
		})
	}
	return result
}

func pairsFromProto(pairs []*backendv1.PortPair) map[model.Pair]struct{} {
	result := make(map[model.Pair]struct{}, len(pairs))
	for _, pair := range pairs {
		result[model.Pair{
			Ingress: pair.GetIngressPort(),
			Egress:  pair.GetEgressPort(),
		}] = struct{}{}
	}
	return result
}

func timingFromProto(value *backendv1.OperationTiming) Timing {
	if value == nil {
		return Timing{}
	}
	return Timing{
		Strategy:               value.GetStrategy(),
		Transport:              value.GetTransport(),
		PlanningUS:             value.GetPlanningUs(),
		DeleteCommitUS:         value.GetDeleteCommitUs(),
		RequestedGapUS:         value.GetRequestedGapUs(),
		ActualGapUS:            value.GetActualGapUs(),
		InstallCommitUS:        value.GetInstallCommitUs(),
		ReadbackUS:             value.GetReadbackUs(),
		RollbackUS:             value.GetRollbackUs(),
		ProgrammingTotalUS:     value.GetProgrammingTotalUs(),
		DeleteEntries:          value.GetDeleteEntries(),
		InsertEntries:          value.GetInsertEntries(),
		UnchangedEntries:       value.GetUnchangedEntries(),
		ActiveEntries:          value.GetActiveEntries(),
		DeviceWriteRequests:    value.GetDeviceWriteRequests(),
		DeviceWorkerTotalUS:    value.GetDeviceWorkerTotalUs(),
		PreconditionReadbackUS: value.GetPreconditionReadbackUs(),
		CachePreconditionUS:    value.GetCachePreconditionUs(),
		SouthboundQueueWaitUS:  value.GetSouthboundQueueWaitUs(),
		WriteVerification:      value.GetWriteVerification(),
		ReadbackSource:         value.GetReadbackSource(),
	}
}
