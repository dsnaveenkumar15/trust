import importlib.util
import os
from dataclasses import dataclass

ALL_PLANETS = ("trust", "radar", "blueprints")

DEFAULT_OLLAMA_MODEL = "qwen2.5:1.5b"      # must be pulled on the Ollama host, or every live model feature is off


@dataclass(frozen=True)
class Settings:
    hosts: dict
    db_path: str
    data_root: str
    admin_user: str | None
    admin_password: str | None
    trust_proxy: bool
    ollama_url: str
    public_scheme: str
    public_port: str
    dispute_url: str = ""
    trust_data_root: str | None = None      # where Trust's published data is read from (Blueprints); default: data_root
    radar_data_root: str | None = None      # where Radar's published data is read from (Blueprints); default: data_root
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ask_model: str = ""                     # Ask on OpenRouter when set together with the key; else Ollama
    ask_daily_usd: float = 0.02             # Ask stops for the day when the app's hosted spend reaches this
    openrouter_key: str = ""
    planets: tuple | None = None            # which planets this deployment serves; default: the planets whose files are present

    @staticmethod
    def detect_planets() -> tuple:
        """The planets whose web module exists: a repo holding only Radar's files serves only Radar."""
        return tuple(p for p in ALL_PLANETS if importlib.util.find_spec(f"web.{p}_site") is not None)

    @property
    def enabled_planets(self) -> tuple:
        return tuple(self.planets) if self.planets else self.detect_planets()

    @property
    def trust_root(self) -> str:
        return self.trust_data_root or self.data_root

    @property
    def radar_root(self) -> str:
        return self.radar_data_root or self.data_root

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        return cls(
            hosts={
                "trust": env.get("TRUST_HOST", "trust.localhost"),
                "radar": env.get("RADAR_HOST", "radar.localhost"),
                "blueprints": env.get("BLUEPRINTS_HOST", "blueprints.localhost"),
            },
            db_path=env.get("DB_PATH", "data/planets.db"),
            data_root=env.get("DATA_ROOT", "data/published"),
            admin_user=env.get("ADMIN_USER") or None,
            admin_password=env.get("ADMIN_PASSWORD") or None,
            trust_proxy=env.get("TRUST_PROXY", "0") == "1",
            ollama_url=env.get("OLLAMA_URL", "http://localhost:11434"),
            public_scheme=env.get("PUBLIC_SCHEME", "https"),
            public_port=env.get("PUBLIC_PORT", ""),
            dispute_url=env.get("DISPUTE_URL", ""),
            trust_data_root=env.get("TRUST_DATA_ROOT") or None,
            radar_data_root=env.get("RADAR_DATA_ROOT") or None,
            ollama_model=env.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL,
            ask_model=env.get("ASK_MODEL", ""),
            ask_daily_usd=float(env.get("ASK_DAILY_USD") or 0.02),
            openrouter_key=env.get("OPENROUTER_API_KEY", ""),
            planets=_planets(env.get("PLANETS", "")),
        )


def _planets(raw: str) -> tuple | None:
    names = [n.strip() for n in raw.split(",") if n.strip()]
    unknown = [n for n in names if n not in ALL_PLANETS]
    if unknown:
        raise ValueError(f"unknown planet(s) in PLANETS: {', '.join(unknown)}")
    return tuple(p for p in ALL_PLANETS if p in names) or None
