# Changelog

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
