# Changelog

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
