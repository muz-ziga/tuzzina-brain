# Agent notes for tuzzina-brain

## Scope (do not exceed)

This repo is the **intelligence/generation layer** for Tuzzina. It
produces inputs (text + public media URLs + dates) and pushes them
through Tuzzina's Public API. It owns **no** publish state, **no**
scheduler, **no** DB, **no** direct R2 access.

When extending this repo, add a new `SourceAdapter` (in
`src/g1/<name>.py`) or a new `ImageGenerator`/`TextGenerator`
(in `src/g2/generators.py`). Do not add new ad-hoc HTTP clients for
Tuzzina; extend the existing one.

## Build discipline

Before every commit:

```bash
python3 -m compileall -q src
python3 -m unittest discover -s tests
```

## Secrets

Secrets only via environment variables. Never commit, print, or store
secrets in YAML or any tracked file.

## Deployment

Local commit -> push -> `git pull` on `/opt/projects/tuzzina-brain`.
No `scp` of source files.
