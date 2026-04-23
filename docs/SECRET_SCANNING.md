# Secret Scanning

This repo uses `gitleaks` to reduce accidental secret leaks.

## What's configured

- `.gitleaks.toml` project config (extends default rules).
- GitHub Action: `.github/workflows/gitleaks.yml` (runs on push/PR).
- Local pre-commit support: `.pre-commit-config.yaml`.

## Local setup

1. Install pre-commit:
   - `pip install pre-commit`
2. Install hooks:
   - `pre-commit install`
3. Run once manually:
   - `pre-commit run --all-files`

## Direct gitleaks scan

- Full working tree:
  - `gitleaks detect --source . --config .gitleaks.toml`
- Staged changes:
  - `gitleaks protect --staged --config .gitleaks.toml`

## If scanner finds a leak

1. Revoke/rotate the secret immediately.
2. Remove secret from code/history.
3. Re-run gitleaks and verify clean output.

