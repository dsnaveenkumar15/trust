# naveenlabs-planets — Design Spec

**Date:** 2026-09-30
**Status:** Approved in brainstorming, pending written-spec review
**Repo:** `algo\repo\naveenlabs-planets\` (internal name, never user-facing)

## 1. Overview

Three public AI-practitioner products on one shared backend, each on its own subdomain and shown as a planet in the naveenlabs.tech "AI galaxy":

| Planet | Host | One-line purpose |
|---|---|---|
| **Trust** | `trust.naveenlabs.tech` | Evidence-based security audits and trust grades for MCP servers / agent tools |
| **Radar** | `radar.naveenlabs.tech` | Daily "what changed in AI engineering, does it affect my stack, what do I do" — grounded in code-level API diffs |
| **Blueprints** | `blueprints.naveenlabs.tech` | Living, linted reference architectures for agentic systems, wired to Trust and Radar data |

### Audiences (all four apply)
1. Recruiters / hiring managers — credibility signal for Agentic AI Engineer / Staff-Principal roles.
2. Real public users — AI practitioners with a weekly reason to return.
3. The owner — tools used personally.
4. Future monetization — kept possible, not built now.

### Design principle
**LLMs explain; deterministic code decides.** Scores, grades, versions, costs, CVE severities, topology choices and diagrams are computed by code. LLM output is structured, schema-validated, and must carry verifiable evidence. Anything unverifiable is dropped, not published.

### Success criteria (targets for v1)
- Zero published findings/claims without verified evidence (enforced by validators, not by review).
- Trust: ≥ 50 MCP servers audited in the first month; critical-rule precision ≥ 0.9 and recall ≥ 0.8 on the golden set.
- Radar: digest published (full or clearly labeled partial) on ≥ 90% of nights; breaking-change recall ≥ 0.8 on the labeled API-break set.
- Blueprints: ~20 blueprints approved, all passing the linter.
- Each host ≥ 99% uptime as measured by Uptime Kuma.
- Trading apps on the same VPS never degraded by this stack (enforced by resource caps).

## 2. Constraints

- **LLM usage model — Hybrid:**
  - **Claude Pro subscription** is used only for the owner's own background batch jobs via headless `claude -p` running under the owner's login. It never serves public live traffic (consumer subscriptions are licensed for personal use; serving users from one risks an account ban).
  - **Live traffic** uses self-hosted Ollama (`qwen2.5:1.5b` for generation, `nomic-embed-text` for embeddings) from the existing naveenlabs-ai stack.
  - **BYOK** for heavy live features: the user's Anthropic key stays in their browser, which calls the Anthropic API directly. The key never reaches our server.
- **Claude Pro limits** are small and shared with the owner's daytime Claude Code use → batch runs at night (IST), adaptively rate-controlled.
- **Hosting:** single Hostinger VPS (4 vCPU / 16 GB) shared with real-money TradFi and Web3 apps behind Traefik. VPS currently down; everything must be buildable and testable locally first.
- **Naming:** no real platform/broker/brand names anywhere. One name per product used identically for subdomain, landing-page planet label, and in-app wordmark/`<title>`.
- **Commits:** author is the owner only; no co-author or tool-attribution trailers.
- **Local machine has no Docker.** Local dev runs in a venv against the laptop's Ollama and Claude Code.

## 3. Architecture

```
                 Traefik (Host(trust|radar|blueprints).naveenlabs.tech)
                                   │
                              ┌────▼────┐   read-mostly
                              │   web   │── SQLite (WAL) + published data volume
                              │ FastAPI │── Ollama (shared, internal network) ── live features
                              └─────────┘
                                   ▲ published JSON + manifests
                              ┌────┴────┐
                              │  batch  │── claude -p (owner's Pro login, no tools)
                              │ workers │── GitHub / PyPI / npm / OSV / GHSA / feeds
                              └─────────┘
Browser ── BYOK ──► Anthropic API (direct; key never touches server)
Browser ── BYOK output ──► web /api/lint-blueprint (JSON only)
```

- **`web`** (public): FastAPI, server-rendered Jinja2 pages (SEO for Trust server pages and Radar digests) plus small vanilla JS for live features. Routes by `Host` header to the planet's templates and data. `/admin` behind Traefik Basic Auth.
- **`batch`** (private, no Traefik labels): job engine, quota scheduler, planet pipelines.
- **Ollama:** single shared instance from naveenlabs-ai, reached over that compose network. Never exposed.
- **Storage:** one SQLite database in WAL mode + a published-data volume. Both added to naveenlabs-ops backup scripts.

### Repo layout
```
core/        llm gateway, job engine, scheduler, events, evidence lib, provenance, publish, parsers, github client
trust/       sources, rules, scoring, schemas, prompts/ (versioned)
radar/       sources.yaml, collectors, diff engine, signal score, schemas, prompts/
blueprints/  patterns, decision engine, linter, cost model, schemas, prompts/
web/         FastAPI app, host routing, templates per planet, shared design system, static JS
evals/       golden sets + harness (run in CI)
tests/       unit, contract (recorded LLM responses), integration (fake LLM), web smoke
docker-compose.yml
```

## 4. Core Platform

### 4.1 LLM gateway
- Single interface: `complete(task, schema) -> ValidatedResult | Failure`.
- Backends: `claude_cli` (Pro batch; invoked with JSON output, no tools, single turn — exact flags verified during implementation), `ollama` (live), `fake` (tests; replays recorded responses).
- Every call recorded in `llm_calls`: prompt version, input hash, output, latency, outcome.
- Response cache keyed by `(prompt_version, input_hash)` — identical reruns cost no quota.
- Untrusted content is always passed as delimited data, never as instructions.

### 4.2 Job engine
- Job types declare ordered stages; each stage's output is persisted in `stage_artifacts` so jobs resume at the failed stage without redoing earlier (possibly LLM-spending) stages.
- Idempotency key per job; retries with exponential backoff + jitter; dead-letter after 3 failures.
- Lease + heartbeat: a crashed worker's job is reclaimed after lease expiry.
- Per-planet lanes: failures in one planet cannot block another.

### 4.3 Quota scheduler
- One priority queue across planets. Priority = planet weight × job priority × staleness. Initial weights: Trust 50, Radar 35, Blueprints 15 (tunable).
- **Adaptive capacity (AIMD):** allowance per usage window increases additively after clean windows and halves on any rate-limit response. No hard-coded Pro limits.
- Runs only in the night window (IST); daytime quota reserved for the owner.
- Circuit breaker: repeated auth/limit errors → planets switch to degraded mode (Section 8) instead of retrying.
- Claude auth expiry detected and surfaced on `/admin`.

### 4.4 Events (cross-planet contracts)
- `events` table (outbox). Typed, versioned events:
  - `radar.mcp_server_discovered {repo, source, signal}`
  - `radar.breaking_change_detected {package, from_version, to_version, symbols[]}`
  - `trust.audit_published {server_id, sha, grade, audit_id}`
  - `trust.grade_changed {server_id, old_grade, new_grade}`
  - `blueprint.needs_refresh {blueprint_id, reason, cause_event_id}`
- Consumers keep cursors in `consumer_cursors`; delivery is at-least-once; handlers are idempotent.
- Planets never read each other's tables — only events and published JSON.

### 4.5 Evidence library (shared by all planets)
- **Quote verifier:** each LLM claim carries an exact quote + location; the quote must exist verbatim in the fetched source at that location, else the claim is dropped.
- **Citation checker:** cited item IDs must exist in the job's input set.
- **Symbol checker:** code symbols in generated snippets must exist in the relevant API snapshot.
- **Version checker:** versions in LLM output must be in the deterministically parsed version set.
- **Schema validation** (pydantic) on every LLM output.
- **Non-dismissable findings rule** (Trust) enforced here.

### 4.6 Provenance and publishing
- Every published artifact records: source URLs + content hashes, rule version, prompt version, model, validator results, approval ID (if human-approved).
- Publishing is atomic (write temp → rename) and produces `manifest.json` with sha256 of every file. `web` serves only complete manifests; a failed run never replaces the last good publish.
- Published Radar digests are immutable; corrections are appended as errata.

### 4.7 Data model (SQLite WAL)
`jobs`, `stage_artifacts`, `llm_calls`, `events`, `consumer_cursors`, `manifests`, `review_queue`, plus planet tables (e.g. `trust_servers`, `trust_audits`, `radar_items`, `radar_clusters`, `radar_api_snapshots`, `blueprints`). `batch` is the primary writer; `web` reads and writes only admin review actions.

### 4.8 Secrets
GitHub token and Basic Auth credentials via environment / Docker secrets. Claude login token on a volume mounted only into `batch`, mode 600.

## 5. Trust

### 5.1 Discovery
Official MCP registry, GitHub (topic `mcp-server`, sorted by stars), and `radar.mcp_server_discovered` events. Repos fetched as text via GitHub API (README, manifests, tool-defining source files; size-capped). Never cloned, never executed. Every audit pinned to a commit SHA. GitHub requests authenticated and ETag-cached.

### 5.2 Extraction
Tool definitions (name, description, input schema) and relevant code extracted with Python `ast` (Python) and `tree-sitter` (TS/JS). Other languages → listed as "unsupported — not scored". Extraction failure → "could not analyze". Never a guessed score.

### 5.3 Audit layers
1. **Deterministic rules** (run first): hidden instructions in tool descriptions (`ignore previous`, `<IMPORTANT>`, directions to read `~/.ssh`/`.env`), zero-width/invisible Unicode, base64 blobs, tool-name shadowing of common tools, shell exec (`subprocess`, `child_process`, `eval`), network calls to hard-coded URLs, filesystem access outside declared scope, hard-coded secrets, unpinned dependencies, maintenance and license signals, text addressed to auditors/reviewers.
2. **LLM review** (`claude -p`, no tools): input = rule findings + extracted tool definitions + relevant snippets. Output per finding restricted to enums: `confirm | dismiss | new`, severity, confidence, reason code, exact quote, `file:line`.

### 5.4 Robustness rules
- LLM never outputs a score or grade.
- Critical rule findings (hidden instructions, invisible Unicode, tool shadowing) are **non-dismissable**.
- Dismissing a high-severity finding requires an enum reason code and routes to review.
- New LLM findings need a verified exact quote (evidence library) or are dropped.
- **Self-consistency:** findings that would change the grade are reviewed twice; disagreement → review queue, not published.
- Any F grade or "malicious" label requires owner approval before publishing.

### 5.5 Scoring
Five categories: Injection surface, Capabilities/permissions, Exfiltration risk, Supply chain/maintenance, Transparency. Score 0–100 computed by a public formula over validated findings (severity × confidence weights per category). Grade A–F from score. Any critical finding caps grade at D.

### 5.6 Disclosure
- Apparently unintentional vulnerability → details embargoed 30 days, listing shows "under disclosure", maintainer notified.
- Clearly malicious behavior (poisoned descriptions, exfiltration) → warning published immediately (after owner approval).
- Every listing: dispute link (GitHub issue) and disclaimer "automated static review, not a guarantee".

### 5.7 Freshness and quota
- ~3–6 audits per night (adaptive). Priority by stars and Radar signal.
- Nightly cheap check (no LLM) of tool-definition hash; change → stale + re-queue. Audits older than 30 days marked stale.
- Rule/prompt version bump flags affected audits for re-scoring.

### 5.8 Live features
- `/check`: paste a `tools/list` manifest → JSON-schema validated, size-limited → same rule engine as batch → Ollama explains findings (rules decide). Ollama down → findings without explanation.
- BYOK deep audit: paste manifest + code → same audit prompt run from the browser.
- Deferred: live repo-URL fetch.

### 5.9 Pages
Directory (search, grade filter) · server detail (grade, category bars, findings with evidence links, SHA, audit ID + versions, age) · `/check` · methodology (rubric, formula, golden-set precision/recall).

## 6. Radar

### 6.1 Sources (structured feeds/APIs only; no HTML scraping)
GitHub release feeds of ~15 tracked frameworks (LangGraph, LangChain, LlamaIndex, CrewAI, AutoGen, MCP SDKs, vLLM, Ollama, model-provider SDKs), provider changelogs/RSS, arXiv API (`cs.AI`, `cs.CL`, `cs.MA`), MCP registry new entries, HN Algolia API (filtered), OSV and GitHub Security Advisory APIs. Configured in `sources.yaml` with tier `official | community`.

### 6.2 Pipeline (staged, resumable)
1. **Collect** (no LLM) from a per-source watermark — missed nights are back-filled automatically. Normalize to `{source, url, title, published, content_hash}`.
2. **Dedupe + cluster**: canonical URL + content hash + embedding similarity (`nomic-embed-text`).
3. **API-surface diff engine** (no LLM): for tracked packages, fetch the published artifact per version (PyPI wheel/sdist; npm package incl. `.d.ts`), parse without executing (`ast` / `tree-sitter`), produce API snapshots (public symbols, signatures, deprecation markers), diff consecutive versions → removed/renamed symbols, signature changes, new deprecations. Emits `radar.breaking_change_detected`.
4. **Security intel**: OSV/GHSA advisories for tracked packages; severity taken from source.
5. **Signal score** (deterministic): source tier, corroboration count, 7-day GitHub star velocity, existence of code behind the claim. Formula public.
6. **Pre-filter**: relevance rules + tier + embedding similarity to topic anchors → top-N clusters per night.
7. **Synthesize** (`claude -p`, ~10 clusters per call): per cluster `what_changed`, `affects` (enum stack tags), `impact` (`breaking|notable|minor`), `migration_effort`, `action`, `citations[]`, migration before/after snippets, entity IDs.
8. **Validate + publish** atomically.

### 6.3 Robustness rules
- Every claim cites input item IDs (citation checker).
- `breaking` requires either a detected API-diff break or a verbatim quote from release notes.
- Versions and dates come from parsed metadata; claims with unknown versions dropped.
- Migration snippets must parse (`ast.parse` / TS parse) and every "after" symbol must exist in the new API snapshot.
- Entities mapped to a canonical registry (IDs + aliases); unknown entities → review queue.
- Completeness gate: publish as full only if ≥ 80% of official-tier sources succeeded; otherwise labeled "partial". Footer shows "N/M sources checked".
- Source health tracked; parser returning zero items or missing fields = schema-drift alert; 3-day failures in nightly summary.
- Claude unavailable → publish deduped/clustered raw feed labeled "unsynthesized".
- Digests immutable; corrections appended as errata.
- (v1.1) Supersession links (yanked release, CVE, regression fix) add an "updated" banner to earlier items.
- "Report error" per item → review queue → errata + new eval case.
- `radar.mcp_server_discovered` emitted for newly seen MCP servers.

### 6.4 Live features (no accounts)
- **My stack**: stack tags in `localStorage` filter the digest.
- **Since last visit**: `localStorage` timestamp highlights new items.
- **Ask Radar**: query router — structured questions ("what broke in X since 0.3", "CVEs in Y") answered from diff/advisory data without an LLM; open questions → hybrid retrieval (SQLite FTS5 + embeddings, reciprocal rank fusion) → `qwen2.5:1.5b` answer; each sentence must cite a retrieved item or is stripped; low retrieval score → "not in Radar archive". Brute-force cosine is fine to ~50k items (upgrade path: `sqlite-vec`).
- **Upgrade impact report** (v1.1): paste `requirements.txt` / `package.json` / lockfile, parsed in the browser → cumulative breaking changes, deprecations, migrations and open advisories between pinned and latest versions, from published diff data. No LLM.
- **RSS** feed of Radar itself.

### 6.5 Weekly trends (v1.1)
One Claude call per week over validated digests + mention counts + velocity → rising/falling entities and patterns.

### 6.6 Pages
Today's digest · archive by date · cluster detail with citations and diffs · Ask · sources + health + signal formula · RSS · (v1.1) upgrade report, trends.

## 7. Blueprints

### 7.1 Blueprint schema (JSON)
Requirements profile (latency, volume, risk level, data sensitivity, autonomy level, budget), topology (`workflow | router | orchestrator-workers | supervisor | evaluator-optimizer | autonomous`), components (model per role, tools, memory, retrieval), guardrails, failure modes + mitigations, threat mapping (OWASP Top 10 for LLM Applications IDs), eval plan, observability, deployment, version + changelog.

### 7.2 Pattern catalog
Prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer, ReAct, plan-and-execute, reflection, handoff/swarm, HITL approval, RAG variants. Each: when to use, when not to, failure modes, cost profile. Owner-reviewed.

### 7.3 Decision engine (deterministic)
Rules map requirements profile → topology and mandatory components, e.g. high risk + write/payment actions → mandatory HITL gate; tight latency → no multi-agent loops; prefer workflow over autonomous agent unless open-ended. Every decision recorded as a visible "why" trace.

### 7.4 Linter (single Python implementation, also exposed as `/api/lint-blueprint`)
Every high-risk capability (write / exec / network / payment) has a mitigation; every failure mode maps to a component; threat IDs validated against the OWASP list; autonomous agent with write access and no approval gate = violation. Violations block publishing.

### 7.5 Cost model (deterministic)
Tokens per step × dated, source-cited pricing table × volume → monthly cost and latency estimate. Pricing table reviewed monthly and flagged when stale. Client-side sliders on detail pages. LLM never produces numbers.

### 7.6 Living links
Tool slots show Trust grades; only grade ≥ B tools recommended. Framework slots show latest stable version and open advisories from Radar. `radar.breaking_change_detected` or `trust.grade_changed` affecting a blueprint → `blueprint.needs_refresh` → re-queued.

### 7.7 Generation pipeline (evaluator-optimizer)
Generate → schema + linter → Claude reviewer pass against checklist → revise (max 2 loops) → owner approval → publish. Diagrams rendered from JSON to Mermaid by code. ~1 blueprint per night.

### 7.8 Live features
- **Finder**: structured requirements form (free text → Ollama pre-fills, user confirms fields) → decision engine → topology + nearest blueprints → Ollama adaptation notes.
- **BYOK custom blueprint** (v1.1): Claude in browser → JSON posted to `/api/lint-blueprint` (key never sent).
- **Compare** (v1.1): side-by-side.
- **Runnable starters** (v1.1): LangGraph skeletons with pinned versions, validated against Radar API snapshots.

### 7.9 Pages
Library (filter by use case, risk, topology) · blueprint detail (diagram, why-trace, components with Trust grades, cost sliders, failure-mode table, eval plan) · pattern catalog · Finder.

### 7.10 v1 library (~20)
E.g. support agent, docs RAG, code review agent, research agent, data analysis agent, generic fintech dispute handling (no employer-specific content).

## 8. Degradation Matrix

| Failure | Trust | Radar | Blueprints |
|---|---|---|---|
| Claude down / quota exhausted / auth expired | Rules-only results, labeled provisional | Unsynthesized feed, labeled | Generation paused; last good served |
| Ollama down | `/check` findings without explanations | Ask disabled; digest served | Finder form without adaptation notes |
| GitHub API rate-limited | Queue deferred | GitHub sources skipped; digest labeled partial | Links show last-known data |
| DB/disk failure | `web` serves last good manifest; batch halts + alerts | same | same |

## 9. Cross-cutting

### 9.1 UI system
Shared design tokens, dark default, per-planet accent colour. Evidence-first technical aesthetic: monospace data, provenance badges (`audit #412 · rules v1.3 · prompt v2`), evidence links on every claim, visible why-traces. Shared footer linking the three planets ("part of naveenlabs AI galaxy").

### 9.2 VPS safety
Compose resource caps: `batch` `cpus: 1.5` + memory limit; Ollama memory limit. Batch only in the IST night window (outside TradFi market hours). Live Ollama requests behind a concurrency semaphore → 503 + `Retry-After` when saturated.

### 9.3 Live endpoint hardening
Per-IP rate limits, request size limits, timeouts, strict CSP. No user accounts in v1. BYOK keys never reach the server.

### 9.4 Admin
`/admin` behind Traefik Basic Auth: review queue (F grades, LLM disagreements, blueprint approvals, reported errors, unknown entities), dead-letter queue, quota/AIMD status, Claude auth status, nightly summary.

### 9.5 Observability
Structured JSON logs, job metrics table, `/healthz`. Uptime Kuma (naveenlabs-ops): HTTP monitors on all three hosts + batch heartbeat push monitor (alerts on a missed nightly run).

### 9.6 Testing (TDD)
- Unit: rules, evidence library, scoring, diff engine, signal score, decision engine, linter, cost model, scheduler (AIMD), job engine (lease/retry/resume).
- Contract: LLM output schemas against recorded responses. CI never calls live Claude.
- Integration: full pipelines with the `fake` backend incl. failure injection (quota exhausted, malformed JSON, hallucinated quote, auth expiry).
- Eval harness in CI: Trust golden set (known-good servers, published tool-poisoning samples, crafted cases), Radar labeled API-break + impact set, Blueprints golden profiles + known-bad blueprints. Precision/recall drops fail the build; prompt changes gated on it.
- Web smoke: host routing and key pages per planet.
- CI: GitHub Actions.

### 9.7 Local development
venv runs `web` + `batch` against laptop Ollama and laptop Claude Code (owner's Pro). First full compose run happens on the VPS (optional: install Docker Desktop for local compose).

### 9.8 Deployment
DNS A records for three hosts → Traefik router `Host(trust…) || Host(radar…) || Host(blueprints…)` with automatic certificates → join naveenlabs-ai network for Ollama → Uptime Kuma monitors → verify → add three planets to the landing page's AI galaxy (`naveenlabs-home/script.js`).

## 10. Build Order

Each milestone gets its own implementation plan.

| Milestone | Scope |
|---|---|
| **M0 Core** | LLM gateway, job engine, quota scheduler, events, evidence library, provenance/publish, parsers, GitHub client, host routing, design system, admin |
| **M1 Trust v1** | Section 5 |
| **M2 Radar v1** | Section 6 minus upgrade report, trends, supersession links |
| **M3 Blueprints v1** | Section 7 minus BYOK custom, compare, starters |
| **M4 v1.1** | Radar upgrade report + trends + supersession; Blueprints BYOK + compare + starters |
| **Deploy** | Section 9.8, once VPS is back |

## 11. Deliberately Out of Scope

Redis / Celery / Kafka (SQLite + outbox suffices on one VPS), artifact signing (hash manifests suffice), feature flags, user accounts, email digests (RSS covers it), live repo-URL fetch in Trust, HTML scraping of any source.

## 12. To Verify During Implementation

- Exact `claude -p` flags for JSON output, disabling all tools, and single-turn execution; headless login persistence on the VPS.
- Current Claude Pro usage terms for personal automated batch use, and practical per-window capacity (AIMD will adapt, but initial allowance needs a sane default).
- Anthropic API direct-browser-access header name and CORS behavior for BYOK.
- Current OWASP Top 10 for LLM Applications version and IDs.
- Availability and rate limits of the MCP registry API, OSV, GHSA, HN Algolia, arXiv.
