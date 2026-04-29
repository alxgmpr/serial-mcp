# PyPI Release Automation Design Spec

**Date:** 2026-04-29
**First release via this pipeline:** 0.4.0

## Context

`serial-mcp` is published to PyPI manually. The current state is out of sync: `pyproject.toml` says `0.4.0` but PyPI's latest is `0.3.0`. There are zero git tags — releases have been bumps to `version` in `pyproject.toml` followed by an out-of-band `python -m build && twine upload`. This drift is the symptom we're fixing: there is no single source of truth, no enforcement that PyPI matches a tagged commit, and no record on GitHub of which commit shipped.

## Goals

- Cut a release with a single `git tag` + `git push` after a version bump
- Make tag, `pyproject.toml` version, PyPI version, and GitHub Release always agree
- Publish without long-lived secrets in the repo
- Distribute the `.mcpb` bundle alongside each release

## Non-goals

- Auto-generating version numbers from git history (rejected — keeps `pyproject.toml` as the explicit source)
- TestPyPI dry-run jobs (rejected — overkill for project size)
- Changelog automation beyond GitHub's auto-generated release notes
- Mirroring wheel/sdist as GH release assets (PyPI hosts them; would be redundant)

## Architecture

A new workflow `.github/workflows/release.yml` triggered on tag pushes matching `v*.*.*`. Five jobs run in sequence; any failure short-circuits the publish.

```
git push v0.5.0
       │
       ▼
┌──────────────┐
│   verify     │  tag matches pyproject.toml version
└──────┬───────┘
       ▼
┌──────────────┐
│    test      │  ruff check + ruff format --check + pytest matrix (3.10–3.13)
└──────┬───────┘
       ▼
┌──────────────┐
│    build     │  python -m build  +  scripts/build-mcpb.sh
│              │  → uploads dist/*.whl, dist/*.tar.gz, serial-mcp.mcpb as artifacts
└──────┬───────┘
       ▼
┌──────────────┐
│ publish-pypi │  pypa/gh-action-pypi-publish (OIDC, environment: pypi)
│              │  ⏸ environment requires manual approval before run
└──────┬───────┘
       ▼
┌──────────────┐
│github-release│  softprops/action-gh-release — auto-generated notes,
│              │  attaches serial-mcp.mcpb
└──────────────┘
```

### Job: verify

Reads `version` from `pyproject.toml` and compares to `${{ github.ref_name }}` minus the leading `v`. Fails with a clear message on mismatch. Inline Python script using `tomllib` (stdlib in 3.11+; runner has 3.12).

### Job: test

Mirrors `ci.yml`'s lint + matrix-test jobs. Re-running on the tagged commit is intentional belt-and-suspenders: catches the case where someone tags a commit that was never on `main`, or where main has drifted since CI passed.

### Job: build

Two outputs:

- `python -m build` → `dist/*.whl` + `dist/*.tar.gz`
- `bash scripts/build-mcpb.sh` → `serial-mcp.mcpb` at repo root (existing script — no changes needed)

All three uploaded via `actions/upload-artifact@v4`. Subsequent jobs download what they need.

### Job: publish-pypi

- Runs inside GitHub Environment `pypi` (configured in repo settings, requires reviewer approval)
- Uses `pypa/gh-action-pypi-publish@release/v1` with `id-token: write` permission for OIDC
- Downloads the `dist/` artifact and uploads it
- No `password:` parameter — Trusted Publishing handles auth

### Job: github-release

- Depends on `publish-pypi` (only create the release once PyPI publish succeeded)
- Uses `softprops/action-gh-release@v2`
- `generate_release_notes: true` — GitHub generates notes from commits since the previous tag
- Attaches `serial-mcp.mcpb` from the build artifact

## Manual setup (one-time, by repo owner)

1. **PyPI Trusted Publisher** — at https://pypi.org/manage/project/serial-mcp/settings/publishing/, add a Trusted Publisher with:
   - Owner: `alxgmpr`
   - Repository name: `serial-mcp`
   - Workflow filename: `release.yml`
   - Environment name: `pypi`
2. **GitHub Environment** — in repo Settings → Environments, create `pypi` and enable "Required reviewers" with the repo owner as reviewer. This adds an explicit approval click between the `build` and `publish-pypi` jobs.

These steps are documented in `RELEASING.md` (see below) so they can be replayed if PyPI credentials/settings ever need to be reconstructed.

## Release procedure (post-setup)

1. Edit `version` in `pyproject.toml` (e.g. `0.4.0` → `0.5.0`)
2. Commit on `main` (directly or via PR)
3. `git tag v0.5.0 && git push origin v0.5.0`
4. Watch the Actions tab; approve the `pypi` deployment when prompted
5. Done — PyPI publish + GitHub Release happen automatically

## Initial drift fix

After the workflow file lands and the PyPI Trusted Publisher + GitHub Environment are configured:

1. Confirm `pyproject.toml` still says `0.4.0`
2. `git tag v0.4.0 && git push origin v0.4.0`
3. Approve the deployment

This ships `0.4.0` to PyPI (closing the drift) and validates the full pipeline end-to-end before any future release.

## Documentation

New file: `RELEASING.md` at repo root, containing:

- One-time setup steps (Trusted Publisher + Environment)
- Release procedure
- What to do if the publish fails partway (e.g. PyPI succeeded but GH release failed — re-run only the failed job; tag is consumed)
- Note on tag immutability: PyPI rejects re-uploads of the same version, so a botched release means bumping to the next patch and re-tagging

CLAUDE.md gets a one-line pointer to `RELEASING.md` under a new "Releasing" section.

## Error handling

| Failure | Behavior |
|---|---|
| Tag/version mismatch | `verify` job fails, no publish |
| Lint or test failure | `test` job fails, no publish |
| Build failure | `build` job fails, no publish |
| Reviewer rejects deployment | `publish-pypi` never runs |
| PyPI rejects (e.g. version exists) | `publish-pypi` fails loudly; user must bump and re-tag |
| GH release creation fails after PyPI publish | PyPI is already published; user re-runs the `github-release` job manually |

## Testing the workflow itself

- The lint/test/build jobs are exercised on every tag, so the only path that *can't* be tested without going live is the OIDC publish step
- First validation: cutting `v0.4.0` to fix the drift
- Future workflow edits can be tested by pushing temp tags like `v0.4.0-test1` against a fork, but for this project the small surface area doesn't warrant it

## Risks

- **Wrong PyPI Trusted Publisher config** — if owner/repo/workflow/environment don't match exactly, OIDC exchange fails with an opaque error. Mitigation: test on `v0.4.0` first, before any future release depends on the pipeline.
- **`scripts/build-mcpb.sh` assumes Linux runner has required tools** — currently it's a bash script; will need verification that the GH-hosted `ubuntu-latest` runner has everything it needs. Will check during implementation.
- **First-run experience** — until the user completes the manual PyPI/Environment setup, the workflow file exists but tagging will fail at the publish step. `RELEASING.md` will lead with the setup steps to make this obvious.
