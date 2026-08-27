# Contributing to ReconfigNet-Sim

ReconfigNet-Sim is research infrastructure for exposing and de-risking
system-integration problems around reconfigurable optical networks. Changes
should preserve that boundary and should make it clear which observations are
packet-switch emulation results rather than optical-hardware claims.

## Before opening a pull request

- Read the relevant target and model-boundary documentation.
- Keep site-specific addresses, credentials, SDE paths, port mappings and raw
  acceptance artifacts out of the portable repository.
- Preserve the logical OCS contract when changing a backend or deployment
  profile. Device SDK details belong behind the backend adapter.
- Add or update tests and documentation together with behavior changes.
- Report benchmark results with the complete execution path, backend,
  consistency mode, transport, strategy and client-to-Agent RTT.

## Local checks

```bash
git diff --check
make -C targets/p4app test
make -C targets/p4app test-container
(cd agent/go && go test ./...)
```

Tofino-specific builds require a locally installed BF-SDE and are not expected
to run on every contributor workstation. When available, record the SDE
version and target model with the result.

## Pull requests

Use a focused branch and explain the motivation, scope, validation and any
changes to the emulation boundary. Do not include secrets, private topology
details or generated benchmark artifacts. Generated protocol bindings should
be regenerated from the checked-in source definitions rather than edited by
hand.

Contributions are reviewed under the license stated in the affected files.
Project-authored files use the MIT License; selected upstream-derived P4 files
retain their Apache-2.0 attribution as documented in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
