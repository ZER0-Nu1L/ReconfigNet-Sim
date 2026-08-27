# Protocol sources

`ocs_operations.proto` is the ReconfigNet-Sim operational extension used for the active-writer control lease, permutation batch, runtime mode, explicit device recovery, revision and timing. Every mutation requires `expected_revision` and the `x-ocs-control-lease` gRPC metadata value.

`device_backend.proto` is the internal Agent Core -> Device Worker contract. It carries expected and target directed entries plus backend timing; it is not a public controller API.

The checked-in `gnmi_pb2*.py` and `gnmi_ext_pb2*.py` files were generated from OpenConfig gNMI tag `v0.14.1`. That source tag's protobuf package advertises gNMI service version `0.10.0`, which is also declared by `config/p4app-capabilities.yaml`.

Generated modules use package-relative imports so they can be imported as `api.proto.*`. Regeneration must retain those relative imports and use the protobuf/gRPC tool versions pinned in `requirements-dev.txt`.

Regenerate the local operational service from `ocs.p4app/ocs.p4app-rc2` with:

```bash
python3 -m grpc_tools.protoc \
  -I api/proto \
  --python_out=api/proto \
  --grpc_python_out=api/proto \
  api/proto/ocs_operations.proto \
  api/proto/device_backend.proto
```

The Go stubs for both local services are checked in under `go-agent/gen`. Regenerate them with Go 1.25, `protoc-gen-go v1.36.9` and `protoc-gen-go-grpc v1.5.1`:

```bash
cd go-agent
protoc -I ../api/proto \
  --go_out=. --go_opt=module=github.com/reconfig-net-sim/ocs-go-agent \
  --go-grpc_out=. --go-grpc_opt=module=github.com/reconfig-net-sim/ocs-go-agent \
  ../api/proto/ocs_operations.proto \
  ../api/proto/device_backend.proto
```

The OpenConfig source files are available at:

- `https://github.com/openconfig/gnmi/blob/v0.14.1/proto/gnmi/gnmi.proto`
- `https://github.com/openconfig/gnmi/blob/v0.14.1/proto/gnmi/gnmi_ext.proto`
