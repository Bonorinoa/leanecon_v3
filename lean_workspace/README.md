# LeanEcon Lean Workspace

This workspace is the Lean kernel trust boundary for LeanEcon v3. It is pinned by:

- `lean-toolchain`: `leanprover/lean4:v4.31.0`
- `lakefile.toml`: LeanEcon package and mathlib dependency
- `lake-manifest.json`: resolved mathlib and transitive dependency revisions

## Command Lanes

Use the lightest lane that matches the decision you are making.

| Lane | Purpose | Commands | Expected Use |
| --- | --- | --- | --- |
| Developer edit-loop gate | Check ordinary code/proof edits quickly. | `PYTHONPATH=. ./.venv/bin/python -m pytest -o addopts=''` from repo root, then `cd lean_workspace && lake build Mathlib LeanEcon`. | Before/after focused local changes and on PR CI. |
| Local release-candidate gate | Decide whether the narrow alpha release denominator is locally green. | Developer edit-loop gate plus `PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --claim-set tier1_core_preamble_definable --budget-profile release --output-dir /private/tmp/leanecon-alpha-tier1 --allow-unready`. | Local release-candidate decisions. |
| Release-image gate | Prove the image/cache state can support deployment. | `cd lean_workspace && lake exe cache get`, `cd lean_workspace && lake build`, and `docker build --pull=false -t leanecon-v3:ci .` from repo root. | Main-branch/manual CI and before any hosted redeploy. |
| Hosted deployment gate | Validate the already-built image in production. | Production `/health`, `/metrics`, `/metrics/prometheus`, bounded job/SSE smoke, and one release-profile proof smoke. | Sprint 34 hosted deployment only. |

The developer edit-loop uses explicit `lake build Mathlib LeanEcon` library
targets rather than raw `lake env lean LeanEcon.lean`. Those targets work on a
fresh CI cache because Lake materializes the aggregate `Mathlib.olean` required
by checked-in theorem stubs and builds the project source tree before checking
root module imports.

## Full Lake Build Policy

Run `lake build` when the work changes infrastructure or release-image state:

- Lean package topology, imports, generated Lean source, `lakefile.toml`,
  `lake-manifest.json`, or `lean-toolchain`
- Docker/Railway/CI behavior that controls the Lean workspace
- release-image readiness before hosted deployment

Do not make `lake build` part of ordinary proof-attempt or edit-loop work unless
you are explicitly validating the release-image lane. It may replay a large
mathlib trace graph when the local cache is cold.

## Cache Expectations

Before a full build, pre-warm mathlib artifacts:

```bash
cd lean_workspace
lake exe cache get
lake build
```

CI caches:

- `~/.elan`
- `lean_workspace/.lake/packages`
- `lean_workspace/.lake/build`

The release Docker image copies both `/root/.elan` and `/lean_workspace` from
`ghcr.io/bonorinoa/leanecon-lean-base:latest`, places `/root/.elan/bin` on
`PATH`, and checks `lean --version`, `lake --version`, and
`lake env lean LeanEcon.lean` during image build. The base image is responsible
for carrying the pre-warmed Lake/mathlib state; the application image should not
discover a missing Lean toolchain or workspace for the first time at Railway
runtime.

## LSP Tooling

`lean-lsp-mcp` is used only by the mathlib-native frontier path. The runtime
checks for either a local `.venv/bin/lean-lsp-mcp` binary or `uvx`, and the
Docker image installs `uv` so `uvx lean-lsp-mcp` is available.

LSP availability is observable in `/health`, but it is not part of the alpha
release denominator. The release denominator remains
`tier1_core_preamble_definable`; frontier diagnostics stay separate.
