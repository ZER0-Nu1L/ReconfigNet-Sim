# Protocol sources

`ocs_operations.proto` is the ReconfigNet-Sim operational extension used for the active-writer control lease, permutation batch, runtime mode, explicit device recovery, revision and timing. Every mutation requires `expected_revision` and the `x-ocs-control-lease` gRPC metadata value.

`device_backend.proto` is the internal Agent Core -> Device Worker contract. It carries expected and target directed entries plus backend timing; it is not a public controller API.

The checked-in `gnmi_pb2*.py` and `gnmi_ext_pb2*.py` files under
`agent/python/ocs_agent/proto/` were generated from OpenConfig gNMI tag
`v0.14.1`. That source tag's protobuf package advertises gNMI service version
`0.10.0`, which is also declared by `agent/configs/p4app/capabilities.yaml`.

Generated modules retain the public `ocs_agent.proto.*` imports. Both sets
come from the same four sources, including the checksum-verified OpenConfig
sources and license in `vendor/openconfig-gnmi-v0.14.1/`. The generator normalizes
the upstream gNMI import to the historical flat descriptor name and applies
package-relative imports deterministically.

| Environment | Requirements | Binding location | Compiler |
| --- | --- | --- | --- |
| Python 3.10 / 3.11 | `agent/python/requirements-dev.txt` | `ocs_agent/proto/modern/` | grpcio-tools 1.84.0 |
| P4App Python 3.5 | `agent/python-legacy/requirements-dev.txt` | `ocs_agent/proto/` | grpcio-tools 1.40.0 |

The initializer selects the binding directory for protobuf 7 or 3. Other
major versions fail with installation guidance. Install a complete requirements
set in a separate environment; do not upgrade the legacy SDK or P4Runtime
interpreter in place. The Tofino model image is unchanged and does not contain
grpcio; its model tests do not validate Agent RPC dependencies.

From the repository root, use the matching environment:

```bash
python3 -m pip install -r agent/python/requirements-dev.txt
python3 agent/proto/generate_python.py --flavor modern
python3 agent/proto/generate_python.py --flavor modern --check
# Inside the legacy test image, with the checkout mounted at /workspace:
python3 /workspace/agent/proto/generate_python.py --flavor legacy --check
```

`check_compatibility.py matrix` launches real gRPC processes against a persistent
fake switch. It compares descriptors, uint64/oneof/unknown-field serialization
and safe YAML parsing, then runs legacy → modern → legacy Workers with clients
from both environments. It covers stale generations, failed transactions,
failed rollback, explicit recovery, and equality of the final and initial
mapping. Repeat `--go-agent /path/to/binary` for old and new Go agents to include
gNMI, REQUIRE_MATCH blocking, explicit recovery, lease and revision checks.
Use `--legacy-source /absolute/old/source` with a retained old image to rehearse
a full source/runtime downgrade; by default both runtimes use this checkout.
This exercises software compatibility, not BMv2 packet forwarding or hardware.

```bash
python3 agent/proto/check_compatibility.py matrix \
  --legacy-image reconfig-net/ocs-p4app-agent-test:rc2 \
  --go-agent /path/to/ocs-go-agent
```

The Go stubs for both local services are checked in under `agent/go/gen`. Regenerate them with Go 1.25, `protoc-gen-go v1.36.9` and `protoc-gen-go-grpc v1.5.1`:

```bash
cd agent/go
protoc -I ../proto \
  --go_out=. --go_opt=module=github.com/reconfig-net-sim/ocs-go-agent \
  --go-grpc_out=. --go-grpc_opt=module=github.com/reconfig-net-sim/ocs-go-agent \
  ../proto/ocs_operations.proto \
  ../proto/device_backend.proto
```

The OpenConfig source files are available at:

- `https://github.com/openconfig/gnmi/blob/v0.14.1/proto/gnmi/gnmi.proto`
- `https://github.com/openconfig/gnmi/blob/v0.14.1/proto/gnmi_ext/gnmi_ext.proto`
