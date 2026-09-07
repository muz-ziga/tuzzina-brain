# Changelog

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
