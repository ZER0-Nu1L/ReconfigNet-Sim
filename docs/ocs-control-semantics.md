# OCS control semantics

This repository implements an IPv4/MAC packet-level simulation of an optical circuit switch. It is not a transparent optical OCS: the P4 pipeline parses packets, decrements TTL, recomputes the IPv4 header checksum and applies endpoint forwarding entries.

The higher-level project motivation, assumptions and physical boundary are documented in [OCS simulation principles and boundaries](./ocs-simulation-principles-and-boundaries.md).

> [!IMPORTANT]
> A successful southbound ACK or software readback confirms a control-plane boundary only. It does not mean that a physical optical path has switched, a link has recovered or packets are already flowing on the new path.

## State representations

`ConnectionSet` is the authoritative agent representation. It contains named, bidirectional point-to-point connections and may be sparse: unused ports are legal.

`pi` is a compact batch representation for a complete fixed-point-free symmetric permutation. Every active port appears exactly once and applying the mapping twice returns the source slot. A sparse `ConnectionSet` cannot be represented as `pi`; in that case `GetPermutation` fails with `FAILED_PRECONDITION` and HTTP `GET /ocs_mapping` returns 409.

Debug Mode installs every non-self source/destination pair. Full connectivity is diagnostic behavior and is neither a `ConnectionSet` matching nor a valid `pi`; it remains limited to the supported IPv4/MAC packet pipeline. Mode transitions, retained desired state, API errors and capacity requirements are defined in [Debug Mode](./debug-mode.md).

## Controller and agent boundary

The controller produces connection intent, chooses Full or Delta execution, acquires the single active-writer control lease and attaches the current expected revision to every write. It addresses stable logical names such as `port-1`; it does not program BMv2 or future Tofino port identifiers directly.

The OCS Agent Core owns semantic validation, the desired/observed snapshot, revision, request IDs and the single-writer commit path. The two supported implementations preserve the same semantics but have different boundaries: `python-monolith-http-direct` calls the backend in process, while `go-split-grpc` calls a Python Device Worker through the `DeviceBackend` gRPC contract over a Unix Domain Socket. The Worker owns P4Runtime/BFRT writes, readback and rollback.

HTTP does not maintain an independent legacy mapping or revision. It calls the Python Core with the same model, lease and transaction rules implemented by the Go typed NBI. Python split, Python gRPC NBI and Go HTTP NBI are historical implementations and are not deployment options.

The default lease lasts 30 seconds and should normally be renewed every 10 seconds. HTTP never silently acquires a lease. A missing, expired or wrong token fails before device programming; a stale revision returns `ABORTED`. Once a transaction has passed the checks and owns the commit slot, it is allowed to complete even if the lease expires during device execution.

## Write and failure semantics

All mutations are serialized for the complete apply/readback/rollback interval. A queued operation constructs and validates its target against the latest committed state after obtaining the commit lock. Reads and request decoding may proceed on gRPC worker threads, but the P4app profile does not claim port-level parallel device commits.

An unchanged request is idempotent: it does not clear entries or increment revision. A successful changed request increments revision after the selected consistency boundary succeeds: southbound ACK for `CACHED_ACK`, or complete target readback for `CACHED_SYNC` and `STRICT_DEVICE`.

If programming or readback fails, the backend attempts to restore the previous entries and verifies the restoration. A restored transaction returns `ABORTED`; a rollback failure returns `INTERNAL` and leaves runtime status in error. This provides control-plane transaction behavior, not instantaneous data-plane atomicity.

Conflicting port ownership returns `FAILED_PRECONDITION` with the port and owning connection. The agent never silently tears down another named connection to satisfy a new request.

## Full, Delta and transport

Both strategies use break-before-make for entries that change:

- Full removes all active entries, waits for the optional `delay_us`, then installs the complete target.
- Delta removes only changed or conflicting entries, waits for the optional gap, installs additions and preserves unchanged entries.

`delay_us` simulates a physical reconfiguration gap; it is not a measurement of optical hardware timing.

Sequential transport issues one device write per entry and is the required baseline. Native Batch places multiple P4Runtime updates in one request and is exposed only when the running backend reports that capability. Neither transport currently claims data-plane atomic switching.

Every operation reports validation, planning, queue, delete, gap, install, readback, rollback and total timing together with entry and device-request counts.

## Configuration and observed truth

The YAML model defines logical ports and initial named connections. The YANG files define the experimental local Draft schema profile. The capability YAML states which Draft areas are supported, derived, planned, unsupported or out of scope.

The P4app profile declares Draft reboot recovery as `NO_RECOVERY`. On startup the agent reads the existing OCS table and fully replaces it with the YAML initial connection set; it does not adopt stale device entries as recovered controller configuration.

P4App defaults to `CACHED_SYNC`; Tofino defaults to `CACHED_ACK`. In the split profile, the Device Worker checks its generation and cached entries in memory before each write. `CACHED_SYNC` performs synchronous readback after each changed write, while `CACHED_ACK` relies on the southbound ACK and later reconciliation. Both reconcile against the device periodically. External changes produce `DRIFTED`; a lost Worker or unverifiable rollback produces `UNKNOWN`. Neither state is silently adopted. A lease holder must explicitly call `RecoverDeviceState(REAPPLY_DESIRED)` to restore the desired state. `STRICT_DEVICE` remains available when a device pre-read is required for every write.

P4app reports `TUNED`, `CONNECTED`, peer and connected state only after table readback matches the target. These values are derived state; they do not prove optical power, MEMS position, BER or physical link health. Unsupported counters are omitted rather than synthesized from unrelated packet counters.

Unsupported model fields and RPC features fail explicitly. The P4app profile does not silently accept recovery configuration, multicast, SOA, port metadata writes, gNMI Subscribe, leaf-level update or unknown YAML fields.

> [!WARNING]
> Unsupported or out-of-scope model fields must remain explicit failures. Do not make a request appear successful by silently ignoring fields that the current profile cannot enforce.

`FakeSwitch` and in-memory test doubles are used only for deterministic unit tests and failure injection. They are not an inter-process production layer and do not provide authoritative performance results; those require the real BMv2/P4Runtime closed loop.

## Testbed-specific constraints

ARP is not forwarded by the current pipeline, so experiments may need static neighbors. That is a testbed constraint, not a property of a real OCS. Site addresses, MACs, physical ports and controller listen addresses do not belong in the portable model; Tofino startup continues to require an explicit deployment profile.

The northbound model, transaction semantics and logical port names are shared by the P4App and Tofino backends. Tofino-specific BFRT objects and `dev_port` values stay behind its backend adapter.
