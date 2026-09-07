# Changelog

## 0.4.1 — Test portability fix (Linux/Python 3.12)

- `tests/test_select.py`:
  - `from pathlib import Path` and `import importlib.util` imports
    added.
  - Replaces the old `import select as select_mod; importlib.reload(...)`
    pattern with a `_load_select_mod()` helper that uses
    `importlib.util.spec_from_file_location` to load `src/select.py`
    directly by file path under the module name `brain_select_mod`.
- Why: on Linux/Python 3.12 the stdlib `select` is a C builtin
  (no `__file__`, loaded by `BuiltinImporter`) and gets pulled into
  `sys.modules` very early by `urllib`/`subprocess`. Once cached,
  a bare `import select` always resolves to the stdlib — even with
  `sys.modules.pop('select', None)` and `sys.path.insert(0, ...)` —
  because `BuiltinImporter` is consulted before path-based finders.
  Loading by file path under a different module name bypasses the
  cache entirely.
- Effect: `python3 -m unittest discover -s tests` now passes
  83/83 on Linux/Python 3.12 (matches Windows).
- No production code change. No Tuzzina source change. No
  architecture change. No new dependency. Single file touched.

## 0.4.0 — Channel Selection / Strategy Management

- Tuzzina is the source of truth for channel identity. Brain stores
  only Brain-owned strategy, never channel identity.
- `brain-strategies/<integration_id>.yaml`: one file per Tuzzina
  integration. Filename = Tuzzina's id, NOT a brain-side name.
- New `src/channels/strategy.py`: `ChannelStrategy` dataclass with
  Brain-owned policy groups (brand, hashtags, links_policy, content,
  generation, planning, provider_overrides, extras). No `platform`,
  `name`, `provider` (social), or `oauth` fields.
- New `src/channels/strategy_store.py`: load/save/validate with
  path-traversal protection (`^[A-Za-z0-9_\-]{1,128}$` on the id)
  and atomic write (temp + rename). Reusable by select.py and run.py;
  when a Brain API/Frontend replaces the CLI, this is the only
  file-logic to keep.
- `src/channels/profile.py`: new `resolve_strategy(project, strategy,
  channel_meta)` reads the new ChannelStrategy and surfaces
  content/generation/planning in `extra_policies`. Uses the
  shared `_MISSING` sentinel from `channels.strategy` so all
  dataclass sentinels are comparable across modules.
- New `src/select.py`: interactive + non-TTY CLI.
  - Interactive: lists integrations from `GET /public/v1/integrations`,
    lets the operator pick one, then asks only for Brain-owned
    policy fields. Never asks for platform, name, OAuth, or
    provider identity (those come from Tuzzina).
  - Non-TTY: `--stdin-json` reads strategy as JSON for the upcoming
    Brain API/Frontend integration.
  - Validates the picked id against Tuzzina via `get_integration`
    before saving. Read-only on Tuzzina (one GET); one local write.
- `src/run.py`: new `--strategy-by-integration <integration_id>` mode.
  Calls `client.get_integration(integration_id)`, loads the strategy
  from `brain-strategies/`, and runs G1/G2/G3. The platform passed
  to `build_package` comes from Tuzzina's response, never from the
  strategy. Legacy two-arg mode (`<campaign.yaml> <strategy.yaml>`)
  preserved for backward compatibility.
- 20 new tests:
  - `tests/test_strategy_store.py` (8): id validation, save/load
    roundtrip, overwrite, missing-file, mismatched-id, invalid-yaml,
    path-traversal rejection, identity-leak detection.
  - `tests/test_select.py` (7): empty-list discovery, integration
    lookup success/missing, non-TTY save with stubs, traversal
    rejection, missing id, integration not in Tuzzina.
  - `tests/test_run.py` (5): strategy-by-integration loads + validates,
    strategy cannot override platform, missing strategy file stops
    before G1, Tuzzina 404 stops run, legacy two-arg mode still works.
- 0.3.0 files (`channels/juzzir.facebook.yaml` with the old `name`
  + `platform` fields) left in place for backward compatibility;
  the legacy `run.py` two-arg mode still reads it. New work uses
  `brain-strategies/<integration_id>.yaml`.

## 0.3.0 — Tuzzina-Owns-Channel-Identity (architectural correction)

