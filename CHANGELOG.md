# Changelog

## 0.24.0 — Phase 8: content format allowlist gate

- New `src/research/formats.py`: closed executable vocabulary
  (`text`, `text+image`, `text+video`, `link+text` — derived
  1:1 from Tuzzina post primitives; no `story`/bare-`image`
  invention) + pure `format_for()` mapping + `select_format()`
  allowlist check + `allowed_from_distribution()` (count > 0
  allows; explicitly empty denies; missing row unconstrained).
- `GenerationPlan.format` (additive) + `CycleResult.format` /
  `format_reason` observability; `run_cycle` gates after
  eligibility — disallowed formats stop cleanly (commit, no
  error, reason recorded), never silently downgraded.
  Provider-level support stays Tuzzina-validated.
- `TuzzinaClient.get_content_distribution()` (GET
  `/public/v1/content-distribution/:id`, 404 -> None) +
  `run_cycle.py` attaches live distribution to policy
  (fetch failure warns, continues unconstrained).
- 17 tests: formats unit, cycle gate (allow/block/empty),
  client contract, CLI end-to-end gating. 433/433 pass (was
  399). No scheduler/queue/cron/publishing changes; Strategy
  untouched; counts stored but cross-run caps deferred
  (documented, needs usage counters).

## 0.23.0 — Phase 7: one-shot cycle CLI for automation triggers

- New `src/run_cycle.py`: machine entry for unattended runs
  (campaign + strategy-by-integration -> full research cycle
  -> media resolve + inject, unless --dry-run). Flags:
  --mock/--openai, --post-mode, --dry-run (zero writes AND
  forces the memory state store), --run-id (default generated),
  --state-store memory|tuzzina. Live runs require --openai;
  --mock with live writes is refused (exit 2).
- Machine contract: the LAST stdout line is always
  `BRAIN_RESULT {...}` (run_id, status, sources_checked,
  items_new, eligible, intents, post_ids, committed, error);
  exit codes mirror run.py (0 ok incl. no-op, 1 cycle error,
  2 config, 3 Tuzzina, 4 platform).
- Reuses run.py loaders/client/generators/media-resolve and
  the research cycle unchanged (no duplication); research
  and analysis models follow the --mock/--openai mode.
  Per-role multi-vendor selection stays constructor-level
  (adapters ready; server-side assignment resolution is a
  future seam, documented in-code).
- 8 tests (`tests/test_run_cycle.py`, faked client/transport):
  envelope, exit codes, store selection, run-id, mock-live
  refusal, dry-run zero-write proof, live post_ids, tuzzina
  commit path, secrecy. 399/399 pass (was 391). Proven live
  locally: real CLI --dry-run --mock against a stub API ->
  exit 0 with a valid envelope. No scheduler/cron/daemon in
  Brain; no publishing/state changes beyond the cycle's own
  rules.

## 0.22.0 — Phase 6: durable monitoring state (no local DB)

- New `src/tuzzina/state_store.py`: `TuzzinaStateStore`
  (durable `StateStore` over Tuzzina-owned rows via two narrow
  Public API endpoints; 404 loads as fresh, other failures
  raise; no caching, no secrets, last-write-wins documented)
  plus defensive `state_to_dict`/`state_from_dict` (unknown
  keys dropped, wrong types fall back).
- `TuzzinaClient` gains `get_research_state` /
  `save_research_state` (GET+PUT
  `/public/v1/research-state/:sourceId`, URL-encoded ids,
  PUT idempotent).
- `research/monitor.py`: shared `classify_candidates()`
  (pure NEW/UNCHANGED/UPDATED over pre-normalized entries);
  `collect()` reuses it (semantics byte-identical, suite
  proves it).
- `research/cycle.py`: website path now classifies through
  the shared helper (identity = canonical page URL, hash =
  title+text) instead of always-NEW; pending website state
  commits on the same clean-end rule as feeds.
- 15 tests (`tests/test_tuzzina_state_store.py`, faked
  transport): contract, HTTP paths/methods, 404-fresh,
  roundtrip durability, encoding, secrecy, isolation,
  classify parity. 391/391 pass (was 376). No scheduler/
  queue/cron/DB-in-Brain/publishing changes.

