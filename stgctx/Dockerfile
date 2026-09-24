# STAGING ONLY (Lead-2, 2026-09-24): engine 0.5.29 image + the serverless-judge branch wheel (fd8befe, under review).
FROM pravrudhi-engine:0.5.29
COPY pravrudhi-*.whl /tmp/
RUN pip install --no-cache-dir --no-deps --force-reinstall /tmp/pravrudhi-*.whl && rm -f /tmp/pravrudhi-*.whl
