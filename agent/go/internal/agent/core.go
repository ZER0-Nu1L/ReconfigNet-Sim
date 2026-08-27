package agent

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/reconfig-net-sim/ocs-go-agent/internal/apierr"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/backend"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/model"
	"google.golang.org/grpc/codes"
)

const maxDelayUS = 1_000_000

type Timing struct {
	Strategy               string `json:"strategy"`
	Transport              string `json:"transport"`
	QueueWaitUS            uint64 `json:"queue_wait_us"`
	ValidationUS           uint64 `json:"validation_us"`
	PlanningUS             uint64 `json:"planning_us"`
	DeleteCommitUS         uint64 `json:"delete_commit_us"`
	RequestedGapUS         uint64 `json:"requested_gap_us"`
	ActualGapUS            uint64 `json:"actual_gap_us"`
	InstallCommitUS        uint64 `json:"install_commit_us"`
	ReadbackUS             uint64 `json:"readback_us"`
	RollbackUS             uint64 `json:"rollback_us"`
	ProgrammingTotalUS     uint64 `json:"programming_total_us"`
	ServerTotalUS          uint64 `json:"server_total_us"`
	DeleteEntries          uint32 `json:"delete_entries"`
	InsertEntries          uint32 `json:"insert_entries"`
	UnchangedEntries       uint32 `json:"unchanged_entries"`
	ActiveEntries          uint32 `json:"active_entries"`
	DeviceWriteRequests    uint32 `json:"device_write_requests"`
	DeviceWorkerRPCUS      uint64 `json:"device_worker_rpc_us"`
	DeviceWorkerTotalUS    uint64 `json:"device_worker_total_us"`
	PreconditionReadbackUS uint64 `json:"precondition_readback_us"`
	LeaseRevisionCheckUS   uint64 `json:"lease_revision_check_us"`
	CachePreconditionUS    uint64 `json:"cache_precondition_us"`
	SouthboundQueueWaitUS  uint64 `json:"southbound_queue_wait_us"`
	WriteVerification      string `json:"write_verification"`
	ReadbackSource         string `json:"readback_source"`
}

type DeviceState struct {
	ConsistencyMode     string `json:"consistency_mode"`
	CacheStatus         string `json:"cache_status"`
	Generation          uint64 `json:"generation"`
	LastVerifiedUnixNS  uint64 `json:"last_verified_unix_ns"`
	LastReconcileUnixNS uint64 `json:"last_reconcile_unix_ns"`
	DriftCount          uint64 `json:"drift_count"`
	WriteVerification   string `json:"write_verification"`
	ReadbackSource      string `json:"readback_source"`
	LastWriteAckUnixNS  uint64 `json:"last_write_ack_unix_ns"`
}

type ControlLease struct {
	LeaseToken    string `json:"lease_token"`
	LeaseEpoch    uint64 `json:"lease_epoch"`
	ExpiresUnixNS uint64 `json:"expires_unix_ns"`
	Revision      uint64 `json:"revision"`
}

type ControlState struct {
	Active        bool   `json:"active"`
	ClientID      string `json:"client_id"`
	LeaseEpoch    uint64 `json:"lease_epoch"`
	ExpiresUnixNS uint64 `json:"expires_unix_ns"`
	Revision      uint64 `json:"revision"`
}

type ConnectionState struct {
	Name          string `json:"-"`
	Bidirectional bool   `json:"-"`
	NearPortName  string `json:"-"`
	FarPortName   string `json:"-"`
	Status        string `json:"-"`
}

func (c ConnectionState) MarshalJSON() ([]byte, error) {
	config := map[string]any{
		"connection-name": c.Name,
		"bidirectional":   c.Bidirectional,
		"near-port-name":  c.NearPortName,
		"far-port-name":   c.FarPortName,
	}
	state := maps.Clone(config)
	state["status"] = c.Status
	return json.Marshal(map[string]any{
		"connection-name": c.Name,
		"config":          config,
		"state":           state,
	})
}