## 0.21.0 — Phase 2: video delegation via existing Tuzzina pipeline

- New `TuzzinaClient.generate_video(video_type, output,
  prompt, params, timeout=600)` (`src/tuzzina/client.py`):
  single-attempt POST /public/v1/generate-video {type, output,
  customParams} (never retried — a retry could double-spend
  ai_videos credits), long timeout (Tuzzina polls the provider
  inside the request), returns stored {id, path}. Fail-closed
  TuzzinaError on HTTP/timeout/malformed/no-ref; no fallback,
  no bytes, no upload, no provider calls from Brain.
- `VideoRequest` gains opaque `video_type` (Tuzzina TEMPLATE
  identifier, validated server-side — no Brain catalog),
  `output`, and `params` (into customParams untouched);
  `plan_generation()` accepts optional `video_config`.
- Cycle wiring (`src/research/cycle.py` only): video config
  from policy media section; optional `video_resolve` callable
  executes the request (execution phase binds the client);
  success records {id, path} in `CycleResult.video_media`;
  failure fails the cycle with zero state advance; absent
  executor keeps prior defer-and-commit behavior.
- Type selection (`veo3` vs `image-text-slides`) already works
  caller-side today (verified fresh against current Tuzzina:
  `type` passthrough, no code change needed anywhere).
- 21 tests (`tests/test_video_delegation.py`, faked
  transport): contract, passthrough, failures, timeout,
  no-retry, no-leakage, cycle attach/fail/skip. 376/376 pass
  (was 355). Image/text/research/analysis untouched.
  LIVE: BLOCKED — prod holds only OPENAI_API_KEY; neither
  video template has credentials (env-name scan, zero calls,
  zero spend).

## 0.20.0 — Phase 1: multi-provider LLM execution core

- New `src/llm/adapters.py`: `complete(system, user, *,
  temperature, max_tokens, timeout) -> str` contract +
  `_post_json` (HTTP+JSON+timeout+status normalization only,
  fail-fast, timeouts propagate raw) + `OpenAIAdapter`
  (Bearer, messages[], choices extraction) + `AnthropicAdapter`
  (x-api-key + version header, top-level system, required
  max_tokens, content[] extraction, stop_reason gating:
  non-end_turn is loud, never trusted) + closed `ADAPTERS`
  dispatch (`resolve_adapter`; unknown keys fail closed; code
  pointers only, no models/capabilities/limits).
- Research/Analysis/Text roles refactored onto adapters with
  UNCHANGED interfaces, prompts, validators, timeouts, and
  error categories (adapter=`None` builds the same OpenAI
  default as before; explicit adapter wins entirely). All
  role assignment is constructor-level: each role independently
  takes adapter+credential+model (proven: three simultaneous
  distinct assignments; text OpenAI->Anthropic and research
  Anthropic->OpenAI switch with zero other-role changes).
- Vendor protocol details exist ONLY in `llm/adapters.py`
  (grep-verified). 44 tests (`tests/test_llm_adapters.py`,
  faked transport, zero live calls): both protocols, refusal
  semantics, dispatch closure, independence, switching,
  contract stability, secrecy. 355/355 pass (was 311).
  No Tuzzina/Postiz/registry/DB/UI/cron/image/video changes.

## 0.19.0 — R6: generation orchestration (decision only)

- New `src/research/generation.py`: `GenerationPlan`
  (text request always when eligible; image iff
  media_intent == image; video iff == video, intent-only) +
  `plan_generation()` (pure) + `shape_item_for_g2()` (plan
  title/text over preserved ids/images/refs/hashes) +
  `image_gen_for()` (None unless image requested). Formats
  stay post + none|image|video; no story/reel invention.
- Cycle integration (`src/research/cycle.py` only): plan step
  between analysis and G2; G2 now consumes opportunity-shaped
  inputs; image engine gated so media:none stays text-only
  (previously the pipeline added generator entries whenever an
  engine was present — the R6 gap, now closed); video intents
  recorded in `CycleResult.video_deferred`, never attempted,
  never silently dropped; state commits normally.
- No engine runs in the plan layer; text runs inside G2 as
  before; media resolution stays in run.py; video has no
  executor anywhere (documented). G2/G3/research/analysis/
  strategy/Tuzzina semantics untouched.
