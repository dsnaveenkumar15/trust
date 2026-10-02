from web.config import Settings


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        hosts={"trust": "trust.localhost", "radar": "radar.localhost", "blueprints": "blueprints.localhost"},
        db_path=str(tmp_path / "web.db"),
        data_root=str(tmp_path / "published"),
        admin_user=None,
        admin_password=None,
        trust_proxy=False,
        ollama_url="http://localhost:11434",
        public_scheme="http",
        public_port="",
        dispute_url="",
    )
    values.update(overrides)
    return Settings(**values)
