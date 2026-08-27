# Tofino target

The Tofino target runs the same logical OCS model on a hardware P4 pipeline and programs its permission table through BFRT. It remains a packet-level OCS approximation; using a hardware switch does not add optical behavior to the model.

Read the project [simulation boundaries](../../docs/ocs-simulation-principles-and-boundaries.md) before interpreting hardware results.

## Requirements

- a Tofino switch with a BF-SDE release matching the compiled P4 artifacts;
- `bf_switchd` running the `ocs` pipeline;
- external BF Runtime gRPC, normally on `127.0.0.1:50052`;
- site-specific front-panel, `dev_port`, endpoint address and MAC mappings;
- a Python runtime that can import the installed BFRT client packages;
- the Go Agent binary when using `go-split-grpc`.

## Reproducible model CI

The repository's [Tofino model workflow](../../.github/workflows/tofino-model.yml)
consumes the independent
[`open-p4studio-container`](https://github.com/ZER0-Nu1L/open-p4studio-container)
release by the immutable digest recorded in
[`tofino-image-lock.json`](../../.github/tofino-image-lock.json). The workflow
mounts this repository read-only, compiles `ocs.p4`, runs the target-neutral
runtime tests, loads the generated pipeline into the Tofino 1 model, and
initializes the site-neutral BFRT profile.

The job runs on a privileged `linux/amd64` GitHub runner because the model
needs veth interfaces and huge pages. It validates compilation and model/BFRT
integration only; it does not validate a physical Tofino board or any
site-specific port mapping. Update the lock only from a reviewed release
`image-lock.json`; do not replace it with a floating tag.

Set `SDE_INSTALL` to the BF-SDE installation before starting the launcher.
`OCS_PYTHON_RUNTIME` may be set when the Agent Python package is installed
outside this repository; otherwise the repository's `agent/python` directory is
used. No machine-specific SDE or checkout path is assumed by the launcher.

Real addresses and port mappings must not be committed as portable defaults. Keep them in a deployment repository or a local ignored configuration.

> [!WARNING]
> The addresses, MACs, front-panel ports and logical-to-`dev_port` mapping in a Tofino deployment are site-specific. Keep them outside the portable repository and verify them against the initialized local pipeline before starting the Agent.

## Data-plane initialization

[`runtime/initialize_dataplane.py`](runtime/initialize_dataplane.py) installs the baseline IPv4/MAC tables and an initial OCS mapping. It does not expose a runtime REST writer.

Create a site-specific copy of [`device-profile.example.json`](runtime/config/device-profile.example.json), then load it through the BF-SDE shell:

```bash
export OCS_CONFIG_FILE=/absolute/path/to/device-profile.json
$SDE/run_bfshell.sh \
  -b /absolute/path/to/ReconfigNet-Sim/targets/tofino/runtime/initialize_dataplane.py \
  -i
```

This initialization profile describes endpoint slots, addresses, MACs and physical device ports. It is separate from the Agent deployment JSON described below.

## Agent deployment configuration

The Agent JSON selects one maintained profile, the shared YAML model and the BFRT backend. A Go split deployment has the following shape:

```json
{
  "mode": "l3",
  "deployment_profile": "go-split-grpc",
  "model_file": "/absolute/path/to/agent/configs/tofino/model-6port.yaml",
  "capability_profile_file": "/absolute/path/to/agent/configs/tofino/capabilities.yaml",
  "enable_debugger": false,
  "grpc_api": {"host": "<management-address>", "port": 9339},
  "device": {"consistency_mode": "CACHED_ACK"},
  "worker": {
    "target": "unix:///tmp/ocs-device-worker.sock",
    "timeout_seconds": 10
  },
  "startup_policy": "REQUIRE_MATCH",
  "control": {
    "lease_seconds": 30,
    "reconcile_interval_seconds": 30
  },
  "go_agent": {"binary": "/usr/local/bin/ocs-go-agent"},
  "backend": {
    "type": "bfrt",
    "grpc_target": "127.0.0.1:50052",
    "p4_name": "ocs",
    "logical_to_device_port": {
      "1": 132,
      "2": 140,
      "3": 148,
      "4": 156,
      "5": 180,
      "6": 188
    }
  }
}
```

The values above are illustrative. The logical-to-device mapping must match the initialized pipeline and local hardware.

The common `mode: l3` field is endpoint forwarding configuration, and `enable_debugger` is a P4App/BMv2 debugger field. Neither selects Agent runtime Debug Mode. See [Debug Mode](../../docs/debug-mode.md) for the distinction.

## Start the Agent

```bash
cd targets/tofino/runtime
export SDE_INSTALL=/path/to/bf-sde/install
./run_agent.sh /absolute/path/to/tofino-agent.json
```

The launcher:

- validates the shared model and capability profile;
- acquires an exclusive BFRT ownership lock;
- connects to external BF Runtime;
- starts either the Python monolith HTTP Agent or the Go Agent plus Python Device Worker;
- shuts down the Worker and releases ownership on a clean exit.

Only one Agent may own a device. Do not start both profiles against the same BFRT table.

## Profile-specific constraints

### Go split gRPC

`go-split-grpc` is the normal typed deployment. The Go Core owns gRPC/gNMI, lease, revision and desired state. The Python Worker owns the BFRT client and runs synchronous SDK calls on its dedicated backend executor.

### Python monolith HTTP

`python-monolith-http-direct` keeps the Python Core and BFRT backend in one process for minimum API latency. Its HTTP listener must not use TCP port 5000 when the BF-SDE control process already owns that port; use an explicit alternative such as 8080.

The two profiles share connection, rollback and state semantics. Their engineering trade-off is documented in [OCS Agent architecture](../../docs/ocs-agent-architecture.md).

## Startup and consistency

- `REQUIRE_MATCH` refuses normal writes when the device table does not match the desired startup connection set. Use explicit recovery after confirming ownership and intent.
- `REAPPLY_DESIRED` replaces startup device state with the YAML connection set.
- `CACHED_ACK` returns after BFRT acknowledgement and relies on reconciliation for later device verification.
- `CACHED_SYNC` adds synchronous software readback.
- `STRICT_DEVICE` adds device precondition checks and synchronous verification.

Changing these values changes the success boundary and must be recorded with performance results.

## Debug Mode and diagnostics

The runtime `SetMode` operation supports the same diagnostic all-to-all behavior as P4App. For the current six-port model it installs 30 directed entries in the 64-entry OCS table. Follow [Debug Mode](../../docs/debug-mode.md) and return to OCS mode before connection or blackout acceptance.

Target-neutral runtime tests can be run without hardware:

```bash
PYTHONPATH="$PWD/agent/python:$PWD/targets/tofino/runtime" \
  PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s targets/tofino/runtime/tests -v
```

Hardware acceptance must additionally verify port state, BFRT ownership, the installed logical-to-`dev_port` mapping, endpoint neighbors, OCS-limited reachability and separately measured packet blackout.