- 18 tests (`tests/test_generation_plan.py`, offline): plan
  decisions, gating, deferral, shaping, policy preservation,
  no-execution-constructs guard, determinism, no-Tuzzina-
  contact. 311/311 pass (was 293). No cron/scheduler/DB/UI/
  provider/model-registry/bytes/publishing.

## 0.18.0 — R5: end-to-end research cycle wiring (intelligence only)

- New `src/research/cycle.py`: `run_cycle(sources, store,
  policy, schedule, now, ...)` — one deterministic pass:
  collect (rss/youtube via monitor NEW-gate, website via
  extract as always-NEW, documented) -> research -> analysis
  -> eligible ? existing G2 -> existing G3 -> CanonicalIntent
  (: ineligible/empty stop cleanly, no G2, no Tuzzina call).
  All roles injectable, Mocks by default (offline).
- Commit rule: per-source monitor states commit ONLY on clean
  end (full success, ineligible stop, or no-NEW stop); ANY
  downstream raise commits nothing (at-least-once redelivery).
  Collector errors are per-source (never block healthy ones).
- STOPS at CanonicalIntent with media UNRESOLVED (generator
  objects/URLs, never {id,path}) and no client: nothing can
  publish. Single opportunity per cycle (R4 contract,
  documented). Website has no dedup (absence documented, not a
  second engine). G2/G3/monitor/research/analysis semantics
  untouched; run.py/strategy/Tuzzina untouched.
- 16 tests (`tests/test_research_cycle.py`, faked transport,
  fixed clock): full/empty/updated/multi-source cycles,
  traceability, eligible/ineligible paths, intent chain,
  zero-Tuzzina-contact audit, 4 failure blocks, website
  semantics, determinism. 293/293 pass (was 277). No cron/
  DB/publish/generation/UI.

## 0.17.0 — R4: content opportunity analysis ("should we cover it?")

- New `src/research/analysis.py`: `AnalysisModel` interface
  (`analyze(result, policy, items=None) -> ContentOpportunity`),
  `ContentOpportunity` (eligible, topic, angle, rationale,
  FACT-only facts, source_item_ids, format, media_intent,
  audience, language, priority, confidence, constraints,
  model, meta). One call yields at most one opportunity;
  item-level novelty stays in research/monitor.py.
- `MockAnalysisModel` (deterministic: eligible iff >=1 FACT and
  pillar fit; facts never launder inference; media via explicit
  prefer, visual material, or min_items demand — video only via
  explicit prefer). `OpenAIAnalysisModel` (own prompt/contract,
  strict validation incl. known-ids, eligible-requires-facts,
  enum checks; timeout/model/invalid-output -> AnalysisError).
  Policy is a plain Brain-vocabulary dict; provider-shaped keys
  are ignored by construction (tested with poisoned policy).
- Constraints carry G2-must-respect language/audience/pillar;
  confidence reuses high|medium|low. No caption/hashtag/CTA/
  schedule/media-id in output (G2 owns those downstream).
- 31 tests (`tests/test_analysis_model.py`, faked transport,
  offline): all 18 required cases + boundary guards (no
  execution duties, single transport call, no secrets, no
  registries, roles in separate files). 277/277 pass (was
  246). G1/R1/R2A/R3/G2/G3/Strategy/Injection/engines/
  Tuzzina untouched. No cron/DB/publish/generation.

## 0.16.0 — R3: structured Research Model ("what did we find?")

- New `src/research/models.py`: `ResearchModel` interface
  (`research(items) -> ResearchResult`), `Finding` (statement,
  kind fact|inference, confidence high|medium|low, item_ids,
  excerpt) + `ResearchResult` (summary, findings, topics,
  entities, item coverage, published range, model, meta).
  Every finding traces to input ids; fact vs inference is
  explicit so Analysis can never mistake a guess for a source.
- `MockResearchModel` (deterministic: 1 fact/item + 1 shared
  low-confidence inference) and `OpenAIResearchModel` (separate
  role/prompt/contract from text generation, same stdlib
  transport style; strict envelope unwrap + schema validation;
  failures raise ResearchError: empty/malformed input, timeout,
  model error, invalid output). Bounded context (20 items,
  2000 chars, truncation recorded). No fetch, no state, no
  Tuzzina, no publish/schedule/media duties (guard-scanned).
