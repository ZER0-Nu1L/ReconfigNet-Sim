# Public BFRT protocol source

The source is copied unchanged from the pinned public Open P4 Studio commit
recorded in `source.json`, under Apache-2.0. `SHA256SUMS` covers this source,
the upstream license and its provenance record.

The checked-in Go bindings were generated using protoc 29.0 (grpcio-tools
1.70.0) and protoc-gen-go 1.36.9. Install the compiler and the Google RPC proto
include files, then from `agent/go` run:

```bash
protoc -I ../proto/vendor/open-p4studio-bfruntime \
  -I /absolute/path/to/googleapis-protos \
  --go_out=. --go_opt=module=github.com/reconfig-net-sim/ocs-go-agent \
  --go_opt=Mbfruntime.proto=github.com/reconfig-net-sim/ocs-go-agent/gen/bfruntime \
  ../proto/vendor/open-p4studio-bfruntime/bfruntime.proto
```

Only Go messages are needed; the client uses the BFRT service's canonical RPC
names. Building the checked-in code does not require protoc, Python, an SDE
installation or generated files from an old experimental checkout. A compatible
BFRT runtime is required when starting the actual device service.
