FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip==26.3 && pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
COPY core ./core
COPY web ./web
COPY trust ./trust
COPY evals ./evals
COPY tests/fixtures/trust ./tests/fixtures/trust
COPY tests/fixtures/trust_redteam ./tests/fixtures/trust_redteam

FROM base AS web
USER app
EXPOSE 8000
CMD ["uvicorn", "web.app:build", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS batch
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*
USER app
# Claude Code CLI (native installer) for headless `claude -p`; auth comes from CLAUDE_CODE_OAUTH_TOKEN in batch.env
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/home/app/.local/bin:${PATH}"
CMD ["python", "-m", "core.worker"]