- 23 tests (`tests/test_research_model.py`, faked transport, no
  network/key): all 12 required fixture cases + boundary
  guards. 246/246 pass (was 223). G1/R1/R2A/G2/G3/Strategy/
  Injection/Tuzzina untouched. No Analysis Model (R4).

## 0.15.0 — R2A: YouTube public channel collector (collection only)

- New `src/g1/youtube.py`: `YouTubeFeedAdapter` (source type
  "youtube") over the public channel Atom feed (live-proven R2,
  no key/OAuth/API). Channel_id ONLY (`^UC[A-Za-z0-9_-]{22}$`,
  fail-fast; no handle resolution, no URL override, no search).
  Identity = yt:videoId, description from media:description,
  thumbnails ride existing `images[]`, published/updated
  preserved. Failure -> partial reason, never raises.
- Extended `src/g1/rss.py` (shared, no fork): Media RSS
  namespace (media:group/description/thumbnail), yt:videoId
  preference, shared `to_source_item()` mapper (RssAdapter now
  uses it; behavior identical plus thumbnails when present).
- Monitoring/state untouched: same `collect()` + NEW/UNCHANGED/
  UPDATED + commit lifecycle over the same RssItem contract.
- KNOWN LIMIT (documented, no workaround): feed carries ~15
  recent entries, no pagination; beyond-window videos between
  polls can be missed.
- 14 tests (`tests/test_youtube_collector.py`, real-shape
  fixtures, fake transport): fields, identity, monitor
  lifecycle, 15-entry completeness, failures, SSRF/channel
  validation, no-auth constructs. 223/223 pass (was 209).
  No FB/IG/research/cron/DB/UI/Tuzzina changes.

## 0.14.0 — R1: RSS collector + monitoring state (collection only)

- New `src/g1/rss.py` (stdlib only: urllib, xml.etree, socket,
  ipaddress): fetch (25s timeout, 1MB cap, 5-redirect cap,
  http/https-only, non-public targets refused pre-fetch) +
  parse (RSS 2.x + Atom, tag-stripped summaries, RFC822/ISO
  dates) + normalize (canonical URLs, utm/fragment stripped) +
  stable identity (guid > link > content-hash) + sha256 content
  hash. Failures raise `FeedError(reason)`; `RssAdapter` maps to
  `SourceItem` (with new `item_id`/`content_hash` contract
  fields) or partial `ExtractionResult`. No LLM, no decisions.
- New `src/research/monitor.py`: `SourceState` (seen map +
  advisory high-water mark + checked/error stamps, plain data),
  `StateStore` protocol + `MemoryStateStore`, `collect()` with
  CHECK→COLLECT→CLASSIFY→RETURN→COMMIT (cursor advances only on
  commit; at-least-once, never silent loss), NEW/UNCHANGED/
  UPDATED kinds (UPDATED never auto-new), horizon + max_items
  window inputs, wall-clock-free (`now` is an explicit string).
- Intermediate-unseen items surface as NEW (the autopost
  single-newest loss mode is explicitly not repeated).
- 26 fixture tests (`tests/test_rss_collector.py`, fake
  transport/DNS, zero network): both formats, identity
  precedence, idempotency, no-commit-no-advance, edits,
  horizon, 11 failure modes, SSRF refusals, adapter mapping.
- run.py/strategy/G1-website/G2/G3 untouched (no CLI wiring
  until a state backend exists). 209/209 pass (was 183).
  No Tuzzina changes. No cron/DB/LLM/UI.

## 0.13.1 — Live-proven MCP failure fidelity (Phase 1C)

- `TuzzinaClient.generate_image`: when the live tool result
  carries `isError: true`, Brain now raises with the server's
  cause text (capped) instead of the generic "no media reference"
  message. Proven by the live MCP handshake: the real
  `generateImageTool` failure arrives as `{result: {content:
  [{text}], isError: true}}` with `TOOL_EXECUTION_FAILED` detail;
  the old message masked it. Fail-closed behavior unchanged (no
  fallback, no retry, no upload).
- Same change corrects `_mcp_url` (proven live in the same
  session): MCP is served through the deployment base URL
  (`base + "/mcp"`); the stripped root hits the frontend auth
  guard (307). Regression test updated to the live-proven URL.
