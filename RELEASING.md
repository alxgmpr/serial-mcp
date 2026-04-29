# Releasing serial-mcp

Releases are automated by `.github/workflows/release.yml`, triggered on tag pushes matching `v*.*.*`. Each release produces:

- A PyPI publish (sdist + wheel) via PyPI Trusted Publishing — no API tokens
- A GitHub Release with auto-generated notes
- An attached `.mcpb` bundle on the GitHub Release

## One-time setup

These steps must be completed by a maintainer before the first automated release will succeed.

### 1. PyPI Trusted Publisher

Go to https://pypi.org/manage/project/serial-mcp/settings/publishing/ and add a Trusted Publisher with:

| Field               | Value             |
| ------------------- | ----------------- |
| Owner               | `alxgmpr`         |
| Repository name     | `serial-mcp`      |
| Workflow filename   | `release.yml`     |
| Environment name    | `pypi`            |

### 2. GitHub Environment

In the repo → Settings → Environments, create an environment named `pypi`. Enable **Required reviewers** and add yourself. This adds a manual approval click between the build and publish steps — a final guard against accidental tag pushes.

## Cutting a release

1. Bump the version in **both** files:
   - `pyproject.toml` → `[project] version`
   - `manifest.json` → `version`
2. Commit on `main` (direct push or merged PR).
3. Tag and push:
   ```bash
   git tag v0.5.0
   git push origin v0.5.0
   ```
4. Open the Actions tab on GitHub. The workflow runs `verify` → `lint` → `test` → `build`, then pauses for environment approval.
5. Click **Review deployments** → **Approve** to release.
6. PyPI publish + GitHub Release happen automatically. Done.

The version in the tag, `pyproject.toml`, and `manifest.json` must match exactly, or the `verify` job fails fast before anything is built.

## Recovering from a botched release

| Failure point                                  | Recovery                                                                                                   |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `verify` fails (version drift)                 | Fix the offending file, commit, delete and re-push the tag. Nothing was published.                          |
| `lint` / `test` / `build` fails                | Fix the issue, commit, delete and re-push the tag. Nothing was published.                                  |
| Approval was rejected                          | Re-run the workflow when ready. Nothing was published.                                                     |
| `publish-pypi` fails (e.g. flaky network)      | Re-run only the failed job from the Actions UI. Trusted Publishing tokens are minted on each run.          |
| `github-release` fails after PyPI succeeded    | PyPI version is final. Re-run only the `github-release` job, or create the release manually with `gh release create`. |
| PyPI rejects "version already exists"          | PyPI does not allow re-uploads of the same version. Bump to the next patch (e.g. `v0.5.1`) and re-tag.     |

To delete and re-push a tag:

```bash
git tag -d v0.5.0
git push origin :refs/tags/v0.5.0
# fix the issue, commit, then re-tag
git tag v0.5.0
git push origin v0.5.0
```
