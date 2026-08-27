# Protocol sources

`ocs_operations.proto` is the ReconfigNet-Sim operational extension used for the active-writer control lease, permutation batch, runtime mode, explicit device recovery, revision and timing. Every mutation requires `expected_revision` and the `x-ocs-control-lease` gRPC metadata value.

`device_backend.proto` is the internal Agent Core -> Device Worker contract. It carries expected and target directed entries plus backend timing; it is not a public controller API.

The checked-in `gnmi_pb2*.py` and `gnmi_ext_pb2*.py` files under
`agent/python/ocs_agent/proto/` were generated from OpenConfig gNMI tag
`v0.14.1`. That source tag's protobuf package advertises gNMI service version
`0.10.0`, which is also declared by `agent/configs/p4app/capabilities.yaml`.

Generated modules use package-relative imports so they can be imported as
`ocs_agent.proto.*`. Regeneration must retain those relative imports and use
the protobuf/gRPC tool versions pinned in `agent/python/requirements-dev.txt`.

From the repository root, regenerate the Python modules with:

```bash
PYTHONPATH=agent/python python3 -m grpc_tools.protoc \
  -I agent/proto \
  --python_out=agent/python/ocs_agent/proto \
  --grpc_python_out=agent/python/ocs_agent/proto \
  agent/proto/ocs_operations.proto \
  agent/proto/device_backend.proto
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
- `https://github.com/openconfig/gnmi/blob/v0.14.1/proto/gnmi/gnmi_ext.proto`
