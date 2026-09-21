# Changelog

## Unreleased — Generic LLM task retry (shared execution layer)

- New `llm.adapters.complete_with_retry` + `classify_llm_error`:
  an LLM task succeeds ONLY on transport success PLUS valid
  task output. HTTP 200 with reasoning-only / empty / malformed
  output, 429 / 5xx / timeouts / connection errors retry the
  SAME task (same adapter/session/args) with exponential
  backoff + jitter (default 3 attempts); auth / unknown model /
  bad config / provider refusals fail closed immediately.
- All four LLM roles execute through it (research, analysis,
  text, image prompt); validators feed `invalid-output` into
  the same classifier, so malformed/schema-invalid replies no
  longer terminate a run on first attempt. Domain error
  contracts (`ResearchError`, `AnalysisError`) unchanged.
- Tests: 28 new in `tests/test_llm_retry.py` (classification,
  retry/rescue, backoff caps, identity stability, exhaustion,
  no-duplicate-side-effects).

## Unreleased — Brain Image Agent (prompt LLM)

- New Image Agent role: the LLM that turns the campaign Image
  Skill + topic/brand context into the single image prompt.
  Tuzzina still owns bytes/storage/publishing unchanged.
- `g2.generators.OpenAIImagePromptGenerator` (real transport,
  same per-role adapter/session/base contract as the Text
  Agent) + `MockImagePromptGenerator` (deterministic, tests).
- `g2.pipeline` uses the agent prompt when configured; when no
  Image Agent exists the previous deterministic assembly is
  kept. A configured-but-failing agent propagates — no silent
  downgrade.
- `run_cycle._build_image_prompt_gen`: optional role. Absent
  `BRAIN_IMAGE_PROMPT_*` = feature off (None); partial config
  fails closed; `--mock` uses the mock.
- Tuzzina side: `AI_ROLES`/`LLM_ROLES` gain `image_prompt`;
  `brain.activity` forwards it as an optional role (missing
  assignment never fails a run); settings UI lists it.
- Tests: 8 new in `tests/test_image_agent.py` (prompt used,
  deterministic fallback, failure propagates, env resolution).

## Unreleased — Stage L 24h readiness prep (no deploy, no publish)

- Dry-run only: 24-slot bounded window (`window_publish_ats`
  hourly UTC + `materialize_slots`) proven deterministic
  with 24 unique in-window slot IDs; full mocked campaign
  simulation (19 success, 2 no-op, 1 claim-lost, 1
  generation-failed, 1 publication-failed, 1 local
  companion) plus worker-B restart (19 already-complete,
  non-terminal retried once, zero duplicate creates).
- Read-only findings: live candidate pool is empty (no
  persisted `candidate-pool` row), so early production
  slots would NO_OP until discovery fills it; 3
  `brainpub:%` rows exist (1 live external leftover from
  the prior session + 2 DRAFT debug rows, both
  `releaseId: None`). Nothing published, deleted, or
  deployed in Stage L.

## Unreleased — Stage K-5 campaign proof (no deploy)

- `src/slots.py`: `run_campaign`/`run_campaign_slots` accept
  `mode` and forward it to `run_slot_content` (was: accepted
  on `run_campaign_slots` but dropped, so every campaign post
  silently built as `draft` and could never publish
  externally). Default stays `draft`. No other behavior
  change.
- Tests: 5 new in `tests/test_campaign_slots.py` (mode
  propagation `now`/`draft` through both entry points down
  to `create_post post_type`; campaign companion main+child
  value items with deterministic `:main`/`:comment` ids and
  no-duplicate replay). Suite 941/941.
- Live K-5 pilot (single authorized external post, since
  cleaned up): fresh `k5-pilot-20260918b` campaign via
  `run_campaign(mode="now")` published Facebook post
  `1326634327193967_122106060909474468` (state PUBLISHED),
  replay returned `already-complete` with zero new posts,
  restart skipped the published slot. Mocked generation
  emits no companion marker, so the live post had no
  companion; the companion path is proven locally by the
  new tests instead.

## Unreleased — Stage J idempotent publication handoff (no deploy)

