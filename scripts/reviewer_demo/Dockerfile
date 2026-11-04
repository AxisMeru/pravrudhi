# Reviewer demo image. linux/amd64 on purpose: the pinned Lean `score` binary is Linux x86-64, so on an Apple-silicon Mac
# Docker Desktop runs this image under emulation. The binary is NOT in the image: mount it at /opt/nyaya/score; the app
# refuses to start unless its sha256 equals configs/nyaya_agent.yaml's pinned_score_sha256.
#
#   docker build --platform linux/amd64 -f scripts/reviewer_demo/Dockerfile -t pravrudhi-reviewer-demo .
#   docker run --rm --platform linux/amd64 -p 127.0.0.1:8765:8765 -v "/abs/path/to/score:/opt/nyaya/score:ro" pravrudhi-reviewer-demo
ARG BASE=python:3.13-slim
FROM --platform=linux/amd64 ${BASE}
# `ca` is an OPTIONAL build secret, only for building behind a TLS-intercepting proxy; a normal build passes nothing.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca SSL_CERT_FILE=/run/secrets/ca; fi; \
    pip install --no-cache-dir --quiet "uv==0.11.32"
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY pravrudhi_kernel ./pravrudhi_kernel
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    uv sync --frozen --no-dev --no-install-project --no-install-workspace
COPY src ./src
COPY configs ./configs
COPY scripts/reviewer_demo ./scripts/reviewer_demo
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app/pravrudhi_kernel/src \
    PRABHASA_NYAYA_SCORE_BIN=/opt/nyaya/score \
    REVIEWER_DEMO_HOST=0.0.0.0 \
    REVIEWER_DEMO_RECORD=/data/review_record.jsonl
EXPOSE 8765
CMD ["/app/.venv/bin/python", "scripts/reviewer_demo/app.py"]
