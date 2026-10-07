# naveenlabs AI tools: architecture

How Trust, Radar, Blueprints and Chat are built, deployed, secured and kept running. This document is part of the
shared core, so every app repository (`trust`, `radar`, `blueprints`) carries the same copy; the monorepo `atlas` is
the master.

Last reviewed: 2026-10-04.

## 1. What runs where

| Public host | What it is | Code |
|---|---|---|
| `naveenlabs.tech` | Landing page (static, unprivileged nginx; browsers revalidate on every visit) | `naveenlabs-home` |
| `trust.naveenlabs.tech` | Security review of MCP server tool lists | `trust` (and `atlas`) |
| `radar.naveenlabs.tech` | Release digest and dependency upgrade check for AI libraries | `radar` (and `atlas`) |
| `blueprints.naveenlabs.tech` | Linted, priced reference architectures for AI agents | `blueprints` (and `atlas`) |
| `chat.naveenlabs.tech` | Small local model behind a rate-limited, guarded API | `naveenlabs-ai` |
| `tradfi.` / `web3.` | Finance apps (separate projects, same server) | `zerodha` / `binance` repos |

Not public (bound to `127.0.0.1`): Portainer and Uptime Kuma are reachable only from the owner's devices over a private
Tailscale network (see Operations); n8n and Flowise are configured but not running.

Everything runs on one VPS (Ubuntu, Docker Compose), behind one reverse proxy.

```
                      Internet
                         |
            UFW: 22, 80, 443 only (fail2ban on SSH)
                         |
              Traefik v3.7.13 (host network)
     TLS (Let's Encrypt), HTTP -> HTTPS, routes by Host header
        |          |           |           |          |
     home       trust-web   radar-web  blueprints   chat-api ---- ollama (internal network only)
    (nginx)        |           |        -web          |
                trust-      radar-     blueprints-
                batch       batch       batch
                   \           |           /
            one Docker volume per app: SQLite + published releases
                   (Blueprints and Trust read sibling releases read-only)
```

## 2. Application design

### One codebase, pluggable planets

- FastAPI with server-rendered Jinja templates. The `Host` header picks the planet; an unknown host gets 404.
- A deployment serves the planets whose modules are present (`PLANETS` overrides), so the same core runs as the
  monorepo or as a single app. The shared core is listed in `scripts/core_manifest.json` and kept identical across
  repos by `scripts/sync_core.py`.

### Web and batch are separate containers

- **web** answers requests. It reads published data and writes only the owner's decisions from `/admin`; rate-limit
  state is kept in memory. Its image is read-only.
- **batch** runs the durable job worker: collect, audit, digest, publish. Slow or failing jobs never block a page.

### Agent runtime (`core/agent/`)

Shared by Radar's upgrade agent and the Blueprints architect: the model gateway with budgets, fallback and cost tracking
(`gateway`), per-app settings under a prefix, `AGENT_` or `ARCHITECT_` (`config`), the run store and background runner
with one table set per app, `agent_*` or `architect_*` (`runs`), and the server-sent-events follower (`stream`). Models
flagged public (free tiers may keep prompts) are used only when the input allows it; everything else is sent with
data collection denied.

### Durable job engine (`core/jobs.py`, `core/worker.py`)

- Idempotent enqueue (a job key cannot be queued twice), leased claims, per-stage cached outputs, exponential
  backoff, dead-letter after the attempt limit.
- A lease that expires (worker died mid-job) counts as an attempt, so a poison job ends up dead instead of looping.
- Periodic jobs are scheduled idempotently per window, so a restart never doubles work.

### Publish, then serve (`core/publish.py`)

- Batch writes each dataset as an immutable release: files plus a manifest of SHA-256 hashes, then flips a
  `CURRENT` pointer. Unchanged content produces no new release.
- Web loads the release `CURRENT` points to and verifies its hashes before serving it; a broken or partial release
  is never shown. The last 10 releases are kept, so going back is a pointer change.
- Apps share data only through these published releases, mounted read-only. There is no shared database and no
  synchronous call between apps, so one app being down never takes another down.

### Model use is bounded and checked

- Scores, grades, impacts and costs are computed by code. Models label, explain and summarise; their output is
  parsed against a schema and rejected when it does not match.
- The scheduled model calls go through a quota guard (`core/scheduler.py`): a night window, an adaptive allowance per
  usage window and a circuit breaker. When the model is unavailable, pages say so ("unsynthesized") and keep
  serving the computed facts.
- Untrusted text (tool descriptions, release notes) is wrapped and labelled as untrusted inside prompts.
- "Bring your own key" features call the model provider from the visitor's browser; the key never reaches the
  server, and only those pages allow a connection to the provider.

### Upgrade agent (Radar)