- New `src/slot_publish.py`: SlotContent in, idempotent
  Tuzzina execution out. Validates every intent (identity,
  destination, ISO publish time, media refs, duplicate
  detection), resolves destination via existing
  get_integration, re-claims the candidate, claims the
  Tuzzina-owned BrainPublication ledger row, publishes
  through the EXISTING InjectionService with deterministic
  value ids (replay upserts same rows), verifies companion
  via the open preview endpoint, completes the ledger
  first-writer-wins, releases the claim. Retries observe
  recorded rows (idempotent-complete) instead of
  re-executing. Terminal statuses: no-op,
  validation-failed, destination-unavailable,
  idempotency-already-complete, publication-failed,
  companion-failed, success. No Research/Analysis/
  generation calls, no uploads, no credentials, no
  scheduler.
- `CanonicalIntent.value_ids` (optional, default None):
  deterministic row ids attached by InjectionService only
  when present with exact length match; legacy path
  byte-identical.
- `TuzzinaClient`: claim/get/complete publication,
  delete_post (cleanup), get_post_preview (read-only
  companion verification).
- Tuzzina (narrow, additive only): `BrainPublication`
  model (unique org+slot+intent, expiry-swept),
  repository/service/controller/DTO/spec following the
  candidate-claim pattern (409 with row, same-owner
  reclaim, first-writer-wins complete).
- Tests: 25 new (`tests/test_slot_publish.py`); full Brain
  suite green; Tuzzina publication spec 10/10, claim spec
  7/7; backend tsc shows only pre-existing errors.

## Unreleased — Stage I slot content (no deploy)

- New `src/slot_content.py`: SlotExecution in, generation
  handoff out. Mirrors research/cycle.py stage-for-stage
  for ONE claimed candidate (roles gating incl.
  research-off-forces-analysis-off, research, analysis +
  ineligible stop, format gate, text gating,
  plan_generation, video deferral, G2 packaging, G3
  planning, intent building) reusing the SAME helpers with
  identical semantics. No collect/claim/publish/schedule/
  upload/scheduler/credentials. Claim released best-effort
  on terminal outcomes (mirrors run_cycle.py); expiry
  covers the rest. Companion flows via build_intent split.
- One-line closed-pipeline correction in
  `g2/pipeline.py:apply_banned`: preserve newlines (was:
  flatten all whitespace, which destroyed the companion
  marker line before build_intent could split it).
  Banned-word removal byte-identical otherwise.
- Tests: 24 new (`tests/test_slot_content.py`); suite
  887/887. Live-verified read-only (mock models, socket
  connect patched to raise): ready + 1 intent + companion
  split + released.

## Unreleased — Stage H slot executor (no deploy)

- New `src/slots.py`: one slot in, one independent
  execution context out (`SlotExecution`: slot/campaign/
  window ids, candidate id + verbatim provenance, claim
  owner, terminal status). Select from existing pool,
  atomic claim via injected callable, fail-closed
  no-op/expired/invalid/disabled/claim-lost/claim-failed.
  Idempotency from same-owner reclaim (repeat rebuilds,
  new owner loses). No LLM, no publish, no network, no
  clock, no store. Tests: 27 new
  (`tests/test_slot_execution.py`); suite 863/863.
  Live-verified read-only against real RSS data.

## Unreleased — Stage G candidate pool (no deploy)

- New `src/discovery/pool.py` (pure, no IO/LLM/clock):
  source-neutral retention over plain dicts. `upsert`
  merges by stable identity (first_seen preserved, fields
  refresh); `select` returns eligible entries (dated
  newest-first, dateless kept, deterministic order, limit,
  exclude); `prune` drops stale and caps by oldest;
  `to_envelope` serializes the `{"candidates": {...}}`
  envelope persisted under `candidate-pool` via the
  existing research-state endpoint. No claiming, no
  scheduling, no Research calls from the pool.
- Tuzzina: `candidates` allowlisted in
  `research-state.service.ts` with the same object-shape
  validation as `seen` (1MB cap unchanged). Nothing else
  touched server-side.
- Tests: 27 new (`tests/test_candidate_pool.py`); suite
  836/836. Tuzzina research-state spec 11/11.
- Live-verified read-only against a real RSS feed: 5
  candidates pooled, second poll added zero, selection
  returned dated-newest-first with provenance intact.

## Unreleased — Stage F social discovery (no deploy)