type Snapshot struct {
	Profile             string               `json:"profile"`
	Status              string               `json:"status"`
	State               string               `json:"state"`
	Mode                string               `json:"mode"`
	Revision            uint64               `json:"revision"`
	RequestID           uint64               `json:"request_id"`
	ActiveEntries       uint32               `json:"active_entries"`
	Connections         []ConnectionState    `json:"connections"`
	BackendCapabilities backend.Capabilities `json:"backend_capabilities"`
	LastTiming          *Timing              `json:"last_timing,omitempty"`
	LastError           string               `json:"last_error,omitempty"`
	DeviceState         DeviceState          `json:"device_state"`
	ControlState        ControlState         `json:"control_state"`
}

type Result struct {
	Status                string `json:"status"`
	Result                string `json:"result"`
	RequestID             uint64 `json:"request_id"`
	RequestReceivedUnixNS uint64 `json:"request_received_unix_ns"`
	Revision              uint64 `json:"revision"`
	State                 string `json:"state"`
	Mode                  string `json:"mode"`
	ActiveEntries         uint32 `json:"active_entries"`
	Timing                Timing `json:"timing"`
}

type ConnectionOperation struct {
	Kind       string
	Name       string
	Connection model.Connection
	All        *model.ConnectionSet
}

type Agent struct {
	inventory               model.Inventory
	backend                 *backend.Client
	profile                 string
	capabilities            backend.Capabilities
	consistencyMode         string
	leaseDuration           time.Duration
	reconcileInterval       time.Duration
	startupPolicy           string
	startupRecoveryRequired bool

	commit chan struct{}
	mu     sync.RWMutex
	state  runtimeState
}

type runtimeState struct {
	connections   model.ConnectionSet
	mode          string
	status        string
	revision      uint64
	requestID     uint64
	lastTiming    *Timing
	lastError     string
	leaseToken    string
	leaseClientID string
	leaseEpoch    uint64
	leaseExpires  time.Time
}

func New(
	ctx context.Context,
	inventory model.Inventory,
	initial model.ConnectionSet,
	deviceBackend *backend.Client,
	profile string,
	consistencyMode string,
	leaseDuration time.Duration,
	reconcileInterval time.Duration,
	startupPolicy string,
) (*Agent, error) {
	if startupPolicy != "REQUIRE_MATCH" && startupPolicy != "REAPPLY_DESIRED" {
		return nil, fmt.Errorf("startup policy must be REQUIRE_MATCH or REAPPLY_DESIRED")
	}
	agent := &Agent{
		inventory:         inventory,
		backend:           deviceBackend,
		profile:           profile,
		capabilities:      deviceBackend.Capabilities(),
		consistencyMode:   consistencyMode,
		leaseDuration:     leaseDuration,
		reconcileInterval: reconcileInterval,
		startupPolicy:     startupPolicy,
		commit:            make(chan struct{}, 1),
		state: runtimeState{
			connections: initial,
			mode:        "ocs",
			status:      "updating",
		},
	}
	observed, err := deviceBackend.Read(ctx)
	if err != nil {
		return nil, fmt.Errorf("read initial device state: %w", err)
	}
	desired := initial.Pairs()
	if maps.Equal(observed, desired) {
		timing := Timing{
			Strategy: "FULL", Transport: "SEQUENTIAL",
			UnchangedEntries:  uint32(len(desired)),
			ActiveEntries:     uint32(len(desired)),
			WriteVerification: "STARTUP_MATCH",
		}
		agent.state.status = "ready"
		agent.state.lastTiming = &timing
	} else if startupPolicy == "REQUIRE_MATCH" {
		timing := Timing{
			Strategy: "FULL", Transport: "SEQUENTIAL",
			ActiveEntries:     uint32(len(observed)),
			WriteVerification: "STARTUP_MISMATCH",
		}
		agent.state.status = "error"
		agent.state.lastError = "device state does not match YAML desired state"
		agent.state.lastTiming = &timing
		agent.startupRecoveryRequired = true
	} else {
		backendTiming, applyErr := deviceBackend.Apply(
			ctx, observed, desired, "FULL", "SEQUENTIAL", 0)
		if applyErr != nil {
			return nil, fmt.Errorf("initialize device state: %w", applyErr)
		}
		timing := timingFromBackend(backendTiming)
		agent.state.status = "ready"
		agent.state.lastTiming = &timing
	}
	go agent.reconcileLoop(ctx)
	return agent, nil
}