- Live finding (Tuzzina-side, not Brain): production image
  generation currently fails inside `MediaService.generateImage`
  (OpenAI HttpException, `TOOL_EXECUTION_FAILED`); `useCredit`
  rolls the credit row back on throw, so zero credit consumed and
  zero media rows created. Brain parser/envelope fully compatible.
- Tests: isError fixture replaying the exact live shape. 183/183
  pass (was 182). No Tuzzina changes. No UI.

## 0.13.0 — Migration Step 5: G2 intelligence boundary (audit + isolate)

- Forensic verdict: G2 already holds decisions/policy only. No
  execution removed because none duplicated Tuzzina: image bytes
  went in Step 2, maxLength truth in Step 4, validity lives in
  Tuzzina. No video execution exists in Brain (policy-only stands).
- `OpenAITextGenerator` classified TEMPORARILY KEEP with an explicit
  isolation boundary (module + class docstrings): it owns just the
  LLM call (title/summary/brand in, text out) and must never acquire
  scheduling, media, OAuth, publishing, or provider truth. Kept
  because no machine-facing Tuzzina text endpoint accepts full Brain
  context (no Public API text route, no MCP text tool, internal
  /generator is session-authed and brand-unaware, Agent chat has no
  deterministic contract). Deletion condition documented.
- New `tests/test_g2_boundary.py` (7 tests): temporary status
  documented, execution-duty isolation scan, Brain-only text
  context, `--openai` wiring carries no bytes-capable image engine,
  no video execution, media intent representable with zero
  execution, Mock determinism.
- No behavior change. G1/G3/Strategy/Injection/image-delegation/
  scheduling/adapters untouched. 182/182 pass (was 175).
  No Tuzzina/Postiz changes. No live calls. No UI. No new subsystem.

## 0.12.0 — Migration Step 4: slim platform adapters (translators only)

- Deleted `capabilities()` from both adapters and the
  `injection/capabilities.py` status lens: no static maxLength
  (63206/2200), post_types, media min/max/carousel flags, or
  media_delivery mirrors remain anywhere in `src` (verified by
  grep). Adapters keep translation only: text assembly, media
  passthrough, link branching, `link_setting()`, and the
  `default_settings()` skeleton required by the API contract.
- Removed Brain's second validation layer: the post-kind gate
  and story-attachment gate in `InjectionService` are gone.
  Tuzzina's POST /posts validation refuses authoritatively and
  its errors propagate as TuzzinaError (a text-only story now
  flows to Tuzzina instead of being refused on pinnable data).
- Live bound: `_execute` fetches `maxLength` once per run via
  the existing `get_integration_settings` (outside pure policy
  code) for pre-truncation; unavailable/malformed -> no cut,
  Tuzzina stays authoritative. Not consumed inside adapters
  (they stay sync, deterministic, network-free) and not per
  post (Tuzzina owns refusal).
- `shape_text` assembles without truncation; `shape_media` passes
  refs through (IG empty/carousel enforcement removed).
- Tests: translator proofs, anti-registry guards, link_setting,
  live-bound + fallback, refusal-ownership change. 175/175 pass
  (was 171). G1/G3/image/scheduling untouched. No Tuzzina/Postiz
  changes. No live calls. No UI. No new subsystem.

## 0.11.0 — Migration Step 3: policy-only Strategy (provider_overrides removed)

- Removed `provider_overrides` from `ChannelStrategy`
  (`src/channels/strategy.py`), the store (`strategy_store.py`),
  the resolver (`profile.py`), the interactive creator
  (`strategy_select.py`), and all `run.py` reads. It was the sole
  provider-DTO mirror: values flowed straight into the Tuzzina
  post `settings` payload.
- Loader is fail-closed: a YAML containing `provider_overrides`
  raises instead of being silently accepted or stored.
- `run.py` now builds intents with `post_kind="post"` and
  `settings={}`; identity (`__type__`) and kind are forced at
  injection from adapter translation (`injection/service.py`),
  and live provider truth stays available via
  `TuzzinaClient.get_integration_settings` for the upcoming
  adapter slimming (no decorative fetch added here).
