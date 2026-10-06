# Trust

Evidence-based security grades for MCP servers and agent tools.

A self-contained app of the naveenlabs AI galaxy (siblings: Radar, Blueprints). Clone it, install, run: nothing else is needed.
Design: `docs/design.md` (one design for all three apps; this one implements §4 (core) and §5 (Trust)).
Architecture, security, reliability and operations: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Production at a glance: a hardened web container (non-root, read-only image, no Linux capabilities, CPU and memory
limits) with a Docker health check on `/healthz` (which opens the database); HSTS, a strict CSP and a permissions
policy on every response; exact dependency pins in `requirements.txt`.

## Local development (Windows)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

Run the web app (Chrome and Edge resolve `*.localhost` to 127.0.0.1):

```powershell
$env:PUBLIC_SCHEME="http"; $env:PUBLIC_PORT=":8000"; $env:ADMIN_USER="owner"; $env:ADMIN_PASSWORD="local-dev-password-123"
.venv\Scripts\python -m uvicorn web.app:build --factory --port 8000
```

Open http://trust.localhost:8000 and http://trust.localhost:8000/admin. Only this app's host answers; any other host is a 404.

Run the worker (uses the laptop's Claude Code login and Ollama):

```powershell
$env:PLANET_MODULES="trust.jobs"; $env:WEIGHTS="trust=100"
.venv\Scripts\python -m core.worker
```

## Trust audits

Trust does not publish, discover or refresh other people's repositories unless `TRUST_THIRD_PARTY=1` is set (hosting
policy: no public grades of third-party projects). Leave it unset in production.

Audit one repository by hand (rules only, no Claude quota):

```powershell
.venv\Scripts\python -m trust.cli owner/repo --no-llm
.venv\Scripts\python -m trust.cli --local path\to\checkout --no-llm
```

Drop `--no-llm` to add the Claude review (uses your Pro quota; `--model haiku` for a cheap run).
The worker runs the same pipeline as the `trust.audit` job when `PLANET_MODULES=trust.jobs`.
Rule quality gate: `.venv\Scripts\python -m evals.trust_eval`.

## Trust site

The worker schedules `trust.discover` and `trust.refresh` every 6 hours and `trust.publish` every 30 minutes. Discovery also reads the servers Radar reported as new from Radar's published digests (`RADAR_DATA_ROOT`, default `DATA_ROOT`), so it works when Trust and Radar are separate apps with separate databases.
Audits with critical findings, partial snapshots or reviewer disagreements wait in `/admin` until you approve,
reject or embargo them (embargo = publish counts now, details after 30 days).

Publish by hand and preview locally:

```powershell
.venv\Scripts\python -m trust.cli --publish --db data\planets.db --data-root data\published
# add --include-local to also show local/ CLI audits in a local preview (never in production)
$env:PUBLIC_SCHEME="http"; $env:PUBLIC_PORT=":8000"
.venv\Scripts\python -m uvicorn web.app:build --factory --port 8000
```

Then open http://trust.localhost:8000 (directory), `/check` (manifest check and BYOK) and `/methodology`.

- **Look:** Trust has its own theme pack in `web/static/themes/trust/` (security console: cool slate, IBM Plex, shield
  mark, severity colours, dark by default). The home is a console with the working checker beside the live-rules
  example; the published directory appears below it when there is one.

## Deploy (VPS)

1. DNS A record `trust` pointing at the VPS IP; Traefik runs on the VPS.
2. The naveenlabs-ai stack is running (it provides the `naveenlabs-ai_default` network and `ollama`); give its `ollama` service a memory limit (for example `mem_limit: 4g`) so it cannot crowd out other apps.
3. `cp .env.example .env`, `cp batch.env.example batch.env`; set `ADMIN_PASSWORD` (16+ characters; `/admin` stays disabled while it is the default or short); on the laptop run `claude setup-token` and paste the token into `batch.env`.
4. `docker compose up -d --build`; Traefik picks up the labels and issues the certificate. Wait for `docker inspect -f '{{.State.Health.Status}}' trust-web-1` to say `healthy`.
5. Monitoring: probe `https://<host>/healthz` from outside the server (the `atlas` repository's `uptime` workflow does this every 30 minutes); optionally an Uptime Kuma push monitor whose URL goes in `KUMA_PUSH_URL`.
6. Optional: `docker compose -f docker-compose.yml -f docker-compose.siblings.yml up -d` mounts the Radar app's data volume read-only, so discovery also reads the servers Radar reports as new.

## The shared core

`core/` (LLM gateway, job engine, publishing, events, review queue, scheduler) and the shared web kit (`web/app.py`, `web/admin.py`, `web/config.py`, base templates and stylesheet, and their tests) are identical copies of the master in the `atlas` repository, so this repository runs on its own. The list is `scripts/core_manifest.json`; `CORE_VERSION` names the master commit last synced.

```powershell
python scripts/sync_core.py --source PATH_OR_GIT_URL --check    # what differs from the master
python scripts/sync_core.py --source PATH_OR_GIT_URL            # pull the master's core into this repo
```

A change to the shared core belongs in the master first, then in each app repo with this script.
