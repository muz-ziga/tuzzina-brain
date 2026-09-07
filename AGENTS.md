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
Tuzzina (Public API)
   │
   │  GET /public/v1/integrations  (channel discovery)
   │  GET /public/v1/integration-settings/:id
   │  POST /public/v1/upload-from-url
   │  POST /public/v1/posts
   ▼
Brain
   │
   │  select.py      ── CLI: read Tuzzina, pick one, save strategy
   │  run.py        ── CLI: read Tuzzina, load strategy, run pipeline
   │  brain-strategies/<integration_id>.yaml  (Brain-owned policy only)
   │
   │  G1 (sources) -> G2 (brand + policy merge) -> G3 (plan)
   │
   │  upload-to-R2 via Tuzzina Public API
   │  create-posts via Tuzzina Public API
   ▼
Tuzzina (Scheduler / Temporal / provider adapters)
```

G1 / G2 / G3 are ONE pipeline, shared across all channels. Channel
Strategy is a policy layer that supplies the per-channel brand tone,
audience, hashtag policy, link policy, content/generation/planning
policies, and per-provider overrides.

## Strategy file convention

`brain-strategies/<integration_id>.yaml` — one file per Tuzzina
integration. The filename IS the Tuzzina integration id, so two
strategies can never collide and the filename is a reference, not
an identity.

A strategy file may contain only Brain-owned policy. It MUST NOT
contain `platform`, `name` (channel name), `provider` (social
platform), `oauth`, or any `providerIdentifier`. The platform comes
from `get_integration(integration_id)` at runtime.

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
