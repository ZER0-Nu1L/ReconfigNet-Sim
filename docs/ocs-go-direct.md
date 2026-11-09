# Reusable Go device service for Tofino experiments

Status: baseline implementation for low-latency experiments. Software tests exercise
a fake BFRT service; the pinned model also checks the full Agent path.
Performance qualification and long-running acceptance are deployment-specific.
Historical local benchmark numbers do not describe an external-host path.

For a write benchmark limited to two reviewed matchings, supply both
`--expected-initial-pi` and `--alternate-pi` to `ocs-benchmark`, with
`--operation write --concurrency 1 --raw-samples`. Values are comma-separated
logical port permutations with symmetric non-self pairs. The benchmark checks
the initial mapping before acquiring control and again after acquiring the
lease, before any setup write. It alternates only those two matchings and restores
the initial mapping on normal completion. An uncertain write stops the run
without automatic replay or restoration; inspect hardware and recover explicitly.
The JSON records the selected mappings, compact-reply setting and successful
write targets. Omitting these flags retains automatic matching selection for
existing model experiments; it is unsuitable for a restricted physical window.

## Execution path

An experiment runs its client on an external host and submits connection goals:

```text
experiment → OcsOperations → Go Agent Core → Go BFRT client → bf_switchd
             gRPC or framed    lease / revision / serialized state
```

The `go-direct-bfrt` profile places device service and BFRT adaptation in one Go
process. It connects to an already running `bf_switchd`; it does not load P4,
initialize ports or replace the vendor runtime. It supports the existing
two-key `ocs_mapping` / `permit_ocs` table. Single-key layouts need a separate
adapter and are rejected by field discovery.

gRPC/gNMI and the optional framed OcsOperations listener share one Core and
authority. The framed endpoint supports unary OcsOperations, not gNMI or streams.
Both listeners use the existing trusted management network without TLS. Bind
them to the intended management interface and use the site's access controls.

## Build and configure

From `agent/go`:

```bash
go build -o /absolute/output/ocs-go-agent ./cmd/ocs-go-agent
go build -o /absolute/output/remote-switch ./examples/remote-switch
go build -o /absolute/output/ocs-benchmark ./cmd/ocs-benchmark
go build -o /absolute/output/ocs-reconfig-probe ./cmd/ocs-reconfig-probe
```

Copy `agent/configs/tofino/go-direct-bfrt.example.json` into the deployment
repository and set its model/capability paths, listeners, BFRT endpoint,
client/device/pipe IDs and logical-to-device mapping. Example device ports
1–6 are placeholders. Do not use them as a physical wiring description.

`startup_policy: REQUIRE_MATCH` starts with an error state if observed entries
do not match the model; it does not silently overwrite an active experiment.
The ownership lock must be the same file used by other Agents for that device.
Use distinct locks for distinct devices and one writer per device.

Validate without connecting to BFRT:

```bash
/absolute/output/ocs-go-agent --config /absolute/deployment/agent.json --check-config
```

After arranging the device's experiment window, start the service:

```bash
OCS_GO_AGENT_BINARY=/absolute/output/ocs-go-agent \
  targets/tofino/runtime/run_go_direct.sh /absolute/deployment/agent.json
```

This explicit Go launcher replaces the Python launcher for this candidate. The
existing Python launcher and its two stable profiles retain their existing
configuration schema. CPU frequency and affinity are deployment settings;
record them when comparing performance.

## Connect another experiment

The example imports only public packages and has no benchmark dependency. On a
separate client host, it acquires a lease, reads the original permutation,
applies the requested one, then restores the original using the returned
revision:

```bash
/absolute/output/remote-switch \
  --target framed://DEVICE_MANAGEMENT_HOST:9340 --pi 3,4,1,2,6,5
```

The service's listener must be reachable from that host. `grpc://host:9339` or
plain `host:9339` selects gRPC. The example requests a real connection change;
select a valid permutation and reserve the required resources first.

To embed the client in another Go module, use a version of
`github.com/reconfig-net-sim/ocs-go-agent` containing this candidate, or a local
`replace` pointing to this checkout's `agent/go`. Pin a commit containing this profile; older released module versions may not
include it. Imports are:

```go
import (
    "github.com/reconfig-net-sim/ocs-go-agent/client"
    ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
)
```

`client.Dial(ctx, target)` returns a connection accepted by
`ocsv1.NewOcsOperationsClient`. Use that generated client for AcquireControl,
RenewControl, GetRuntime, GetPermutation, ApplyBatch, RecoverDeviceState and
ReleaseControl. Each write needs the lease token in `x-ocs-control-lease` and
the current expected revision. An optional `x-ocs-compact-reply: true` returns
the result, revision, status, request ID and timing; full state remains
available through GetRuntime. The minimal example shows the complete sequence.

Long experiments must renew their lease before it expires and use bounded
per-call contexts. Persistent connections refresh their deadlines on each call;
they have no fixed lifetime from the time of initial connection. A framed
connection is sequential and expires after five minutes idle; reconnect and
read state before resuming. Concurrent experiment owners must coordinate
through the lease, rather than sharing one token without orchestration.

## Completion, failure and recovery

An Apply success waits for the BFRT call and Core state commit. `CACHED_ACK`
confirms BFRT acknowledgement; `CACHED_SYNC` also checks hardware readback;
`STRICT_DEVICE` checks before and after writing. None proves optical readiness.

The default native batch combines deletes and inserts in one BFRT request with
ROLLBACK_ON_ERROR. That is not a dataplane atomicity guarantee. Sequential
transport and explicit gaps remain available. `backend.two_phase: true` is a
controlled diagnostic variant for the delete-then-insert comparison, not the
normal low-latency configuration.

There is no asynchronous early ACK, background write queue, busy poll or
experimental environment-variable override. After a determinate rejection, the service attempts to restore and verify the
previous state. A lost response, timeout or failed rollback makes the cache
UNKNOWN and further writes are rejected. The service does not guess whether
an uncertain write completed or replay it automatically. Query
authoritative state and call RecoverDeviceState explicitly; recovery must read
back the desired state before clearing the error. Experiments must handle an unknown outcome explicitly; a background reconcile
read does not silently clear it.

An interrupted framed exchange poisons that client connection. Reconnect,
inspect state and decide on recovery; do not re-submit the previous write
blindly. Request IDs correlate observations and do not provide durable
exactly-once execution across process restarts.

## Verification and reproducibility

```bash
cd agent/go
go test -race ./...
```

Tests check shared transport authority, stale revisions, idempotency, success
after device completion, one/two-request accounting, unknown-outcome blocking,
drift and readback mismatch, explicit recovery, persistent deadlines and BFRT
embedded errors. A software BFRT fixture exercises discovery, subscription,
readback and batch serialization. These tests do not validate a physical board.

The benchmark and packet probe use the same public `client.Dial` path as other
experiments. Save the full source snapshot, dependencies, binary SHA256,
configuration and measurement conditions for every physical run.
Measure full external-client API latency and dataplane observations separately;
do not subtract an estimated network RTT to manufacture a software budget.

BFRT bindings are generated from a pinned public Open P4 Studio source under
`agent/proto/vendor/open-p4studio-bfruntime`, with source URL, commit and hashes.
No confidential SDE-generated source is required to build the Go binary. The
deployment still needs a compatible vendor BFRT service; wire compatibility
must be checked for each deployed vendor version.
