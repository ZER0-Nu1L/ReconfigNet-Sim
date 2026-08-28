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

## CI target matrix

P4App and the Tofino model validate different integration boundaries. They are
complementary checks rather than interchangeable implementations of the same
CI environment.

| Target | Environment and customization | Cost | Pull requests | `main` push / manual run | Outside the validation boundary |
| --- | --- | --- | --- | --- | --- |
| P4App / BMv2 | Repository-controlled P4App container, BMv2 topology, startup flow, Agent profile and test orchestration. These layers can be changed together for project-specific experiments. | Lower; suitable for frequent iteration. | Python/YANG tests, Go tests, P4App container tests and whitespace checks. | The same complete suite. | Physical switch, firmware, drivers, PHY behavior and optical behavior. |
| Tofino 1 model | Project P4 pipeline, runtime tests and BFRT bootstrap run against the digest-pinned [`open-p4studio-container`](https://github.com/ZER0-Nu1L/open-p4studio-container). The underlying toolchain and model are fixed by that image. | Higher; compilation, model startup and BFRT initialization are comparatively heavyweight. | Pipeline compilation and target-neutral runtime tests only. | Compilation plus privileged Tofino model startup, pipeline loading and BFRT initialization. | Physical Tofino board, BSP, SerDes, firmware, drivers, site port mappings and optical behavior. |

Use P4App for low-cost, highly customized integration work. Use the Tofino
model as a heavier compatibility gate for the P4 compiler, generated pipeline,
software model and BFRT initialization path. A successful model run must not be
described as physical Tofino or optical-hardware validation.

## Local checks

```bash
git diff --check
make -C targets/p4app test
make -C targets/p4app test-container
(cd agent/go && go test ./...)
```

The Tofino workflow consumes the immutable image reference in
`.github/tofino-image-lock.json`; a host BF-SDE installation is not required
for the model CI path. The image is maintained in the independent
[`open-p4studio-container`](https://github.com/ZER0-Nu1L/open-p4studio-container)
repository. It is not an official Intel, P4.org or p4lang image and does not
provide the hardware BSP, SerDes stack, firmware or drivers needed to validate
a physical board. See the [Tofino target documentation](targets/tofino/README.md)
for local compile-only and full-model reproduction commands.

## Keeping CI maintainable

- Keep workflow paths, Make targets, dependency paths, cache paths and target
  documentation synchronized with repository layout changes.
- Update `.github/tofino-image-lock.json` only from a reviewed immutable
  release digest; never replace it with a floating image tag.
- When changing shared Agent contracts or semantics, consider both P4App and
  Tofino runtime tests even if only one target implementation was edited.
- Document whether a result came from P4App, Tofino compilation, the Tofino
  software model or a separately identified physical-hardware experiment.
- Keep generated CI evidence and model logs out of source commits.

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
