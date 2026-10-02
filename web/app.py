from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
    app.state.templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.state.templates.env.filters["model_label"] = model_label
    app.state.templates.env.filters["date"] = lambda ts: (
        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else "")
    app.state.templates.env.filters["datetime"] = lambda ts: (
        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "")
    app.state.templates.env.filters["safe_url"] = lambda u: (
        u if isinstance(u, str) and u.startswith(("http://", "https://")) else "")
    app.state.ollama_gate = Gate(max_concurrent=2)
    app.state.check_backend = OllamaBackend(base_url=settings.ollama_url, timeout_s=30)
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

    return app


def build() -> FastAPI:
    """uvicorn entry point: `uvicorn web.app:build --factory`."""
    return create_app(Settings.from_env())