- `src/g1/search.py`: `social.facebook`, `social.instagram`,
  `social.x` source types through the SAME generic MCP
  path (capability name == source type; zero vendor code).
  Normalization preserves explicit platform `id` as
  `external_id` and explicit `author`, else domain fallback.
- `research/cycle.py`: social dispatch via `_collect_search`;
  shared `stable_identity()` now used for ALL search-type
  candidates so classify, dedup, and claim agree (identical
  for web/news, correct for platform IDs).
- `channels/strategy_store.py`: accepts the three social
  types (`query` required scope string, optional `target`
  label for evidence only, `max_age_days`, `poll_minutes`,
  `source_id`, `enabled`); legacy types unchanged.
- `discovery/manager.py`: 5m poll defaults for the three
  social types.
- Platforms: Facebook/Instagram/X all BLOCKED (no zero-cost
  official access: Meta review gates, X pay-per-use). No
  scraping fallback built. Tests: 32 new
  (`tests/test_social_discovery.py`); suite 809/809.

## Unreleased — Stage E zero-cost search (no deploy)

- New `src/g1/search.py` (Stage E): query-driven `web` /
  `news` discovery through the generic Stage D MCP layer
  only (semantic `web.search` / `news.search`, never a
  vendor/endpoint/credential; no keys, accounts, billing,
  or paid fallback anywhere in code). Results normalize
  into the existing SourceItem contract with shared
  stable_identity (cross-query/cross-capability/
  cross-engine duplicates collapse), optional
  `max_age_days` freshness filter (dateless kept, never
  dated by fiat), and Stage B provenance fields. Failures
  are per-source evidence; empty means zero candidates and
  no Research call. Content stays untrusted (existing
  editorial quoting at the model boundary).
- New `tools/mcp_search_bridge.py` (self-hosted deployment
  infra, stdlib only, not imported by Brain): exposes a
  SearXNG-compatible JSON API as generic MCP tools
  `search` / `news_search`. Fail-closed (SearXNG errors
  become JSON-RPC errors, never fabricated results).
- `research/cycle.py` dispatches   `web` / `news` sources
  (query required; shared NEW/UNCHANGED/UPDATED classify;
  claim integration unchanged); strategy schema accepts
  them (`query` required, optional `max_age_days`);
  discovery cadence defaults 5m for both.
- Live-verified against local self-hosted SearXNG
  (official source, no keys, loopback only): web query
  returned 8/8 kept, news query 8/8 kept, zero dupes.
- Tests: 30 new (`tests/test_search_discovery.py`); suite
  777/777. No commit/push/build/deploy; production
  untouched.

## Unreleased — Stage B discovery foundation (no deploy)

- New shared fetch boundary `src/g1/fetch.py` (extracted
  verbatim from the proven `g1/rss.py` guards + per-hop
  redirect re-validation): scheme/host allowlist, full DNS
  global-routability check (v4+v6, mixed answers refuse),
  capped redirects with re-check, size/timeout caps, machine
  reason errors. `rss.py` delegates to it (behavior
  identical); `website.py` routes through it (closes the
  proven SSRF gap: private/metadata/rebinding targets now
  refuse before connect).
- New generic MCP layer `src/mcp/` (stdlib only, zero new
  deps): `client.py` (initialize/tools-list/tools-call,
  session handling, timeouts, size caps, no retries, no
  credential storage) extracted from the proven Tuzzina
  image-delegation handshake (that caller is byte-identical;
  its private `_rpc` removed); `registry.py` (file-owned
  `servers.yaml`, strict validation, semantic capability ->
  server+tool, env-name auth refs only, ambiguous refuses);
  `servers.yaml` ships empty (zero capabilities by default).
  No vendor/company/agent/campaign concepts anywhere.
- Stage C discovery core: `src/discovery/manager.py`
  (pure due-source policy: explicit `poll_minutes` wins,
  per-type defaults rss/youtube 5m + website 10m, unknown
  types poll conservatively, disabled never due, inputs
  untouched, no clock/network/state); strategy schema
  carries optional `poll_minutes` (int >= 1, rejected
  otherwise). Polling cadence, publication schedule, and
  research lead time share no field. 15 tests.
