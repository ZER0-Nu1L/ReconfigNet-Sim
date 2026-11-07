
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