func (a *Agent) Inventory() model.Inventory {
	return a.inventory
}

func (a *Agent) Snapshot() Snapshot {
	a.mu.RLock()
	defer a.mu.RUnlock()
	return a.snapshotLocked()
}

func (a *Agent) snapshotLocked() Snapshot {
	connections := make([]ConnectionState, 0, len(a.state.connections.Connections()))
	for _, connection := range a.state.connections.Connections() {
		connections = append(connections, ConnectionState{
			Name:          connection.Name,
			Bidirectional: connection.Bidirectional,
			NearPortName:  connection.NearPortName,
			FarPortName:   connection.FarPortName,
			Status:        connectionStatus(a.state.status, a.state.mode),
		})
	}
	var lastTiming *Timing
	if a.state.lastTiming != nil {
		copy := *a.state.lastTiming
		lastTiming = &copy
	}
	return Snapshot{
		Profile:             a.profile,
		Status:              a.state.status,
		State:               a.state.status,
		Mode:                a.state.mode,
		Revision:            a.state.revision,
		RequestID:           a.state.requestID,
		ActiveEntries:       uint32(len(a.currentPairsLocked())),
		Connections:         connections,
		BackendCapabilities: a.capabilities,
		LastTiming:          lastTiming,
		LastError:           a.state.lastError,
		DeviceState:         a.deviceStateLocked(),
		ControlState:        a.controlStateLocked(),
	}
}

func (a *Agent) deviceStateLocked() DeviceState {
	state := a.backend.DeviceState()
	if a.startupRecoveryRequired {
		state.CacheStatus = "DRIFTED"
	}
	return DeviceState{
		ConsistencyMode:     a.consistencyMode,
		CacheStatus:         state.CacheStatus,
		Generation:          state.Generation,
		LastVerifiedUnixNS:  state.LastVerifiedUnixNS,
		LastReconcileUnixNS: state.LastReconcileUnixNS,
		DriftCount:          state.DriftCount,
		WriteVerification:   state.WriteVerification,
		ReadbackSource:      state.ReadbackSource,
		LastWriteAckUnixNS:  state.LastWriteAckUnixNS,
	}
}

func (a *Agent) expireLeaseLocked(now time.Time) {
	if a.state.leaseToken != "" && !now.Before(a.state.leaseExpires) {
		a.state.leaseToken = ""
		a.state.leaseClientID = ""
		a.state.leaseExpires = time.Time{}
	}
}

func (a *Agent) controlStateLocked() ControlState {
	a.expireLeaseLocked(time.Now())
	expires := uint64(0)
	if !a.state.leaseExpires.IsZero() {
		expires = uint64(a.state.leaseExpires.UnixNano())
	}
	return ControlState{
		Active: a.state.leaseToken != "", ClientID: a.state.leaseClientID,
		LeaseEpoch: a.state.leaseEpoch, ExpiresUnixNS: expires,
		Revision: a.state.revision,
	}
}

func (a *Agent) ControlState() ControlState {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.controlStateLocked()
}

func (a *Agent) leaseDurationFor(requested uint32) time.Duration {
	if requested == 0 {
		return a.leaseDuration
	}
	duration := time.Duration(requested) * time.Second
	if duration > a.leaseDuration {
		return a.leaseDuration
	}
	return duration
}

func (a *Agent) leaseReplyLocked() ControlLease {
	return ControlLease{
		LeaseToken: a.state.leaseToken, LeaseEpoch: a.state.leaseEpoch,
		ExpiresUnixNS: uint64(a.state.leaseExpires.UnixNano()),
		Revision:      a.state.revision,
	}
}

