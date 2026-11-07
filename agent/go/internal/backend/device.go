package backend

import (
	"context"

	"github.com/reconfig-net-sim/ocs-go-agent/internal/model"
)

// Device is the southbound device the Agent owns.
//
// Two implementations exist: the split profile talks to the Python device
// worker over gRPC (Client), and the
// direct profile owns the bf_switchd BFRT connection itself (Direct), so the
// online path has no process hop.
type Device interface {
	// Close releases the device connection and its client id.
	Close() error
	// Capabilities reports what the device can do.
	Capabilities() Capabilities
	// DeviceState reports the cache/precondition state of the device.
	DeviceState() DeviceState
	// Read returns the mapping the device currently holds.
	Read(ctx context.Context) (map[model.Pair]struct{}, error)
	// Apply transitions the device from previous to target. Implementations
	// must reject the request when the device does not hold previous.
	Apply(
		ctx context.Context,
		previous map[model.Pair]struct{},
		target map[model.Pair]struct{},
		strategy string,
		transport string,
		delayUS uint64,
	) (Timing, error)
	// Reconcile re-reads the device and reports drift.
	Reconcile(
		ctx context.Context,
		desired map[model.Pair]struct{},
	) (DeviceState, map[model.Pair]struct{}, error)
	// Recover rewrites the device to desired without preconditions.
	Recover(
		ctx context.Context,
		desired map[model.Pair]struct{},
		strategy string,
		transport string,
		delayUS uint64,
	) (Timing, error)
}
