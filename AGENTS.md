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

## Ownership rules (architectural)

```
Tuzzina owns:           Brain owns:
  integration identity    brand voice / audience / tone
  platform identity       hashtag / link / CTA policy
  OAuth                    content pillars, source strategy
  provider DTOs            generation policy
  media + R2               planning preferences (days/times/TZ)
  scheduler / Temporal     extras (forward-compat metadata)
  publish state
  analytics
```

Brain MUST NOT:
- define or duplicate channels (no `name: "X"` channel definitions)
- carry `platform:` as a brain-owned field
- redefine Tuzzina provider DTOs in a parallel system
- access the Tuzzina DB directly
- pick a "first matching channel" by name when multiple share a name

A Brain Channel Profile is a STRATEGY attached to a Tuzzina
integration. The strategy YAML carries `integration_id` (the only
bridge to Tuzzina). At runtime, the brain calls
`client.get_integration(integration_id)` to verify the channel
exists, and uses Tuzzina's response (name, identifier, picture) as
audit metadata — never as the source of identity.

## Layering (G1/G2/G3 + Channel Strategy)

```
Project (campaign.yaml) ── defaults ─┐
                                    ├── resolve(project, channel, channel_meta)
Channel (channels/<integration_id>.yaml) ── strategy ─┘
            ↓
        G2 pipeline (one shared)
            ↓
        Tuzzina Public API (upload + posts)
```

G1 / G2 / G3 are ONE pipeline, shared across all channels. Channel
strategy is a policy layer that supplies the per-channel brand tone,
audience, hashtag policy, link policy, and per-provider overrides.
The Channel strategy does NOT redefine Tuzzina DTOs; it sets the
values that get passed through.

Adding a new channel = add `channels/<integration_id>.yaml` (referencing
an existing Tuzzina integration). No code change to G2.

Adding a new policy field (image_policy, video_policy, ...) = add it
under `extras:` in the YAML; the resolver preserves it. No code change
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