func (a *Agent) AcquireControl(clientID string, requested uint32) (ControlLease, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.expireLeaseLocked(time.Now())
	if a.state.leaseToken != "" {
		return ControlLease{}, apierr.New(codes.ResourceExhausted,
			"control is already held by another writer", map[string]any{
				"client_id": a.state.leaseClientID, "lease_epoch": a.state.leaseEpoch,
				"expires_unix_ns": a.state.leaseExpires.UnixNano(),
			})
	}
	random := make([]byte, 32)
	if _, err := rand.Read(random); err != nil {
		return ControlLease{}, apierr.New(codes.Internal, "generate lease token", nil)
	}
	a.state.leaseEpoch++
	a.state.leaseToken = hex.EncodeToString(random)
	a.state.leaseClientID = clientID
	a.state.leaseExpires = time.Now().Add(a.leaseDurationFor(requested))
	return a.leaseReplyLocked(), nil
}

func (a *Agent) requireLeaseLocked(token string) error {
	a.expireLeaseLocked(time.Now())
	if token == "" || token != a.state.leaseToken {
		return apierr.New(codes.FailedPrecondition,
			"a valid control lease is required", map[string]any{
				"lease_epoch": a.state.leaseEpoch,
			})
	}
	return nil
}

func (a *Agent) RenewControl(token string, requested uint32) (ControlLease, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if err := a.requireLeaseLocked(token); err != nil {
		return ControlLease{}, err
	}
	a.state.leaseExpires = time.Now().Add(a.leaseDurationFor(requested))
	return a.leaseReplyLocked(), nil
}

func (a *Agent) ReleaseControl(token string) (ControlState, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if err := a.requireLeaseLocked(token); err != nil {
		return ControlState{}, err
	}
	a.state.leaseToken = ""
	a.state.leaseClientID = ""
	a.state.leaseExpires = time.Time{}
	return a.controlStateLocked(), nil
}

func (a *Agent) Permutation() ([]uint32, error) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	if a.state.mode != "ocs" {
		return nil, apierr.New(
			codes.FailedPrecondition,
			"permutation is unavailable while debug mode is active", nil)
	}
	permutation, err := a.state.connections.Permutation()
	if err != nil {
		return nil, apierr.New(codes.FailedPrecondition, err.Error(), map[string]any{
			"connected_ports": len(a.state.connections.Connections()) * 2,
			"total_ports":     a.inventory.Len(),
		})
	}
	return permutation, nil
}

func (a *Agent) Connections() model.ConnectionSet {
	a.mu.RLock()
	defer a.mu.RUnlock()
	connections, _ := model.NewConnectionSet(a.inventory, a.state.connections.Connections())
	return connections
}

func (a *Agent) ApplyPermutation(
	ctx context.Context,
	permutation []uint32,
	strategy string,
	transport string,
	delayUS uint64,
	expectedRevision *uint64,
	leaseToken string,
) (Result, error) {
	validationStarted := time.Now()
	target, err := model.FromPermutation(a.inventory, permutation)
	if err != nil {
		return Result{}, apierr.New(codes.InvalidArgument, err.Error(), nil)
	}
	validationUS := uint64(time.Since(validationStarted).Microseconds())
	return a.applyConnections(
		ctx, target, strategy, transport, delayUS, expectedRevision, leaseToken,
		validationUS, false)
}

func (a *Agent) ReplaceConnections(
	ctx context.Context,
	connections []model.Connection,
	strategy string,
	transport string,
	delayUS uint64,
	expectedRevision *uint64,
	leaseToken string,
) (Result, error) {
	validationStarted := time.Now()
	target, err := model.NewConnectionSet(a.inventory, connections)
	if err != nil {
		return Result{}, modelError(err)
	}
	validationUS := uint64(time.Since(validationStarted).Microseconds())
	return a.applyConnections(
		ctx, target, strategy, transport, delayUS, expectedRevision, leaseToken,
		validationUS, false)
}

