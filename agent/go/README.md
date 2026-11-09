# Go OCS Agent and clients

The Go Agent supports direct BFRT and the portable OCS protobuf contract.
Start from [`../configs/tofino/go-direct-bfrt.example.json`](../configs/tofino/go-direct-bfrt.example.json)
for a portable direct-BFRT configuration. Supply your model, capability profile,
management listener and backend mapping explicitly. Use `REQUIRE_MATCH` at
startup; stop other writers before transferring device ownership.

```sh
go build ./cmd/ocs-go-agent
go build ./cmd/ocs-control
go build ./cmd/ocs-qualify
./ocs-go-agent --config /path/to/agent.json --check-config
./ocs-go-agent --config /path/to/agent.json
./ocs-control --target grpc://device:9339 --operation get
./ocs-control --target framed://device:9340 --compact --operation get
```

`client.Dial` returns a connection usable by the generated `OcsOperations`
stubs. gRPC and framed TCP expose the same lease, revision, ownership and
synchronous completion contract. The framed listener must be configured in
`framed_api`. `--compact` omits repeated model data from operation replies and
works with either transport. Read model/capability data separately when needed.
The client also supports `framed-unix:///path` for independently provided Unix
listeners; the Agent configuration currently exposes TCP listeners only.
Endpoints require a trusted management network; this helper adds no TLS or
authentication. The framed connection permits one outstanding call at a time.

For a write, acquire control, use the lease's revision as the expected revision,
and advance it only from the acknowledged reply. Persistent clients must renew
the lease. Any uncertain completion requires authoritative inspection: never
retry the write, switch protocols, or restore automatically. See
[`examples/remote-switch`](examples/remote-switch/main.go) for a small paired
experiment and `ocs-control --help` for one-shot administrative operations.

## Continuous paired-write qualification

Reserve the entire mapping and stop other writers before running this command.
Both permutations must be symmetric, without fixed points, and different.

```sh
./ocs-qualify --target framed://device:9340 --compact --execute \
  --initial-pi 2,1,4,3 --alternate-pi 3,4,1,2 \
  --duration 24h --interval 5s --write-interval 60s \
  --output /private/new-qualification-events.jsonl
```

This uses one persistent connection, renews the lease at each sample, verifies
READY/no drift and revision continuity, and performs an acknowledged real
change/restore pair every minute. Every successful write must advance revision
once and report one device write request. An unexpected reply, renewal failure,
transport failure or evidence write failure stops the client; it never attempts
a restore after an uncertain update. SIGINT/SIGTERM ends the run as failed.
The event file is exclusive, private and synced after each record. A `complete`
event means only that this client finished its requested duration; deployment
qualification must independently validate full-duration sample/write coverage,
process identity, resource limits, hardware verification age and forwarding.
Short smoke runs do not provide a 24h certificate.

Transport, compact replies, process parallelism and CPU policy are independent
choices. `--gomaxprocs=1` is opt-in on the Agent; use `GOMAXPROCS=1` for a client
only after workload qualification. These affect scheduling and throughput;
CPU governor policy belongs to the deployment system and can affect the whole
host. Neither client defaults nor generic configuration force site tuning.

## Diagnostics (off by default)

### Opt-in northbound timing

Start a temporary Agent with `--diagnostic` to enable gRPC statistics and run
`ocs-benchmark --diagnostic --protocol grpc --concurrency 1 --raw-samples ...`.
Only marked ApplyBatch calls return nanosecond duration trailers. Raw samples
include client protobuf marshal/unmarshal, server admission-to-decoded payload,
dispatch, handler, inclusive Core call, pre-Core adaptation, reply construction,
and reply encoding/transport enqueue. The existing Core timer stops before
state/result finalization; subtract it from the inclusive Core call only as a
combined tail/measurement-boundary budget. No protobuf schema changes are needed.

Intervals are nested: handler contains pre-Core + Core call + reply construction;
Core contains the existing server and programming timers. Compute exclusive
budgets per correlated sample, then average. Never add parent/child timers or
subtract percentile values. The remaining RPC duration includes HTTP/2 machinery,
kernel/network transport, unobserved scheduling, and diagnostic overhead; it is
not a pure network measurement. Server input includes payload wait as well as
decoding, and output includes encoding plus enqueue, not remote receipt.

Keep uninstrumented comparisons for performance ranking and report diagnostic
perturbation separately. Connections are already persistent and retries disabled.
Diagnostics keep lease/revision checks, synchronous completion and failure-stop
behavior. A successful write with absent diagnostic trailers stops the benchmark;
inspect state before attempting another write. The framed API does not transport
these diagnostic trailers. Do not treat its absence as a zero-duration phase.

The benchmark also accepts `--grpc-write-buffer-bytes=0` to compare client-side
HTTP/2 write buffering against `-1` (the library default). This affects only the
benchmark's persistent gRPC connection; it neither changes the Agent nor the
BFRT connection. Measure it against an otherwise identical uninstrumented run.

Diagnostic clients additionally record elapsed time from invocation to gRPC's
OutPayload, InHeader and InPayload events. OutPayload follows transport enqueue;
InPayload follows response decoding. These client markers locate delay within
the invocation but can overlap remote server intervals, so do not add them to a
server timing pie. Only durations are compared across machines.

### Bounded runtime traces and experimental server settings

The Agent accepts `--trace-prefix=/private/path/capture --trace-duration=10s`.
An explicit SIGUSR1 then captures `capture.001.trace`, `capture.002.trace`, etc.
Files are private and exclusive, captures are limited to 30 seconds, and repeated
signals during a capture are ignored. No network diagnostics endpoint is added.
The benchmark accepts `--trace-file=/private/path/client.trace` and the same
bounded duration. Tasks log reply request IDs/revisions for correlation; Core
regions distinguish commit waiting, BFRT completion, and reconciliation holds.
Compare intervals within each host; do not subtract unsynchronized clocks.
Runtime traces alter scheduling and must be compared with tracing disabled.

Agent flags `--grpc-write-buffer-bytes=0`, `--grpc-stream-workers=1`, and
`--gomaxprocs=1` expose separate experiments. Defaults preserve existing behavior.
Workers are an experimental upstream API; runtime parallelism affects every
Agent goroutine and may hurt concurrent or bursty workloads. Keep all of these
out of the default deployment until measured and accepted for the actual load.

`ocs-benchmark --request-interval=100ms --concurrency=1` provides serial pacing
without catch-up bursts. Paced writes explicitly renew the lease every five
seconds; a renewal failure stops subsequent writes. Renewal remains inside the
measured Apply call, so affected latency is retained. Raw samples include start
offsets for observed arrival-rate checks. Read-only RPC timings are a transport
and snapshot-path diagnostic, not a substitute for synchronous update latency.
