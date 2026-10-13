# Licita app image. Default command: the demo (synthetic data, no key). Mount real data and a profile to
# run a deployment; set MISTRAL_API_KEY for hosted Mistral or LICITA_LLM_BASE_URL for a self-hosted model.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 STREAMLIT_SERVER_PORT=8501 STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app

# dependencies first (cached layer), then the source
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra app --extra llm --extra docs
COPY src ./src
COPY profiles/example.toml ./profiles/example.toml
COPY .streamlit ./.streamlit
RUN uv sync --frozen --no-dev --extra app --extra llm --extra docs

RUN useradd --create-home --uid 10001 licita && mkdir -p /app/demo /app/data /app/spines /app/out /app/runs \
    && chown -R licita /app/demo /app/data /app/spines /app/out /app/runs /app/profiles
USER licita

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"
CMD ["licita", "demo", "app"]
