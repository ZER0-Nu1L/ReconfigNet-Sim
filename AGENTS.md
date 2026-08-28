# Agent Guidelines

Read and follow [`CONTRIBUTING.md`](CONTRIBUTING.md). It is the authoritative
source for project boundaries, CI target coverage and contributor checks. This
file adds only automation-specific working rules.

## Working tree and commits

- Preserve existing dirty worktrees and unrelated user changes.
- Keep each commit focused. Do not combine repository-layout refactors, CI
  repairs and unrelated code changes in one commit.
- Use a separate branch or worktree unless the user explicitly authorizes work
  directly on the current branch.
- Inspect the staged diff before committing and do not use destructive Git
  cleanup to remove changes you did not create.

## CI and target maintenance

- When paths move, update both workflows, Make targets, dependency and cache
  paths, target documentation and contributor commands in the same change.
- Treat P4App as the lower-cost, more customizable BMv2 integration target.
- Treat the Tofino model as a heavier compatibility check against the pinned
  external toolchain. Pull requests compile; pushes to `main` and manual runs
  also load and initialize the model, exercise forwarding/drop behavior,
  reconfigure the mapping through BFRT and restore the startup state.
- Do not edit `.github/tofino-image-lock.json` unless the task explicitly
  includes adopting a reviewed immutable release digest. Use the checked-in
  image-lock tool rather than editing the JSON by hand.
- Never describe a successful Tofino software-model run as validation of a
  physical board, BSP, SerDes, firmware, driver or optical behavior.

## Safety and repository boundaries

- Do not commit real addresses, MACs, device ports, credentials, firmware,
  generated model artifacts or site acceptance results.
- Do not push, merge, delete remote branches or operate live devices unless the
  user explicitly authorizes that action.
- Before publishing previously local history, scan it for secrets, private
  deployment data and unexpected binary artifacts.