- Retained policy fields unchanged: brand, language, hashtags,
  links_policy, mentions, media intent, sources, content,
  generation, planning, extras. G1, G3, image delegation,
  scheduling, adapters (one docstring word), and Tuzzina untouched.
- Tests: rejection + policy-only + no-override-key proofs;
  integration/selection behavior preserved. 171/171 pass
  (was 170). No Tuzzina/Postiz changes. No live calls. No UI.

## 0.10.0 — Migration Step 2: image generation delegation (no local image engine)

- Removed `OpenAIImageGenerator` (`src/g2/generators.py`): Brain no
  longer contains any local image-execution path. It was dead code
  in `src` (never instantiated; `--openai` wired `None`), but its
  existence was a parallel engine one import away.
- New `TuzzinaClient.generate_image(prompt)` (`src/tuzzina/client.py`):
  stdlib-only JSON-RPC 2.0 to the already-exposed MCP endpoint
  (`POST <backend-root>/mcp`, same API key, no new credentials, no
  new dependency) calling the existing `generateImageTool` with
  Brain's prompt decision. Returns Tuzzina's stored `{id, path}`.
  No retries (generation is not idempotent); errors raise
  `TuzzinaError` with no local fallback. No Public API image
  endpoint exists (only video), and the internal `/generate-image`
  route is session-authed, so MCP is the only existing headless path.
- New `TuzzinaImageGenerator` marker (no creds, no bytes;
  `generate_png` raises fail-closed) + `run._resolve_media`
  dispatch: delegated kind -> `generate_image` directly (zero
  Brain-side bytes, zero `upload_bytes`); `--mock`/Mock path
  byte-identical (`generate_png` + `upload_bytes`); `--openai` now
  wires the delegated marker instead of `None` (production image
  decisions delegate; note: each one spends Tuzzina `ai_images`
  credit server-side). Dry-run unchanged (zero writes).
- InjectionService, adapters, Strategy, G1, G3 untouched.
- 12 new/updated tests (MCP endpoint+prompt+session, media-ref
  passthrough, no-upload in delegated path, tool-error propagation
  with no fallback, removal proof, Mock intact, no secret logging).
- 170/170 pass (was 158). No Tuzzina/Postiz changes. No live calls.

## 0.9.0 — Migration Step 1: live capability/settings fetch (fetch-only primitive)

- New `TuzzinaClient.get_integration_settings(integration_id)`
  (`src/tuzzina/client.py`): read-only `GET
  /public/v1/integration-settings/:id`, keyed by `integration_id`
  only (no name/platform matching). Returns Tuzzina's
  `{output: {rules, maxLength, settings, tools}}` unchanged: no
  provider-schema validation, no DTO copy, no capability registry.
- Explicitly NOT consumed yet: adapters, injection, Strategy, G2,
  G3 untouched (STEP 1 is fetch-only foundation for later steps).
- 6 new tests in `tests/test_tuzzina_client.py` (exact path + id,
  single-GET read-only, unchanged passthrough, HTTP-error
  propagation, empty-id guard without call, no secret logging).
- 158/158 pass. No Tuzzina/Postiz changes. No production calls.

## 0.8.2 — Deployment readiness gaps (Phase 5.1, no behavior change)

- `.gitignore`: added `brain-strategies/` so runtime strategy files
  (per-integration policy created by `strategy_select.py` on the
  deployment host) survive `git pull` / `git clean`. They were
  untracked and deletable before.
- New `requirements.txt` (`pyyaml>=6.0`, floor pin only): the sole
  non-stdlib runtime dependency, previously installed manually and
  unpinned. Install: `pip install -r requirements.txt`.
- Verified (no change needed): `--dry-run` present in HEAD with
  zero-write semantics; `campaigns/*.yaml` tracked in git;
  no deploy scripts/systemd/Docker in repo (manual invocation
  model documented in README/AGENTS).
- 152/152 pass. No Tuzzina/Postiz/Meta/R2 changes. No behavior
  changes. No new runtime code paths.

## 0.8.1 — Channel identity source-of-truth hardening (Phase 4 - stabilization)

