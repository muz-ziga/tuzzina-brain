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

## Layering (G1/G2/G3 + Channel Profile)

```
Project (campaign.yaml) ── defaults ─┐
                                    ├── resolve() ── G2 pipeline ── Tuzzina
Channel (channels/*.yaml) ── override┘
```

G1 / G2 / G3 are ONE pipeline, shared across all channels. Channel
Profile is a policy layer that supplies the per-channel brand tone,
audience, hashtag policy, link policy, and platform settings
(__type + DTO fields). The ChannelProfile does NOT redefine Tuzzina
DTOs; it sets the values that get passed through.

Adding a new channel = add `channels/<name>.<platform>.yaml` + a new
entry in `VALID_PLATFORMS` (loader). No code change to G2.

Adding a new policy field (image_policy, video_policy, ...) = add it
to `extras` of the YAML; the resolver preserves it. No code change
required to read it later from a G2 step.

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