- Candidate contract: `SourceItem` gains optional
  `external_id/discovered_at/capability/author` (defaults
  keep every existing caller byte-identical);
  `monitor.stable_identity()` (platform:id > canonical URL >
  content hash; title never identity); `SourceReport` gains
  optional capability/tool/discovered_at evidence;
  `BRAIN_RESULT` gains a read-only `discovery` projection.
- Slot contract: `g3.planner.Slot` + `research_window()`
  (publish_at/research_start_at/deadline_at; polling
  cadence, schedule, and lead time stay separate fields).
  No scheduler changes (Stage 9 insertion point only).
- Trust boundary: `editorial.quote_untrusted()` delimiters;
  research/analysis prompts wrap collected text (meaning
  untouched); strict JSON validators remain the output gate.
- Reservation: CLOSED - atomic `BrainCandidateClaim`
  table in Tuzzina (`@@unique([organizationId,
  candidateId])`, additive migration only) with a narrow
  claim/release interface (DTO -> Controller -> Service ->
  Repository; 409 on loss, owner-only release + reclaim,
  expiry swept on the claim path, no cron). Brain claims
  NEW items by stable identity before any LLM spend
  (losers drop, all-lost means zero LLM calls, transport
  failure fails closed with nothing committed) and releases
  on every terminal outcome. Proof: concurrent-claim
  exactly-one-winner, reclaim, release, expiry-path, and
  claim-before-research tests. Suite 732/732. No paid
  dependency introduced
  (stdlib + PyYAML only). No Tuzzina/workflow/provider/
  OAuth/campaign changes. No commit/push/build/deploy.
- Tests: 84 new (fetch boundary 19, MCP client 17,
  registry 21, candidate 12, slot 8, trust 5, reservation
  3+doc); suite 721/721. No paid dependency introduced
  (stdlib + PyYAML only). No Tuzzina/workflow/provider/
  OAuth/campaign changes. No commit/push/build/deploy.

## Unreleased — Brain Skills system

- New `src/skills/` file-owned expertise layer (no DB, per
  AGENTS.md): `loader.py` (strict validation, fail-closed,
  `BRAIN_SKILLS_DIR` override) + versioned YAML skills
  (`research-v1`, `text-social-v1`, `image-v1`, `video-v1`).
  Stages resolve skills by type; analysis shares the research
  skill; runs record `skills_used` (stage/type/id/version) in
  `BRAIN_RESULT`.
- New deterministic editorial (`skills/editorial.py`):
  markdown flattening, duplicate-URL collapse, policy-owned
  link/CTA removal, hashtag stripping, duplicate-line
  collapse, and single-application link assembly
  (`append_once`, applied in pipeline + both adapters).
- Text skill v2: language-consistency rule (no third-script
  tokens outside narrow exceptions) plus a deterministic
  Arabic script gate (`script_consistent`/`assert_script`,
  enforced in the pipeline; Chinese-in-Arabic output now
  fails loud instead of publishing).
- Known limitation (no safe deterministic fix): truncated
  mid-sentence output cannot be reliably distinguished from
  valid short posts, so it is not auto-rejected; the skill
  instructs complete sentences instead.
- Enabling runtime fixes in the same window: strategy `n`
  cap honored for rss/youtube collection; markdown-fenced
  JSON accepted in role validators; research-state transport
  via slash-safe query/body routes; generic
  `openai_compatible` dispatch with run-scoped base URL;
  run-scoped `x-opencode-session` for Zen; redacted 4xx
  excerpts and safe empty-response sketches in diagnostics.
- Campaign-specific Skills: campaigns may carry their own
  `skills` instructions (`research`, `analysis`, `text`,
  `image`, `video`) in the campaign document; each stage
  resolves its own type with the global file as fallback.
  New global `analysis.yaml` default (analysis no longer
  shares the research skill). Runs record per-stage
  `source: campaign|file` in `skills_used`.
- UNIFIED Skill system (replaces the campaign-embedded path
  above): skills are reusable named files
  (`<type>.<name>.yaml`); campaigns and channels assign them
  BY NAME (`campaign.skills`, `strategy.skills`, campaign wins
  per stage); one `get_skill(type, name)` resolver, global
  files as fallback, `skills_used` traces the actual selected
  skill (`source: assigned|default`). Embedded instruction
  strings are rejected (fail closed). No production campaign
  carried embedded skills, so nothing was migrated or lost.