func (a *Agent) ApplyConnectionOperations(
	ctx context.Context,
	operations []ConnectionOperation,
	expectedRevision *uint64,
	leaseToken string,
) (Result, error) {
	queueStarted := time.Now()
	if err := a.acquire(ctx); err != nil {
		return Result{}, err
	}
	defer a.release()
	queueWaitUS := uint64(time.Since(queueStarted).Microseconds())
	validationStarted := time.Now()

	a.mu.RLock()
	current := a.state.connections
	mode := a.state.mode
	a.mu.RUnlock()
	if mode != "ocs" {
		return Result{}, apierr.New(
			codes.FailedPrecondition,
			"connections cannot be changed while debug mode is active", nil)
	}
	target := current
	for _, operation := range operations {
		var err error
		switch operation.Kind {
		case "delete":
			target, err = target.Delete(operation.Name)
		case "replace":
			target, err = target.Replace(operation.Connection)
		case "replace_all":
			if operation.All == nil {
				err = fmt.Errorf("replace_all requires a connection set")
			} else {
				target = *operation.All
			}
		default:
			err = fmt.Errorf("unsupported connection operation %s", operation.Kind)
		}
		if err != nil {
			return Result{}, modelError(err)
		}
	}
	validationUS := uint64(time.Since(validationStarted).Microseconds())
	return a.applyConnectionsAcquired(
		ctx, target, "DELTA", "SEQUENTIAL", 0, expectedRevision, leaseToken,
		validationUS, queueWaitUS, false)
}

func (a *Agent) SetMode(
	ctx context.Context,
	mode string,
	delayUS uint64,
	transport string,
	expectedRevision *uint64,
	leaseToken string,
) (Result, error) {
	if mode != "ocs" && mode != "debug" {
		return Result{}, apierr.New(
			codes.InvalidArgument, "mode must be ocs or debug", nil)
	}
	queueStarted := time.Now()
	if err := a.acquire(ctx); err != nil {
		return Result{}, err
	}
	defer a.release()
	queueWaitUS := uint64(time.Since(queueStarted).Microseconds())
	a.mu.RLock()
	target := a.state.connections
	a.mu.RUnlock()
	return a.applyConnectionsAcquired(
		ctx, target, "FULL", transport, delayUS, expectedRevision, leaseToken,
		0, queueWaitUS, true, mode)
}

func (a *Agent) applyConnections(
	ctx context.Context,
	target model.ConnectionSet,
	strategy string,
	transport string,
	delayUS uint64,
	expectedRevision *uint64,
	leaseToken string,
	validationUS uint64,
	modeChange bool,
) (Result, error) {
	queueStarted := time.Now()
	if err := a.acquire(ctx); err != nil {
		return Result{}, err
	}
	defer a.release()
	queueWaitUS := uint64(time.Since(queueStarted).Microseconds())
	return a.applyConnectionsAcquired(
		ctx, target, strategy, transport, delayUS, expectedRevision, leaseToken,
		validationUS, queueWaitUS, modeChange)
}

