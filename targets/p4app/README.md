# P4App target

The P4App target runs the OCS packet-level model on BMv2 in Docker. It is the lowest-cost path for developing connection semantics, testing clients and reproducing control-plane measurements without a physical P4 switch.

Project motivation and fidelity limits are defined in the repository [README](../../README.md) and [simulation boundaries](../../docs/ocs-simulation-principles-and-boundaries.md).

> [!NOTE]
> P4App/BMv2 is a packet-level software target for fast, reproducible integration experiments. It is not an optical validation target, and results that depend on PHY, NIC recovery or optical timing require a separate hardware experiment.

## Requirements

- Docker with permission to access the Docker daemon;
- GNU Make;
- the pinned P4App runner under `third_party/p4app`.

## Start the default profile

From the repository root:

```bash
cd targets/p4app
make image
make run
```

The default configuration is [`python-monolith-http-direct.json`](../../agent/configs/p4app/python-monolith-http-direct.json). It starts:

- an eight-host Mininet/P4App topology;
- the BMv2 `ocs.p4` pipeline;
- the Python monolith HTTP Agent on port 5000;
- `CACHED_SYNC` consistency with P4Runtime readback.

The P4App command remains attached to the Mininet CLI. Stop it by exiting the CLI cleanly.

## Start the Go split profile

Select the alternate configuration when starting the container:

```bash
P4APP_CONTAINER_ARGS='-e OCS_CONFIG_FILE=/opt/ocs-agent/configs/p4app/go-split-grpc.json' \
  make run
```

This profile starts the Go gRPC/gNMI Agent on port 9339 and a Python Device Worker connected through a Unix-domain socket inside the container. The Worker owns the P4Runtime backend and its dedicated executor.

Only one profile owns the BMv2 instance in a given P4App run.

## HTTP operation

Every write requires a control lease and the current expected revision. The following complete example applies a new permutation:

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

The HTTP paths are documented by their shared behavior in [OCS control semantics](../../docs/ocs-control-semantics.md).

## gRPC operation

The image contains an operational client that manages the lease and revision automatically:

```bash
third_party/p4app/p4app exec /usr/local/bin/ocs-control \
  --target 127.0.0.1:9339 \
  --operation apply \
  --pi 4,3,2,1,8,7,6,5 \
  --strategy delta \
  --transport native-batch
```

Use gNMI `Set` for named per-connection create/replace/delete and sparse connection sets. Use `OcsOperations.ApplyBatch` for a complete named set or strict `pi` batch.

To temporarily remove OCS matching during network bring-up, follow the [Debug Mode procedure](../../docs/debug-mode.md).

## Tests

Host tests require Python dependencies and `pyang`:

```bash
make test
```

The container suite supplies the pinned Python 3.5 environment and YANG tooling:

```bash
make test-container
```

Go tests can be run locally from `agent/go` or through Docker:

```bash
make test-go
make test-go-race
```

## Performance collection

Collect each maintained deployment profile separately:

```bash
make benchmark-matrix-collect \
  PROFILE=python-monolith-http-direct \
  OUTPUT=python-http.json

make benchmark-matrix-collect \
  PROFILE=go-split-grpc \
  OUTPUT=go-grpc.json
```

Generate a report:

```bash
python3 ../../benchmarks/profile_matrix.py report \
  --input python-http.json \
  --input go-grpc.json
```

Record client language, RTT, profile, consistency mode, strategy and transport with every result. Historical comparison data belongs in [`docs/archive`](../../docs/archive/README.md); raw run artifacts should remain outside the active source tree.
