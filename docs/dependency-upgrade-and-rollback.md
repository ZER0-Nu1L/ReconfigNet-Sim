# Dependency upgrades and rollback

Update compatible groups together: modern Python runtime/compiler/bindings,
Go protocol libraries, the blog toolchain, and GitHub Actions. Dependabot runs
monthly with one group per ecosystem. TypeScript 7 is deferred while the
Astro checker requires TypeScript 6. Frozen Python 3.5 manifests are outside
the monitored Python directory. Review security advisories for the frozen
runtime separately; it is not a supported modern host environment.

## Supported environments

| Path | Supported combination | Compatibility gate |
| --- | --- | --- |
| Modern Python clients and host tests | Python 3.10/3.11; grpcio/tools 1.84.0, protobuf 7.36.2, PyYAML 6.0.3, pyang 2.7.1 | Both Python versions, generated binding checks, real cross-runtime RPCs |
| P4App Agent, P4Runtime and test image | Existing Ubuntu 16.04/Python 3.5; grpcio/tools 1.40.0, protobuf 3.17.3, PyYAML 5.3.1, pyang 2.6.1 | Legacy generation, mandatory RPC tests, BMv2 acceptance |
| Go split Agent and clients | Go 1.25; gRPC 1.84.0, protobuf 1.36.12 | Unit/race tests; old/new Python Worker and clients |
| Tofino | Unchanged immutable image lock and site SDK environment | Compile/runtime/model checks; Agent RPC tested separately |
| Blog | Node 22.22.2; Astro 7.4.0, Markdown Remark 7.3.2, TypeScript 6.0.3, Playwright 1.64.1 | Content/check/build, production fixtures and paired Chromium browser tests |

Install a complete Python environment using its matching requirements file.
Do not put modern dependencies on a P4Runtime or BFRT SDK interpreter's path.
The [protocol guide](../agent/proto/README.md) documents dual binding generation.
Go generated sources retain their documented compiler versions: updating the
runtime libraries alone does not require regenerating an unchanged contract.

Astro requires Node >=22.22.0; Actions setup-node's own runtime is independent
of the project's pinned Node. Playwright 1.64.1 no longer supports Ubuntu 20.04;
use a supported runner (CI uses ubuntu-latest) and its paired browser. A local
fallback browser run must identify the OS and engine and is not proof of the
supported CI runner's installation path.

## Before a maintenance window

1. Record the current commit, clean/dirty state, configuration, mode,
   desired mappings, observed mappings, runtime dependencies and image IDs.
   Save old Python/Go binaries, generated bindings and dependency locks.
   Export images with `docker save`; a mutable tag alone is not a backup.
2. Save source, image and static-site archives outside Git. Create SHA256
   indices, validate every JSON result, and verify archive checksums before
   testing an upgrade. Keep the old archives until acceptance finishes.
3. Build task-specific image tags. Keep the old image IDs and binaries
   untouched. Install modern Python into a separate virtual environment.
4. Run Python/YANG, Go unit/race, code generation, cross-runtime RPC and
   BMv2 acceptance. Run Tofino compilation/model validation separately.
   Run blog checks against the current published articles and draft fixtures.
5. Rehearse old → new → old with retained artifacts. Confirm equality of
   the initial and restored mapping, and exercise a new lease/revision after
   each restart. A successful transaction rollback alone is insufficient.

## Control-plane downgrade

Use a maintenance window with a single writer. Pause controllers and other
writers before recording state or switching versions. This procedure does not
promise interruption-free service or preserve lease/revision continuity.

1. Record mode, desired and read-back mappings and the exact configuration.
   Stop the new Agent and Worker cleanly; verify no other writer remains.
2. Restore the whole retained old environment: image/binary, source,
   configuration, dependencies and bindings. Avoid partial pip downgrades.
3. Start with `REQUIRE_MATCH` where supported. An observed/desired mismatch
   must block mutation and require deliberate recovery. P4App's default
   `REAPPLY_DESIRED` can overwrite the current mapping at startup; use it only
   when the captured desired mapping is explicitly the intended restoration.
4. Compare the read-back mapping and mode with the captured state. Recover
   explicitly if needed and confirm the runtime is READY. If recovery fails,
   keep writes paused and preserve logs rather than repeatedly overwriting it.
5. Discard old lease tokens and revisions. Generation, revision and lease
   state live in memory and restart with the process. Clients must reacquire
   a lease, read fresh state and then make a controlled write/read-back check.
6. Resume writers after verification. Record all versions, image IDs,
   checksums and outcomes in the external run manifest.

`agent/proto/check_compatibility.py matrix` provides a repeatable software
rehearsal against a persistent fake device, including rollback failure and
explicit recovery. Add `--legacy-source` and old/new `--go-agent` binaries for
retained-artifact downgrade coverage. BMv2 and Tofino acceptance cover separate
integration boundaries; neither proves physical hardware rollback.

## Blog, Actions and remote changes

Keep the old lockfile and static `dist` archive with checksums. Verify an old
build, a new build and a restored old build, including the bilingual articles,
formulas/fonts, figures, headings/outline, mobile layout, themes, RSS and draft
exclusion. Follow [Pages deployment](../blog/deployment.md) for an authorized
release and rollback. A feature branch or PR validates the build; its skipped
deploy job does not validate upload/deploy compatibility or public rollback.

Retain full Action commit SHAs, read-only default permissions, Pages' scoped
permissions, main-only deployment and the blog-only exclusions on both target
workflow events. Artifact uploads must still run after failure and retain the
expected evidence paths. Local static checks do not execute hosted Actions.

For published code, use a focused revert PR for the relevant group and rerun
its checks; do not rewrite shared history. Push, merge, close superseded PRs,
publish Pages or touch live devices only after explicit authorization. Close
a superseded Dependabot PR after its replacement is merged and accepted.

Dependency changes also change the experiment environment. Record Python,
grpcio, protobuf, Go, commit, image ID, backend and consistency mode with any
new benchmark. Do not combine old and new latency measurements as one baseline.