func (a *Agent) applyConnectionsAcquired(
	ctx context.Context,
	target model.ConnectionSet,
	strategy string,
	transport string,
	delayUS uint64,
	expectedRevision *uint64,
	leaseToken string,
	validationUS uint64,
	queueWaitUS uint64,
	modeChange bool,
	targetModeOptional ...string,
) (Result, error) {
	started := time.Now()
	received := uint64(time.Now().UnixNano())
	if err := validateExecution(strategy, transport, delayUS, a.capabilities); err != nil {
		return Result{}, err
	}

	a.mu.Lock()
	a.state.requestID++
	requestID := a.state.requestID
	preconditionStarted := time.Now()
	if err := a.requireLeaseLocked(leaseToken); err != nil {
		a.mu.Unlock()
		apiError := apierr.As(err)
		apiError.RequestID = requestID
		return Result{}, apiError
	}
	if expectedRevision == nil {
		a.mu.Unlock()
		err := apierr.New(codes.FailedPrecondition,
			"expected_revision is required for write operations", nil)
		err.RequestID = requestID
		return Result{}, err
	}
	if *expectedRevision != a.state.revision {
		currentRevision := a.state.revision
		a.mu.Unlock()
		err := apierr.New(codes.Aborted, fmt.Sprintf(
			"expected revision %d but current revision is %d",
			*expectedRevision, currentRevision), map[string]any{
			"expected_revision": *expectedRevision,
			"current_revision":  currentRevision,
		})
		err.RequestID = requestID
		return Result{}, err
	}
	deviceState := a.backend.DeviceState()
	if a.startupRecoveryRequired {
		a.mu.Unlock()
		err := apierr.New(codes.FailedPrecondition,
			"startup device mismatch requires RecoverDeviceState", nil)
		err.RequestID = requestID
		return Result{}, err
	}
	if deviceState.CacheStatus != "READY" {
		a.mu.Unlock()
		err := apierr.New(codes.FailedPrecondition,
			fmt.Sprintf("device cache is %s; recover device state first", deviceState.CacheStatus),
			map[string]any{"cache_status": deviceState.CacheStatus,
				"generation": deviceState.Generation})
		err.RequestID = requestID
		return Result{}, err
	}
	leaseRevisionCheckUS := uint64(time.Since(preconditionStarted).Microseconds())
	if !modeChange && a.state.mode != "ocs" {
		a.mu.Unlock()
		err := apierr.New(
			codes.FailedPrecondition,
			"connections cannot be changed while debug mode is active", nil)
		err.RequestID = requestID
		return Result{}, err
	}
	targetMode := a.state.mode
	if len(targetModeOptional) == 1 {
		targetMode = targetModeOptional[0]
	}
	unchanged := target.Equal(a.state.connections) && targetMode == a.state.mode
	if unchanged {
		timing := Timing{
			Strategy:             strategy,
			Transport:            transport,
			QueueWaitUS:          queueWaitUS,
			ValidationUS:         validationUS,
			UnchangedEntries:     uint32(len(a.currentPairsLocked())),
			ActiveEntries:        uint32(len(a.currentPairsLocked())),
			LeaseRevisionCheckUS: leaseRevisionCheckUS,
		}
		timing.ServerTotalUS = uint64(time.Since(started).Microseconds()) + queueWaitUS + validationUS
		result := a.resultLocked(requestID, received, "unchanged", timing)
		a.mu.Unlock()
		return result, nil
	}
	previousPairs := maps.Clone(a.currentPairsLocked())
	targetPairs := target.Pairs()
	if targetMode == "debug" {
		targetPairs = model.AllToAllPairs(a.inventory)
	}
	a.state.status = "updating"
	a.mu.Unlock()

	backendTiming, backendErr := a.backend.Apply(
		ctx, previousPairs, targetPairs, strategy, transport, delayUS)
	timing := timingFromBackend(backendTiming)
	timing.QueueWaitUS = queueWaitUS
	timing.ValidationUS = validationUS
	timing.LeaseRevisionCheckUS = leaseRevisionCheckUS
	timing.ServerTotalUS = uint64(time.Since(started).Microseconds()) + queueWaitUS + validationUS

	a.mu.Lock()
	defer a.mu.Unlock()
	if backendErr != nil {
		return Result{}, a.backendErrorLocked(requestID, timing, backendErr)
	}
	a.state.connections = target
	a.state.mode = targetMode
	a.state.revision++
	a.state.status = "ready"
	a.state.lastError = ""
	a.state.lastTiming = &timing
	return a.resultLocked(requestID, received, "updated", timing), nil
}

