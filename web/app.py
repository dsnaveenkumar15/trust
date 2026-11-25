import os
import sqlite3
from datetime import datetime, timezone
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.db import connect
from core.ollama import OllamaBackend
from web.config import Settings
from web.gate import Busy, Gate
from web.planets import PLANETS
from web.published import RadarPackages, TrustIndex
from web.ratelimit import RateLimiter
from web.security import apply_security_headers, planet_for_host

HERE = Path(__file__).parent

MAX_API_BODY = 600_000   # CheckRequest caps each field at 200 000 chars; rejected before the body is read


def render(request: Request, template: str, **ctx):
    settings: Settings = request.app.state.settings
    planet = request.state.planet
    return request.app.state.templates.TemplateResponse(request, template, {
        "planet": planet, "meta": PLANETS[planet], "planets": PLANETS, "hosts": settings.hosts,
        "scheme": settings.public_scheme, "port": settings.public_port, "enabled": settings.enabled_planets, **ctx,
    })


def _wire_trust(app: FastAPI, settings: Settings, homes: dict) -> None:
    from web import trust_byok, trust_check, trust_site
    app.state.trust_data = trust_site.TrustData(settings.data_root)
    app.state.check_limiter = RateLimiter(per_minute=10, burst=5)
    app.state.byok_limiter = RateLimiter(per_minute=20, burst=10)
    homes["trust"] = trust_site.directory
    app.include_router(trust_site.router)
    app.include_router(trust_check.router)
    app.include_router(trust_byok.router)
    _wire_redteam(app, settings)


def _wire_redteam(app: FastAPI, settings: Settings) -> None:
    import os

    from core.agent.gateway import chat_factory
    from core.agent.runs import Runner
    from core.github import GitHub
    from trust.redteam import runs as rt_runs
    from web import trust_redteam
    app.state.redteam_config = rt_runs.load_config()
    factory = lambda cfg: getattr(app.state, "redteam_factory", None) or chat_factory(cfg)  # noqa: E731  tests set redteam_factory
    github = lambda conn: getattr(app.state, "redteam_github", None) or GitHub(conn, token=os.environ.get("GITHUB_TOKEN") or None)  # noqa: E731
    work = rt_runs.make_work(lambda: app.state.redteam_config, factory, github)
    app.state.redteam_runner = Runner(settings.db_path, app.state.redteam_config, rt_runs.STORE, work, rt_runs.error_text)
    app.include_router(trust_redteam.router)
    from web import trust_bench
    app.include_router(trust_bench.router)
    from web import admin_trust
    app.include_router(admin_trust.router)
    app.state.admin_sections["trust"] = admin_trust.section
    _control(app, settings, rt_runs.STORE, "redteam", "Red Team", "redteam_config", "redteam_runner")
    try:                                                 # a restart ends runs that were in flight; old runs expire
        conn = connect(settings.db_path)
        try:
            rt_runs.STORE.ensure_schema(conn)
            rt_runs.STORE.interrupt_running(conn, time.time())
            rt_runs.STORE.expire(conn, time.time())
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _control(app: FastAPI, settings: Settings, run_store, prefix: str, label: str, config_attr: str, runner_attr: str) -> None:
    """Put one agent under the console's controls: effective settings, stop-all and the brake, live runs-at-once."""
    from core.control import agents as control_agents

    def concurrent() -> int:
        conn = connect(settings.db_path)
        try:
            return run_store.effective_config(conn, getattr(app.state, config_attr)).concurrent
        finally:
            conn.close()
    control_agents.install(run_store, lambda: os.environ.get("OPENROUTER_API_KEY", ""))
    control_agents.register(app, prefix, label, run_store, config_attr)
    getattr(app.state, runner_attr).limit = concurrent


def _wire_radar(app: FastAPI, settings: Settings, homes: dict) -> None:
    from radar.config import load_entities, load_sources
    from radar.embed import OllamaEmbedder
    from web import admin_radar, radar_ask, radar_site
    app.state.radar_data = radar_site.RadarData(settings.data_root)
    app.state.trends_data = radar_site.TrendsData(settings.data_root)
    app.state.radar_sources = load_sources()
    app.state.radar_registry = load_entities()
    app.state.report_limiter = RateLimiter(per_minute=5, burst=3)
    app.state.ask_limiter = RateLimiter(per_minute=10, burst=5)
    app.state.ask_embedder = OllamaEmbedder(base_url=settings.ollama_url, timeout_s=15)
    homes["radar"] = radar_site.home
    app.include_router(radar_site.router)
    app.include_router(radar_ask.router)
    app.include_router(admin_radar.router)
    app.state.admin_sections["radar"] = admin_radar.section
    _wire_radar_agent(app, settings)


def _wire_radar_agent(app: FastAPI, settings: Settings) -> None:
    from core.db import connect
    from radar.agent import runs as agent_runs
    from radar.agent.config import load_config
    from radar.agent.context import make_context_factory
    from web import radar_agent
    app.state.agent_config = load_config()
    app.state.agent_runner = agent_runs.Runner(settings.db_path, app.state.agent_config, make_context_factory(app, settings))
    app.include_router(radar_agent.router)
    _control(app, settings, agent_runs.STORE, "agent", "Upgrade agent", "agent_config", "agent_runner")
    try:                                                 # a restart ends runs that were in flight; old runs expire
        conn = connect(settings.db_path)
        try:
            agent_runs.ensure_schema(conn)
            agent_runs.interrupt_running(conn, time.time())
            agent_runs.expire(conn, time.time())
        finally:
            conn.close()
    except sqlite3.Error:
        pass                                             # the app still starts; /healthz reports the database as unready


