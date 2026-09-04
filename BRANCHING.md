# Repository branch policy

This document records the branch layout discovered on 2026-09-04 and prevents further work from being based on the wrong application generation.

## Canonical branches

| Branch | Purpose | Status |
|---|---|---|
| `test2` | Current Render production source | Production baseline; hotfixes only while V2 is under review |
| `v2` | Next Checkni Auto release, created directly from `test2` | Active development and pull-request source |
| `main` | Old disconnected demo history | Legacy; do not merge into `test2` or `v2` |
| `master` | Older ancestor of the `test2` line | Legacy |
| `test-changes` | Changes already fully contained in `test2` | Obsolete |
| `v2-product-rebuild` | V2 attempt based on the disconnected `main` history | Superseded; PR #1 was closed without merge |

## Required workflow now

1. Create product changes from `v2` or a short-lived branch based on `v2`.
2. Open pull requests against `test2` until repository and Render settings are consolidated.
3. Do not merge `main`, `master`, or `v2-product-rebuild` into the active line.
4. Keep `test2` deployable and use a Render preview or staging service for `v2` when possible.

## Safe consolidation after V2 validation

After the `v2` pull request is tested on Render and merged into `test2`:

1. Preserve the old disconnected tips under clearly named archive refs or tags.
2. Move `main` to the validated `test2` commit and make `main` the GitHub default branch.
3. Point Render production deployment at `main`.
4. Delete `master`, `test-changes`, `v2-product-rebuild`, and eventually `test2` after confirming no external service depends on them.
5. Use `main` for production and short-lived `feature/*` branches thereafter.

Branch deletion and default-branch/Render changes are intentionally deferred until the replacement build passes a real production-key smoke test. This avoids losing the currently deployed reference or triggering an accidental deployment.