func (a *Agent) backendErrorLocked(requestID uint64, timing Timing, err error) error {
	var transition *backend.TransitionError
	if errors.As(err, &transition) {
		if transition.Code == "FAILED_PRECONDITION" {
			a.state.status = "error"
			a.state.lastError = transition.Message
			apiError := apierr.New(codes.FailedPrecondition,
				transition.Message, map[string]any{
					"backend":      a.capabilities.Backend,
					"cache_status": a.backend.DeviceState().CacheStatus,
				})
			apiError.RequestID = requestID
			apiError.Timing = timing
			return apiError
		}
		a.state.status = "ready"
		code := codes.Aborted
		message := "update failed and previous connections were restored: " + transition.Message
		if !transition.Restored {
			a.state.status = "error"
			code = codes.Internal
			message = "update failed and rollback failed: " + transition.Error()
		}
		a.state.lastError = transition.Message
		apiError := apierr.New(code, message, map[string]any{
			"backend": a.capabilities.Backend,
		})
		apiError.RequestID = requestID
		apiError.Timing = timing
		apiError.Restored = transition.Restored
		return apiError
	}
	a.state.status = "error"
	a.state.lastError = err.Error()
	apiError := apierr.New(
		codes.Unavailable,
		"device worker is unavailable; device state is unknown",
		map[string]any{"backend": a.capabilities.Backend})
	apiError.RequestID = requestID
	apiError.Timing = timing
	return apiError
}

func (a *Agent) reconcileLoop(ctx context.Context) {
	ticker := time.NewTicker(a.reconcileInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			_, _ = a.ReconcileDeviceState(ctx)
		}
	}
}

func (a *Agent) ReconcileDeviceState(ctx context.Context) (DeviceState, error) {
	if err := a.acquire(ctx); err != nil {
		return DeviceState{}, err
	}
	defer a.release()
	a.mu.RLock()
	desired := maps.Clone(a.currentPairsLocked())
	a.mu.RUnlock()
	state, observed, err := a.backend.Reconcile(ctx, desired)
	a.mu.Lock()
	defer a.mu.Unlock()
	if err != nil {
		a.state.status = "error"
		a.state.lastError = err.Error()
		return a.deviceStateLocked(), err
	}
	if state.CacheStatus != "READY" || !maps.Equal(observed, desired) {
		a.state.status = "error"
		a.state.lastError = "device drift detected"
	} else if !a.startupRecoveryRequired && a.state.lastError == "device drift detected" {
		a.state.status = "ready"
		a.state.lastError = ""
	}
	return a.deviceStateLocked(), nil
}

func (a *Agent) RecoverDeviceState(
	ctx context.Context,
	expectedRevision *uint64,
	leaseToken string,
) (Result, error) {
	queueStarted := time.Now()
	if err := a.acquire(ctx); err != nil {
		return Result{}, err
	}
	defer a.release()
	queueWaitUS := uint64(time.Since(queueStarted).Microseconds())
	started := time.Now()
	received := uint64(time.Now().UnixNano())
	a.mu.Lock()
	a.state.requestID++
	requestID := a.state.requestID
	if err := a.requireLeaseLocked(leaseToken); err != nil {
		a.mu.Unlock()
		apiError := apierr.As(err)
		apiError.RequestID = requestID
		return Result{}, apiError
	}
	if expectedRevision == nil {
		a.mu.Unlock()
		err := apierr.New(codes.FailedPrecondition,
			"expected_revision is required for RecoverDeviceState", nil)
		err.RequestID = requestID
		return Result{}, err
	}
	if *expectedRevision != a.state.revision {
		current := a.state.revision
		a.mu.Unlock()
		err := apierr.New(codes.Aborted,
			fmt.Sprintf("expected revision %d but current revision is %d", *expectedRevision, current),
			map[string]any{"expected_revision": *expectedRevision, "current_revision": current})
		err.RequestID = requestID
		return Result{}, err
	}
	desired := maps.Clone(a.currentPairsLocked())
	a.state.status = "updating"
	a.mu.Unlock()
	backendTiming, backendErr := a.backend.Recover(
		ctx, desired, "FULL", "SEQUENTIAL", 0)
	timing := timingFromBackend(backendTiming)
	timing.QueueWaitUS = queueWaitUS
	timing.ServerTotalUS = uint64(time.Since(started).Microseconds()) + queueWaitUS
	a.mu.Lock()
	defer a.mu.Unlock()
	if backendErr != nil {
		return Result{}, a.backendErrorLocked(requestID, timing, backendErr)
	}
	a.state.revision++
	a.state.status = "ready"
	a.state.lastError = ""
	a.startupRecoveryRequired = false
	a.state.lastTiming = &timing
	return a.resultLocked(requestID, received, "recovered", timing), nil
}

