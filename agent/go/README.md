
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
