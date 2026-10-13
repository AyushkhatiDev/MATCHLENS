FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY simulator ./simulator
COPY deploy/requirements-constraints.txt ./constraints.txt
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -c constraints.txt '.[agentic,realtime,presentation]'

FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d AS runtime
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 APP_ENV=demo APP_DEMO_CONTROLS=true APP_DIAGNOSTICS=false
WORKDIR /app
RUN useradd --create-home --uid 10001 matchlens
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/simulator ./simulator
COPY data/generated/phase5/seed99/match.jsonl data/generated/phase5/seed99/match_meta.json ./data/generated/phase5/seed99/
COPY THIRD_PARTY_NOTICES.txt ./THIRD_PARTY_NOTICES.txt
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=3)" || exit 1
CMD ["python","-m","uvicorn","simulator.production.public_api:app","--host","0.0.0.0","--port","8000","--workers","1","--timeout-graceful-shutdown","5","--no-access-log"]