An agent with a bounded loop, built so each model call is cheap to distrust. A LangGraph graph runs deterministic steps
in Python (dependency parsing, grounding in Radar's published API changes, a Python and TypeScript usage index) and
three bounded model steps (plan, investigate with read-only tools, write the report). Model calls go through one gateway
that tries a backup model, reads cost from the provider response and checks per-run budgets before every call. A code
verifier drops any finding whose quoted evidence is not on the cited line of the candidate's own file. Runs write every
event to SQLite, so the browser can follow a live run, resume after a reload, or replay a recorded one; limits are checked
and the run created under one write lock. The daily spend cap ($1.00) and an admin kill switch stop live runs; replays
keep working. Repo content is untrusted: it is fenced in prompts, the tools can only read the fetched snapshot, and
nothing from the repository is ever executed.

### Storage

- SQLite in WAL mode with a 30 s busy timeout, one database per app in that app's volume. One writer process per
  app (the batch worker) keeps SQLite's single-writer model safe.

## 3. Security

### Controls by threat

| Threat | Control |
|---|---|
| Traffic interception | TLS everywhere; HTTP redirects to HTTPS; `Strict-Transport-Security: max-age=31536000; includeSubDomains` |
| Script injection (XSS) | Strict CSP: scripts, styles and fonts from the site itself only, no inline script or style; templates auto-escape |
| Clickjacking | `frame-ancestors 'none'` and `X-Frame-Options: DENY` |
| Browser feature abuse | `Permissions-Policy` turns off camera, microphone, geolocation, payment, USB |
| Host header tricks | Only configured hosts are served; anything else is 404 |
| Request flooding | Per-IP rate limits (client IP = rightmost `X-Forwarded-For` behind the single proxy hop); `Content-Length` required and capped on API posts; a concurrency gate returns 503 with `Retry-After` |
| Admin takeover | Admin is disabled (404) unless a password of 16+ characters is set; every attempt spends a rate-limit token before the credential check; constant-time comparison; posts must be same-origin |
| API surface discovery | OpenAPI and docs pages are turned off |
| Prompt injection via scanned content | Untrusted text is delimited, model output is schema-checked, and code makes every decision |
| Chat abuse | Topic screen on the question and the reply, self-harm questions get a helpline reply, per-visitor and site-wide daily caps, at most 2 generations at once, reply length cap |
| Container breakout or lateral movement | Non-root users, read-only root filesystems for web services, all Linux capabilities dropped, `no-new-privileges`, CPU and memory limits; the model server has no public route |
| Exposed admin tools | Portainer, Kuma, n8n and Flowise listen on `127.0.0.1` only (Docker-published ports bypass UFW, so loopback binding is what keeps them private) |
| Leaked secrets | Secrets live in `.env` / `batch.env` on the server (`chmod 600`), never in git; deploy keys are read-only and per repository |
| Supply chain | Exact Python dependency pins; pinned Traefik, Ollama and nginx images; rebuilding produces what the tests passed on |
| Host compromise | UFW allows 22/80/443 (plus the private Tailscale interface) only; SSH accepts keys only, no passwords (`/etc/ssh/sshd_config.d/00-naveenlabs-hardening.conf`); fail2ban on SSH; unattended security upgrades; Docker logs rotated (10 MB x 3) |
| Admin consoles exposed | Portainer and Uptime Kuma have no public address; `tailscale serve` publishes them only on the tailnet, with valid certificates |
| The chat model starving other apps | Ollama is capped at 3 of 4 cores and 6 GB, so a reply always leaves a core for the trading apps |
| Policy complaints (hosting terms) | No third-party brand names in hostnames or page copy (enforced by a test); Trust does not name or grade other people's projects unless `TRUST_THIRD_PARTY=1`; every page states it is an independent project |

### Known gaps (decided or pending)

- SSH is key-only since 2026-10-03, with a single authorized key (the owner's laptop). Losing that key means using the
  hosting provider's browser console to get back in; keep a backup of the key.
- Traefik and Portainer mount the Docker socket. Read-only mounts do not limit the Docker API, so a compromise of
  either is a host compromise. Standard for this setup; both are kept updated deliberately, and Portainer is not
  public.
- The batch image installs the model provider's CLI at build time from its install script (not pinned).

## 4. Reliability

### Targets

A personal project on one server, so the targets are honest rather than ambitious:

- **Availability:** 99.5% per public host per month, measured by the external probe.
- **Data loss (RPO):** at most 24 hours (nightly backup).
- **Recovery (RTO):** about 2 hours to rebuild the server from git and the newest backup.

### Failure modes and what happens

| Failure | Detection | Recovery |
|---|---|---|
| Web process hangs or its database cannot open | Docker health check on `/healthz` (opens the database) marks it unhealthy; the external probe fails and emails the owner | Restart the container. Docker does not restart unhealthy containers by itself; alerting covers this |
| A process crashes | Container exits | `restart: unless-stopped` brings it back; leased jobs return to the queue |
| A job keeps failing | Attempts count up, including expired leases | Backoff, then dead-letter; visible on `/admin`, retry by hand |
| Model quota exhausted or model down | Quota guard and circuit breaker | Pages show computed facts marked "unsynthesized"; synthesis resumes on its own |
| GitHub rate limit | Provider reset time is honoured | Work resumes after the reset; a `GITHUB_TOKEN` raises the limit |
| A bad release is published | Hash verification refuses a broken release | Point `CURRENT` back at one of the last 10 releases |
| A bad deploy | Health gate during deploy; probe | `git checkout <previous commit>` and `docker compose up -d --build` |
| Memory pressure | Per-container limits | The model server is capped at 4 GB so it cannot starve the finance apps |
| Disk fills | 164 GB free; logs rotated; releases pruned; backups kept 14 days | Prune old images with `docker image prune` |
| Server lost | External probe | Rebuild: Docker, Traefik, clone repos, restore the newest backups (RTO above) |
| Certificate renewal fails | External probe fails when fewer than 14 days remain | Recreate the app container, or restart Traefik if its Docker connection went stale |

### Backups

- **What:** both PostgreSQL databases (finance apps) and the three SQLite databases (a consistent copy taken while
  the apps run).
- **When:** 02:00 UTC every night on the server (`/opt/backups`), kept 14 days.
- **Off-site:** the owner's laptop pulls them at 09:00 IST every day and keeps 30 days.
- **Restore-tested on every pull** (`naveenlabs-ops/scripts/verify-backups.py`): each SQLite backup is restored and
  must pass `PRAGMA integrity_check`; each PostgreSQL dump must be complete; the newest backup must be under 36 hours
  old. Any failure fails the task.

## 5. Operations

### Deploy (one app at a time)

```bash
cd /opt/<app> && GIT_SSH_COMMAND="ssh -i /opt/<app>/.deploy/deploy_key -o IdentitiesOnly=yes" git pull
docker compose up -d --build
docker inspect -f '{{.State.Health.Status}}' <app>-web-1     # wait for "healthy" before the next app
```

Rollback: `git checkout <previous commit>` in `/opt/<app>`, then `docker compose up -d --build`.

A deploy recreates the container, so each app is unreachable for about 4 seconds: Traefik only routes to a container
once its health check passes, and health checks poll every 2 s while a container starts (`start_interval`). Zero
downtime would need two copies behind the proxy (blue/green); not worth it at this scale.

### Monitoring

- **External probe:** GitHub Actions workflow `uptime` in `atlas`, every 30 minutes. Checks every public site
  (`/healthz` for the AI apps) and that each TLS certificate has more than 14 days left. A failed run emails the owner.
- **Health checks:** every web container has a Docker health check.
- **Logs:** the worker logs JSON; Docker keeps 30 MB per container.
- **Admin page** (`/admin`, owner only): job queue, dead letters, model quota, review decisions.
- **Uptime Kuma** (private, over Tailscale): 7 HTTP monitors (every public site, every 60 s, with certificate-expiry
  checks) and 3 heartbeat monitors (the Trust, Radar and Blueprints workers push to
  `http://uptime-kuma:3001/api/push/<token>`, set as `KUMA_PUSH_URL`; silence for ~30 min marks a worker down).
  Alert delivery (email or Telegram) is not configured yet: the dashboard and the external probe are the alerts.
- **Lesson (2026-10-04):** Kuma's first check found Web3 down for ~13 h. uvicorn 0.27 does not respawn workers retired by
  `limit_max_requests`, so the app went dark once each worker had served 1000 requests. Never set
  `limit_max_requests` on uvicorn < 0.30 (Web3 has a regression test).

### Private access (Tailscale)

The server is on the owner's Tailscale network as `naveenlabs-vps` (`100.x.y.z`, tailnet `example-tailnet.ts.net`).
Only devices signed in to that account can reach it this way; nothing here has a public address.

| Console | Address |
|---|---|
| Uptime Kuma | https://naveenlabs-vps.example-tailnet.ts.net:10000 |
| Portainer | https://naveenlabs-vps.example-tailnet.ts.net:8443 |

Port 443 on the tailnet address belongs to Traefik (it runs on the host network), so `tailscale serve` uses 8443 and
10000. Tailscale was installed from its official apt repository with `--accept-dns=false`, so the server's own DNS
(and certificate issuance) is untouched.

### Change policy

- Dependencies, Traefik, Ollama and nginx are pinned; bump one at a time with the test suite green.
- Shared core changes go into `atlas` first, then `scripts/split_repos.py --update` into each app repository.
- Generated files (compose, requirements, README of the app repos) come from `scripts/split_templates.py`.

## 6. Quality gates

- About 1,250 tests in `atlas`, plus each app repository's own full suite, run in CI on every push.
- Trust's rules have an evaluation set (precision and recall on labelled cases); CI fails if either drops.
- Tests also pin: security headers, the readiness check, hardened compose files, exact dependency pins, the
  independence notice, and that no page copy names a vendor or third-party brand.

## 7. Trade-offs we chose

- **One server.** Cheapest, simplest; the server is a single point of failure. Mitigated by off-site,
  restore-tested backups and an external probe; a second region is not worth it at this scale.
- **SQLite, not a database server.** One writer per app, fast reads, trivial backups. Moving to PostgreSQL is a
  change in `core/db.py` if write volume ever needs it.
- **Builds on the server, no image registry or staging.** Fewer moving parts. The cost: a deploy builds in place, so
  the health gate and git rollback are the safety net.
- **No autoheal for unhealthy containers.** Adding one means another container with Docker socket access; alerting
  plus a manual restart was chosen instead.