func (a *Agent) resultLocked(requestID, received uint64, result string, timing Timing) Result {
	return Result{
		Status:                "success",
		Result:                result,
		RequestID:             requestID,
		RequestReceivedUnixNS: received,
		Revision:              a.state.revision,
		State:                 a.state.status,
		Mode:                  a.state.mode,
		ActiveEntries:         uint32(len(a.currentPairsLocked())),
		Timing:                timing,
	}
}

func (a *Agent) acquire(ctx context.Context) error {
	select {
	case a.commit <- struct{}{}:
		return nil
	case <-ctx.Done():
		return apierr.New(codes.Canceled, ctx.Err().Error(), nil)
	}
}

func (a *Agent) release() {
	<-a.commit
}

func (a *Agent) currentPairsLocked() map[model.Pair]struct{} {
	if a.state.mode == "debug" {
		return model.AllToAllPairs(a.inventory)
	}
	return a.state.connections.Pairs()
}

func validateExecution(
	strategy, transport string,
	delayUS uint64,
	capabilities backend.Capabilities,
) error {
	if strategy != "FULL" && strategy != "DELTA" {
		return apierr.New(codes.InvalidArgument, "strategy must be FULL or DELTA", nil)
	}
	if !slices.Contains(capabilities.Transports, transport) {
		return apierr.New(codes.Unimplemented,
			fmt.Sprintf("transport %s is not supported by this backend", transport), nil)
	}
	if delayUS > maxDelayUS {
		return apierr.New(codes.InvalidArgument,
			fmt.Sprintf("delay_us must be between 0 and %d", maxDelayUS), nil)
	}
	return nil
}

func modelError(err error) error {
	var conflict *model.ConflictError
	if errors.As(err, &conflict) {
		return apierr.New(codes.FailedPrecondition, err.Error(), map[string]any{
			"port_name":       conflict.Port,
			"connection_name": conflict.Connection,
		})
	}
	if _, ok := strings.CutPrefix(err.Error(), "unknown connection "); ok {
		return apierr.New(codes.FailedPrecondition, err.Error(), nil)
	}
	return apierr.New(codes.InvalidArgument, err.Error(), nil)
}

func timingFromBackend(value backend.Timing) Timing {
	return Timing{
		Strategy:               value.Strategy,
		Transport:              value.Transport,
		PlanningUS:             value.PlanningUS,
		DeleteCommitUS:         value.DeleteCommitUS,
		RequestedGapUS:         value.RequestedGapUS,
		ActualGapUS:            value.ActualGapUS,
		InstallCommitUS:        value.InstallCommitUS,
		ReadbackUS:             value.ReadbackUS,
		RollbackUS:             value.RollbackUS,
		ProgrammingTotalUS:     value.ProgrammingTotalUS,
		DeleteEntries:          value.DeleteEntries,
		InsertEntries:          value.InsertEntries,
		UnchangedEntries:       value.UnchangedEntries,
		ActiveEntries:          value.ActiveEntries,
		DeviceWriteRequests:    value.DeviceWriteRequests,
		DeviceWorkerRPCUS:      value.DeviceWorkerRPCUS,
		DeviceWorkerTotalUS:    value.DeviceWorkerTotalUS,
		PreconditionReadbackUS: value.PreconditionReadbackUS,
		CachePreconditionUS:    value.CachePreconditionUS,
		SouthboundQueueWaitUS:  value.SouthboundQueueWaitUS,
		WriteVerification:      value.WriteVerification,
		ReadbackSource:         value.ReadbackSource,
	}
}

func connectionStatus(status, mode string) string {
	if status == "error" {
		return "FAILED"
	}
	if mode != "ocs" {
		return "UNKNOWN"
	}
	return "CONNECTED"
}