- Campaign role participation (`campaign.roles`, canonical
  subset, default all five): only selected stages execute.
  Research-off forces analysis-off (architectural dependency);
  text runs per-item without analysis; unselected media stages
  never execute and never invent intent; skips recorded in
  `skipped`, active roles in `roles` on every return path
  (including empty collection), trace covers active
  stages only.
- Tests: 621/621 pass (loader, named assignment, isolation,
  malformed rejection, merge precedence, strategy round-trip,
  editorial, wiring, traceability, secrecy, schedule
  inheritance: empty channel leaves fall back to campaign,
  numeric zero stays configured, role participation gating:
  per-stage execution with recorded skip reasons, media roles
  gate intent downward only, text runs per-item without
  analysis).
- Companion (first) comment support (Juzzir Facebook): a text
  skill may return the main post, a `---COMPANION---` line,
  then the companion comment (`COMPANION_MARKER` in
  `src/skills/loader.py`, split in `build_intent`, optional
  `companion` field on `CanonicalIntent`). Injection sends it
  as the second value item with no hashtags/mentions/media;
  Tuzzina publishes it natively on commentable providers and
  posts the parent alone elsewhere — no Tuzzina, workflow, or
  schema change. G2 emits the two-part instruction only for
  skills declaring `companion: true` in config (all other
  skills keep the exact historical line). Trace gains
  `has_companion`/`companion_chars` (allowlisted, metadata
  only). 9 new tests; suite 630/630.
- Product-relevance hardening (Stream Deck case, no src
  change): strategy data no longer appends a brand URL CTA
  (empty `cta_style`, `links_policy` stays `cta` so relevant
  model-written URLs survive first-occurrence),
  `preferred_words` emptied and hashtag max 3 (no forced
  brand/topic tags); research/analysis/text skills carry
  explicit product-fit gates (audience vs product relevance,
  default-deny, banned forced phrases) with the machine JSON
  contracts unchanged. Guards: runtime-invents-nothing,
  CTA-assembly mechanics, hashtag cap/preferred. Suite
  637/637.
- Data (production DB, no code): new disabled campaign
  "Juzzir Mastering News - 24H" (hourly slots, freq 60)
  with three new mastering skills (research/analysis/writer,
  writer config max_chars 1300 + companion contract) and the
  10-point verified Juzzir product truth embedded in the
  analysis gate. Research skill hardened with freshness and
  source discipline (published_at recency, source-quality
  tiers, parametric-memory ban, subject-dedup merge); Tier-2
  live web research deliberately NOT claimed - adapters have
  no browsing tool, so on-demand web discovery needs a new
  search capability (code, deferred with reasons).
  Pending: feed expansion approval (single
  shared feed cannot supply 24 fresh topics), safe-run
  validation, explicit activation.

## 0.30.0 — Phase 15: OpenCode Zen provider adapter

- New `OpenCodeZenAdapter` (`opencode_zen`): Zen's
  OpenAI-compatible chat completions surface
  (`https://opencode.ai/zen/v1/chat/completions`, Bearer
  auth). Subclasses OpenAIAdapter (same protocol, no code
  duplication); the assignment model id passes through
  unchanged and no default model is invented (empty model
  fails closed as a provider error).
- Closed dispatch extended; role resolution, prompts,
  Strategy/Skill, and error conventions untouched.
- 12 tests (protocol shape/passthrough, no-default-model,
  errors/timeout, role independence, strategy-instruction
  parity, secrecy, dispatch set). 493/493 pass (was 481).

## 0.29.0 — Phase 14A: scope visual rules to image/video prompts

- `build()` no longer renders `visual_rules`/`video_rules`:
  text-side prompts (research, analysis, text) never carry
  visual data. `build_visual(policy, video_rules=True)` keeps
  colors/logo/visual rules for image+video, adding video rules
  for video roles only.
- `_prompt_for` gains `for_video` (image vs video prompts);
  pipeline delegated image prompts use the visual block with
  `video_rules=False`. Empty-policy behavior unchanged.
- 6 routing tests (research/analysis/text exclusion,
  image+video reception, split builder). 481/481 pass.

## 0.28.0 — Phase 14: pillar gating fix + rss/youtube/enabled sources