- src/channels/profile.py: removed ChannelProfile legacy class and resolve() legacy path; resolve_strategy() is now the single resolver (channel wins via sentinel, extra_policies removed as dead data).
- Deleted src/channels/loader.py and channels/juzzir.facebook.yaml legacy channel definition (duplicated Tuzzina identity via name/platform/VALID_PLATFORMS).
- Renamed src/select.py -> src/strategy_select.py to avoid shadowing Python stdlib select on Linux (caused circular ImportError breaking 3 tests on production).
- src/run.py: removed legacy two-arg <campaign> <strategy> mode; canonical --strategy-by-integration is now the sole path (validates via get_integration, platform from Tuzzina).
- tests/test_channel_profile.py: removed (12 legacy tests for deleted code).
- tests/test_channel_integration.py and tests/test_run.py updated to new ChannelStrategy/resolve_strategy flow; 152/152 pass (was 153 with legacy file).
- No Tuzzina/Postiz/Meta/R2 changes. No new dependencies. No scheduler/publisher/DB/R2 code.

## 0.8.0 — Injection causal tracing (Phase 3.1)

- New `src/injection/trace.py` (stdlib only: json, sys, uuid):
  `NullTracer` (default, silent), `MemoryTracer` (tests),
  `StderrTracer` (runtime; JSON lines on stderr so run.py stdout
  stays parseable). `emit()` enforces an allowlist and raises on
  any other field (fail-closed against secret leaks).
- `InjectionService` accepts an optional tracer (default silent;
  return values and execution order unchanged) and emits
  `injection.start`, `injection.adapter_selected`,
  `injection.post_request`, `injection.post_response` with one
  uuid4 `injection_id` per `inject()` call. Failures emit
  `injection.error` with error type + stage only, then re-raise
  unchanged. Allowlisted fields only: no tokens, keys, cookies,
  headers, bodies, or full user content (content length as int).
- `run.py` passes `StderrTracer()` (one-line wiring change).
- 14 new tests in `tests/test_trace.py`: correlation id
  uniqueness + stability, all four success events in order,
  failure event + unchanged re-raise, silent default, secret
  rejection, stderr JSON shape, return-value identity.
- 167/167 pass. No Tuzzina/Postiz/Meta/R2 changes. No new
  dependencies. No live call performed by this change.

## 0.7.1 — Preserve source_ref through planning (attach fix)

- `PlannedPost` carries `source_ref` from the package; G3 planner
  passes it through. `run.py` reads `p.source_ref` for the attach
  link, so `links_policy == "attach"` runs no longer crash with
  AttributeError.
- 1 new test in `tests/test_run.py`
  (`test_attach_policy_reaches_intent_without_crash`). 153/153 pass.
- No Tuzzina/Postiz/Meta/R2 changes. No new dependencies.

## 0.7.0 — Phase 2: InjectionService is the live path

- `run.py` no longer builds Tuzzina post payloads by hand. After
  G1/G2/G3 + media upload (unchanged), each planned post becomes a
  `CanonicalIntent` consumed by `InjectionService.inject`, which
  resolves the integration live, selects the adapter from Tuzzina's
  identifier, translates, and posts via `TuzzinaClient.create_post`.
- New `--post-mode {draft,schedule,now}` flag (default: draft,
  preserving previous behavior). Previously run.py always created
  drafts; modes now flow through the Injection Layer.
- Removed dead `_run()` helper (never called). Removed the inline
  `adapter.shape_text` call (service assembles once; no duplicate
  hashtags by construction).
- `main()` maps `UnsupportedPlatform` to exit code 4 (same as the
  previous inline handler).
- One intentional semantic delta: each planned post is injected
  with its own `planned_at` instead of bundling all posts under
  the first slot's date. G3 already computed per-post slots; the
  old code discarded all but the first.
- 5 new tests in `tests/test_run.py` (schedule/now modes, no
  manual payload construction, stale integration, unsupported
  platform). 152/152 pass.
- No Tuzzina/Postiz/Meta/R2 changes. No new dependencies.

## 0.6.0 — Injection Layer V1 (intent → adapter → Tuzzina payload)

- New `src/injection/` package: `CanonicalIntent` (brain decision,
  not a Tuzzina DTO copy), `InjectionService` (intent → live
  integration lookup → adapter → payload → `TuzzinaClient`),
  `capabilities.status()` lens (supported/unsupported/unknown),
  `UnsupportedCapability` + `InvalidInjectionIntent` errors.