def _wire_architect(app: FastAPI, settings: Settings) -> None:
    from blueprints.architect import runs as arch_runs
    from core.agent.gateway import chat_factory
    from core.agent.runs import Runner
    from web import blueprints_architect
    app.state.architect_config = arch_runs.load_config()
    factory = lambda cfg: getattr(app.state, "architect_factory", None) or chat_factory(cfg)  # noqa: E731  tests set architect_factory
    work = arch_runs.make_work(lambda: app.state.architect_config, factory, lambda: blueprints_architect.sources(app))
    app.state.architect_runner = Runner(settings.db_path, app.state.architect_config, arch_runs.STORE, work, arch_runs.error_text)
    app.include_router(blueprints_architect.router)
    _control(app, settings, arch_runs.STORE, "architect", "Architect", "architect_config", "architect_runner")
    try:                                                 # a restart ends runs that were in flight; old runs expire
        conn = connect(settings.db_path)
        try:
            arch_runs.STORE.ensure_schema(conn)
            arch_runs.STORE.interrupt_running(conn, time.time())
            arch_runs.STORE.expire(conn, time.time())
            arch_runs.STORE.expire_waiting(conn, time.time(), arch_runs.WAIT_SECONDS)
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _wire_blueprints(app: FastAPI, settings: Settings, homes: dict) -> None:
    from web import admin_blueprints, blueprints_api, blueprints_site
    app.state.blueprints_data = blueprints_site.BlueprintsData(settings.data_root)
    app.state.trust_index = TrustIndex(settings.trust_root)
    app.state.radar_packages = RadarPackages(settings.radar_root)
    app.state.lint_limiter = RateLimiter(per_minute=30, burst=10)
    app.state.finder_limiter = RateLimiter(per_minute=20, burst=10)
    app.state.prefill_limiter = RateLimiter(per_minute=10, burst=5)
    app.state.custom_limiter = RateLimiter(per_minute=10, burst=5)
    homes["blueprints"] = blueprints_site.home
    app.include_router(blueprints_site.router)
    app.include_router(blueprints_api.router)
    app.include_router(admin_blueprints.router)
    app.state.admin_sections["blueprints"] = admin_blueprints.section
    _wire_architect(app, settings)



def model_label(model_id) -> str:
    """Neutral size label for display; the real model id stays in values and data."""
    text = str(model_id or "").lower()
    for key, label in (("opus", "large model"), ("sonnet", "mid-size model"), ("haiku", "small model")):
        if key in text:
            return label
    return str(model_id or "")


WIRING = {"trust": _wire_trust, "radar": _wire_radar, "blueprints": _wire_blueprints}


def create_app(settings: Settings) -> FastAPI:
    enabled = settings.enabled_planets
    hosts = {planet: host for planet, host in settings.hosts.items() if planet in enabled}
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.admin_limiter = RateLimiter(per_minute=30, burst=30)
    app.state.admin_sections = {}      # planet -> provider(conn, now) -> dict, in wiring order
    app.state.control_agents = []      # agents under the console's controls (core.control.agents.register)
    app.state.templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.state.templates.env.filters["model_label"] = model_label
    app.state.templates.env.filters["date"] = lambda ts: (
        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else "")
    app.state.templates.env.filters["datetime"] = lambda ts: (
        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "")
    app.state.templates.env.filters["safe_url"] = lambda u: (
        u if isinstance(u, str) and u.startswith(("http://", "https://")) else "")
    app.state.ollama_gate = Gate(max_concurrent=2)
    app.state.check_backend = OllamaBackend(base_url=settings.ollama_url, model=settings.ollama_model, timeout_s=30)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def route_by_host(request: Request, call_next):
        planet = planet_for_host(request.headers.get("host", ""), hosts)
        length = request.headers.get("content-length", "")
        if planet is None and request.url.path != "/healthz":
            response = PlainTextResponse("unknown host", status_code=404)
        elif request.method == "POST" and request.url.path.startswith("/api/") and not length.isdigit():
            response = JSONResponse({"error": "Content-Length required"}, status_code=411)
        elif request.method == "POST" and request.url.path.startswith("/api/") and int(length) > MAX_API_BODY:
            response = JSONResponse({"error": "request too large"}, status_code=413)
        else:
            request.state.planet = planet
            response = await call_next(request)
        # Only Trust's BYOK deep audit and Blueprints' custom page talk to the Claude API, from the visitor's browser.
        browser_claude = planet == "trust" or (planet == "blueprints" and request.url.path == "/custom")
        apply_security_headers(response, "'self' https://api.anthropic.com" if browser_claude else "'self'")
        return response

    @app.exception_handler(Busy)
    async def busy(request: Request, exc: Busy):
        return JSONResponse({"error": "busy, retry shortly"}, status_code=503, headers={"Retry-After": "5"})

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        # Never echo the input: a NaN in it would make the error response itself unserialisable.
        return JSONResponse({"error": "invalid request",
                             "detail": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]}, status_code=422)

    @app.get("/healthz")
    def healthz():
        """Readiness: the process answers AND its database opens. Docker's healthcheck and the uptime probe use it."""
        try:
            conn = connect(settings.db_path)
            try:
                conn.execute("SELECT 1")
            finally:
                conn.close()
        except (sqlite3.Error, OSError):
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    homes: dict = {}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        handler = homes.get(request.state.planet)
        return handler(request) if handler else render(request, "home.html")

    for planet in enabled:
        WIRING[planet](app, settings, homes)
    from web.admin import router as admin_router
    app.include_router(admin_router)
    from web.control import router as control_router
    app.include_router(control_router)           # /internal/control: token-guarded, reached only by the console

    return app


def build() -> FastAPI:
    """uvicorn entry point: `uvicorn web.app:build --factory`."""
    return create_app(Settings.from_env())