- `ChannelProfile` is no longer a channel definition. It is a Brain
  strategy attached to an existing Tuzzina integration, referenced
  by `integration_id` (Tuzzina's id, NOT a brain-side name).
- Removed from brain: `ChannelProfile.name`, `ChannelProfile.platform`,
  `PlatformSettings` dataclass, `VALID_PLATFORMS` whitelist,
  `__type` validation in the loader. None of these existed in
  Tuzzina's domain; all of them were brain duplicating channel
  identity.
- `client.find_integration(provider, name_contains)` removed.
  It picked a channel by name substring, which is the exact pattern
  the new rule forbids ("never choose the 'first matching channel'
  when multiple channels have the same name").
- New `client.integrations()` returns the list of connected
  integrations from Tuzzina; new `client.get_integration(id)`
  validates a strategy's claimed id against Tuzzina and returns
  the integration record (id, name, identifier, picture).
- `resolve(project, channel, channel_meta)` now takes an OPTIONAL
  `channel_meta` hint dict (from Tuzzina) and surfaces it in the
  resolved config as `channel_meta` for audit/logging. The brain
  never invents identity from it.
- Renamed `platform_settings` -> `provider_overrides` everywhere.
  Plain dict, no DTO mirror, no `__type` enforcement. Tuzzina is
  the owner of provider DTO shape; the brain supplies values only.
- `channels/juzzir.facebook.yaml` rewritten: dropped `name` and
  `platform`; carries `integration_id: cmtr8cxod...` and
  Brain-owned policy only. The channel's name, platform, picture
  are sourced from Tuzzina at runtime.
- `run.py` no longer matches by name; it calls
  `client.get_integration(strategy.integration_id)` to validate and
  obtain the Tuzzina record before any work begins.
- 4 new tests: `integrations_returns_list`, `get_integration_by_id`,
  `get_integration_missing_raises`, `get_integration_empty_id_raises`.
  Test count: 53 -> 57. All pass.

## 0.2.0 — Channel Profile / Channel Strategy Layer

- New `src/channels/` package: `ChannelProfile` dataclass, YAML loader,
  and `resolve(project, channel)` deep-merge.
- Project (campaign) defaults are merged with channel-specific overrides;
  channel wins on every leaf, but absent fields fall back to the project.
  The resolver uses a sentinel object to distinguish "channel did not set
  this field" from "channel explicitly set it to the dataclass default
  literal" (so e.g. an empty banned_words list correctly overrides
  the project list).
- `run.py` now requires two args: `campaign.yaml` + `channel.yaml`.
  The resolved config drives G2 (brand, hashtags, language, links
  policy) and the Tuzzina-facing platform settings.
- `g2.pipeline.build_package` accepts `platform` and `platform_settings`
  parameters so the channel profile can supply them. Pipeline still
  applies normalization, brand rules, hashtag generation, link policy,
  and media fallback. No copy of G2 per platform.
- 18 new tests covering: brand override semantics, hashtag policy
  override, language/links_policy/platform fallback, platform settings
  pass-through, unknown fields preserved in `extras`, end-to-end
  resolved payload shape, and loader validation.
- New `campaigns/juzzir.yaml` and `channels/juzzir.facebook.yaml` for
  the Juzzir Facebook case study. Profile sets a Juzzir-specific
  audience, tone, CTA, and hashtag policy; project stays generic.

## 0.1.0 (initial vertical slice)

- G1 `SourceAdapter` interface + `WebsiteAdapter` (stdlib HTML parser).
  N-configurable, dedup by absolute URL, partial result metadata.
- G2 pipeline: normalize, brand rules, hashtag generator, link policy,
  package assembly. Mock generators (default, no keys) and OpenAI
  generators (optional, requires `OPENAI_API_KEY`).
- G3 `plan()`: pure function, timezone-aware via `zoneinfo`, validates
  `__type` per platform, no IO, no state.
- Tuzzina Public API client: upload, upload-from-url, posts (draft),
  integrations, find_integration. Retry on connection/timeout only;
  no retry on HTTP 4xx/5xx to prevent duplicate publish.
- Campaign YAML loader with validation. Only `brand.name` + non-empty
  `sources[]` are required; everything else has documented defaults.
- 35 unit tests covering G1, G2, G3, Tuzzina client, campaign schema.
- README.md and .env.example.