- Service order is explicit: validate intent → resolve integration
  LIVE by id (stale → TuzzinaError propagates) → platform FROM
  Tuzzina's response → adapter → capability gates (refuse, never
  invent) → translate → single `TuzzinaClient.create_post` call.
- Hashtags/mentions travel separately and assemble once in the
  adapter (0.5.1 ownership preserved; tested end-to-end).
- Links: attach goes to `settings["url"]` only on positive support
  signal, else omitted (Instagram). Mentions inline as `@handle`.
- `settings` = adapter defaults + intent overrides, with `__type__`
  and `post_type` forced from Tuzzina's identifier + intent kind.
- `TuzzinaClient.create_post(posts, date, post_type)` added; the
  same POST /posts endpoint `create_draft` always used.
  `create_draft` now delegates (behavior identical).
- 28 new tests in `tests/test_injection.py`: selection by id,
  adapter mapping FB/IG, unsupported platform/capability, links
  FB/IG, hashtag ownership + no-duplication, mentions, scheduled/
  immediate/story intents, media refs, settings merge + identity
  forcing, stale protection, capability helper, plus guard tests
  (no Tuzzina/Postiz imports, no DB/R2, no scheduler/publisher,
  no name-based selection, adapters do no HTTP).
- No Tuzzina/Postiz/Meta/R2 changes. `run.py` live path untouched.

## 0.5.1 — Single hashtag ownership (duplicate fix)

- `g2.pipeline.build_package` no longer merges hashtags into
  `pkg.content`. `pkg.content` is plain generated text (+links);
  `pkg.hashtags` carries tags separately. Final assembly belongs
  to `PlatformAdapter.shape_text`, so each tag appears exactly
  once per platform rules.
- No new hashtag logic, no rule changes. Order note: body is now
  text → links → hashtags (was text → hashtags → links).
- 6 new tests in `tests/test_hashtags_once.py`: exactly-once for
  Facebook and Instagram, clean content without tags, empty tags
  add nothing, preferred/max policy intact, package-content
  contract (no `#` in `pkg.content`).

## 0.5.0 — Shared Core + Facebook/Instagram adapters

- New `src/adapters/` package: `PlatformAdapter` interface
  (`capabilities`, `shape_text`, `shape_media`, `apply_link`,
  `default_settings`) + `get_adapter(identifier)` selection from
  the Tuzzina integration identifier. Unknown identifiers raise
  `UnsupportedPlatform` (no whitelist; we simply have no adapter).
- `FacebookAdapter` (proven v2.23.0 values, each cited file:line):
  max_length 63206, post/story, text+photos+mp4-reel, inline
  hashtags/mentions, attach/cta/hide links, URL media delivery.
- `InstagramAdapter` (proven v2.23.0 values, each cited file:line):
  max_length 2200, post_type required, media required ≥1, carousel
  2-10, story single picture, caption single-media only, inline
  hashtags/mentions, links unsupported (attach ignored, cta text
  only), URL media delivery.
- Strategy gains per-channel `sources` (validated type/url/n);
  precedence: channel strategy sources win, empty/missing falls
  back to campaign default sources (tested both directions).
- Strategy gains `mentions` (style, default inline — proven for
  FB+IG) and `media` (`min_items`/`max_items`) policies.
- G3 `plan()` accepts `daily/weekly/monthly_count` caps,
  `start_time`/`end_time` daily window, `spacing_minutes` gap.
  Pure slot calculation; no runtime/state/queue/cron.
- G2 `build_package` accepts `limits={max_length}` and
  `link_fn` from the adapter (backward compatible defaults).
- `run.py` selects the adapter from Tuzzina's `identifier`,
  enforces media policy (truncate to max, skip below min with
  notice), shapes text via the adapter. Legacy two-arg mode kept.
- `campaign.sources` now optional (fallback only).
- 30 new tests (83 → 113): adapter caps, text shaping, media
  rules, link policies, selection incl. unsupported, sources
  precedence (3 ways), counts/spacing/window/timezone,
  no-Tuzzina-imports, no-R2-direct, no-scheduler-constructs.
- AGENTS.md documents the Core/Adapter split.

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