- Analysis `_policy_view` now falls back to resolved
  `content.content_pillars`, so the pillar gate actually fires
  on run_cycle policy dicts (previously inert: always
  pillars=[]). No contract change; mocks deterministic.
- Strategy sources accept `rss` (url) and `youtube`
  (strict `UC…` channel_id) alongside `website`, plus
  optional `source_id` identity override and `enabled`
  flag. Old website-only entries load byte-identical.
  `run_cycle` skips `enabled: false` sources; legacy
  `run.py` fails closed (exit 2) on non-website with a
  pointer to run_cycle.py.
- 7 tests (pillar match/mismatch/empty, sources
  roundtrip/compat/fail-closed/skip/legacy-guard).
  475/475 pass (was 468).

## 0.27.0 — Phase 12: language/colors/logo as Strategy fields

- Schema: `Brand.colors` + `Brand.logo_url` (visual identity
  hints, plain strategy data) and `LanguagePolicy`
  (`default`, `output_override`) as a per-integration
  Strategy-level override. No provider/auth fields.
- Store: YAML roundtrip for all three (`strategy.language`
  mapping, `strategy.brand.colors|logo_url`) with strict
  type validation; old YAMLs without them load unchanged
  (safe `_MISSING` defaults, fail-closed on wrong types).
- Merge: channel wins per leaf; campaign `colors`/`logo_url`
  are now fallback instead of silently discarded; strategy
  language overrides campaign language per leaf.
- Skill routing: new pure `build_visual()` (colors, logo,
  visual/video rules) appended to image/video prompts only;
  text-side `build()` unchanged (language already there,
  no colors/logo leak into research/analysis/text).
- CLI `select.py` passes the new fields through.
- 11 tests (old-compat, roundtrip, fail-closed, fallback,
  override, routing, image/video reception, secrecy).
  468/468 pass (was 457).

## 0.26.0 — Phase 11: provider-agnostic content generation skill

- New `src/channels/instructions.py`: pure builder rendering the
  merged Channel Strategy profile into a "Channel instructions"
  block (voice, pillars, CTA, image/visual/video rules, creation
  policy, language). Empty policy -> "" (legacy prompts
  byte-identical); unknown/secret-shaped keys never rendered.
- Strategy extension: `brand.voice` + `strategy.visual`
  (`visual_rules`, `video_rules`) with merge (profile.py),
  YAML roundtrip (strategy_store.py), and select-flow
  passthrough (strategy_select.py). No secrets, no scheduling,
  no provider catalogs — plain policy data only.
- Skill threaded into all five prompt sites: research
  (`OpenAIResearchModel.research(items, policy=None)`),
  analysis (`OpenAIAnalysisModel` context), text
  (`TextGenerator.generate(..., policy=None)` + pipeline
  `build_package(..., policy=None)`), image + video
  (`plan_generation(..., policy=None)` prompts and the
  pipeline delegated image prompt). Mocks accept-and-ignore
  (deterministic outputs unchanged); run.py/cycle.py pass the
  resolved strategy profile through.
- 15 tests (build/roundtrip/merge/live-prompt presence/legacy
  parity/mock parity/plan/pipeline/chain/fail-closed/secrecy).
  457/457 pass (was 442). No scheduler/cron/DB/publishing
  changes; Tuzzina untouched (execution owner).

## 0.25.0 — Phase 9: runtime role resolution + usage enforcement

- `run_cycle.py`: `--openai` resolves Research/Analysis/Text
  independently from `BRAIN_<ROLE>_{ADAPTER,KEY,MODEL}` (shared
  `OPENAI_API_KEY` fallback, closed dispatch, missing key or
  unknown adapter fails closed with exit 2); `--mock` ignores
  role env by design; resolution echoed as adapter+model only
  in `BRAIN_RESULT.roles_resolved` (never credentials).
- Usage enforcement: live `get_content_usage()` (new client
  method: GET `/public/v1/content-usage/:id?startDate=`)
  narrows distribution targets to remaining capacity per UTC
  day; failures warn and preserve target-only gating.
- 11 tests (role defaults/switch/missing/unknown/mock-
  isolation, envelope secrecy, exhausted/proceeding/failing
  usage). 442/442 pass (was 433). No scheduler/cron/DB/
  publishing changes; Strategy/G2/G3/Injection untouched.

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
