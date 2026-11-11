# Trust: what was built, and when

Trust's completed work, in order. How it works: [`ARCHITECTURE.md`](ARCHITECTURE.md). Commit messages have the
detail.

## Where things stand (2026-10-09)

- Live at `trust.naveenlabs.tech`: tool-list checker, methodology, bring-your-own-key review, Red Team agent.
- Batch audit reviews are **paused** (2026-10-11): they publish nothing public, so the daily spend bought nothing visible.
  31 audits wait in the queue; turn batch back on in the owner's console to resume.
- About 580 tests; rule evaluation at precision and recall 1.00.

## Timeline

| Date | Work |
|---|---|
| 2026-09-30 | Designed: audits of MCP servers where code decides the grade and a model only explains, with verified evidence |
| 2026-10-01 | Audit engine: snapshot, tree-sitter extraction for Python and TypeScript, rule set, scoring, evidence-checked model review; evaluation set at 1.00. Site: discovery, freshness, disclosure and embargo, `/check`, bring-your-own-key review, `/methodology`, publishing |
| 2026-10-02 | Became its own repository (self-contained: own database, worker, compose stack, CI); discovery reads Radar's published new servers when mounted |
| 2026-10-03 | Deployed; product design with light and dark themes; hardened container, HSTS, permissions policy, readiness check |
| 2026-10-06 | "Security console" identity: IBM Plex, shield mark, severity colours, dark by default; checker beside the live-rules example |
| 2026-10-07 | Red Team agent: analyst, catalogue of 8 attacks, two victim models with planted fake secrets, code verdicts, fix re-runs, A-F grade per model, regression pack; launch gate passed; switched on. Impersonation no longer forces an automatic F. Read-only GitHub token for repository runs |
| 2026-10-08 | Owner controls (switches, caps, visitor limits, weekly brake, stop-all) and the private control interface; audit reviews moved to OpenRouter with a daily cap; over-long replies cut to the schema; rate-limited calls retry in minutes |
| 2026-10-09 | Own architecture and history documents; operations runbook; a Red Team on/off switch in `/admin` (Radar and Blueprints already had one for their agents) |
| 2026-10-11 | Batch audit reviews paused from the owner's console (no public output); the private report lists recent runs and batch jobs (metadata only) for the console |
| 2026-11-11 | Red Team benchmark (`/benchmark`): the 8 known-bad tool lists on named models under three conditions (no defence, prompt warning, code guard), 3-5 tries per cell, code verdicts, no analyst; owner-run CLI with a hard spending cap and a publish step |

## Open items

- Decide whether to resume batch audits. They matter only if third-party grades are published (`TRUST_THIRD_PARTY=1`).
- Deferred minor from the Red Team review: regression packs keep per-run fake canaries. (The budget add is atomic since
  2026-11-11.)
