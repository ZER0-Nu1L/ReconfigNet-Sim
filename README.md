# ReconfigNet-Sim

ReconfigNet-Sim uses programmable switches to simulate a reconfigurable optical circuit switch. It supports a BMv2/P4App backend and a Tofino/BFRT backend with one shared OCS model and transaction contract.

## Current OCS architecture

Only two deployment profiles are supported:

| Profile | Northbound interface | Device path | Purpose |
| --- | --- | --- | --- |
| `python-monolith-http-direct` | Python HTTP | in-process P4Runtime/BFRT backend | Minimum control latency |
| `go-split-grpc` | Go gRPC/gNMI | UDS → Python Device Worker → dedicated backend executor | Typed YANG contract and vendor SDK isolation |

Both profiles retain the YAML model, named connections, strict `pi` validation, lease/revision checks, FULL/DELTA updates, structured errors and rollback semantics. Python split, Python gRPC NBI and Go HTTP NBI are historical implementations and have been removed from the active source tree.

Start with:

- [OCS Agent current architecture](docs/ocs-agent-architecture.md)
- [Draft/YANG support matrix](docs/ocs-model-support.md)
- [Control semantics](docs/ocs-control-semantics.md)
- [Historical architecture evidence](docs/archive/README.md)

## Repository layout

```text
agent/                       shared model, contracts, Python Agent and Go Agent
benchmarks/                  protocol, profile and reconfiguration benchmarks
targets/p4app/               BMv2/P4App data plane and runtime integration
targets/tofino/              Tofino P4 program and BFRT runtime integration
third_party/p4app/           pinned upstream P4App runner
docs/                        current architecture and archived decision evidence
```

The P4App tree does not contain copies of shared Agent, protobuf, Go or YANG sources. Docker builds copy the canonical `agent/` tree.

## P4App

The rc2 P4App implementation runs in Docker.

```bash
cd targets/p4app
make image
make run
```

The default config is `agent/configs/p4app/python-monolith-http-direct.json`, which selects `python-monolith-http-direct`, listens on HTTP port 5000 and uses `CACHED_SYNC`.

To run the Go split profile:

```bash
P4APP_CONTAINER_ARGS='-e OCS_CONFIG_FILE=/opt/ocs-agent/configs/p4app/go-split-grpc.json' \
  make run
```

The Go profile listens on gRPC port 9339 and uses a Unix-domain DeviceBackend socket inside the container.

### HTTP example

Every write requires a control lease and the current expected revision:

```python
import http.client
import json


def call(method, path, payload=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', 5000)
    body = json.dumps(payload) if payload is not None else None
    request_headers = {'Content-Type': 'application/json'} if body else {}
    request_headers.update(headers or {})
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    result = json.loads(response.read().decode('utf-8'))
    connection.close()
    return response.status, result


_, lease = call('POST', '/ocs_control/acquire', {'client_id': 'example'})
headers = {
    'X-OCS-Control-Lease': lease['lease_token'],
    'X-OCS-Expected-Revision': str(lease['revision']),
}
status, result = call('POST', '/ocs_mapping', {
    'new_pi': [4, 3, 2, 1, 8, 7, 6, 5],
    'strategy': 'DELTA',
    'transport': 'NATIVE_BATCH',
}, headers)
print(status, result)
```

### gRPC example

The image contains an operational client that manages lease and revision automatically:

```bash
third_party/p4app/p4app exec /usr/local/bin/ocs-control \
  --target 127.0.0.1:9339 \
  --operation apply \
  --pi 4,3,2,1,8,7,6,5 \
  --strategy delta \
  --transport native-batch
```

Use gNMI `Set` for named per-connection create/replace/delete and sparse connection sets. Use `OcsOperations.ApplyBatch` for a complete named set or strict `pi` batch.

## Tofino

Tofino requires a matching BF-SDE environment and `bf_switchd` exposing external BF Runtime gRPC. The Agent uses an explicit site-specific JSON profile; real addresses, physical ports and logical-to-dev_port mappings must stay in the deployment repository.

```bash
cd targets/tofino/runtime
./run_agent.sh /absolute/path/to/tofino-agent.json
```

The normal Tofino deployment selects `go-split-grpc` with `CACHED_ACK`. `python-monolith-http-direct` remains available when minimum API latency is more important than the explicit Worker contract. Its HTTP listener must not use port 5000 while the BF-SDE control process owns that socket. The embedded `initialize_dataplane.py` only initializes data-plane tables; it no longer exposes an independent REST writer.

Only one Agent may own a Tofino device. The launcher enforces a BFRT ownership lock.

## Tests

```bash
# Shared Python semantics, P4Runtime adapter and BFRT adapter
PYTHONPATH="$PWD/agent/python:$PWD:$PWD/targets/p4app" \
  PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s agent/python/tests -v

# Go Core and gRPC/gNMI implementation
cd agent/go
go test ./...
go test -race ./...

# Pinned Python 3.5 P4App image and YANG validation
cd ../../targets/p4app
make test-container
```

## Performance collection

Performance reports must record deployment profile, client language, client-to-Agent RTT, backend, consistency mode, FULL/DELTA strategy and transport. The current collector accepts only the two supported profiles:

```bash
make benchmark-matrix-collect \
  PROFILE=python-monolith-http-direct \
  OUTPUT=python-http.json

make benchmark-matrix-collect \
  PROFILE=go-split-grpc \
  OUTPUT=go-grpc.json

python3 ../../benchmarks/profile_matrix.py report \
  --input python-http.json \
  --input go-grpc.json
```

The report includes the absolute microsecond cost of the split frontier relative to the monolith frontier. Historical Python/Go × HTTP/gRPC matrices and dedicated-thread root-cause data are archived under `docs/archive/`; raw benchmark JSON belongs in the external artifact store rather than the active source tree.
