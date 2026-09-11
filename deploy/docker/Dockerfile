# A hosted Pravrudhi engine: the multitenant design's shape (c), one container per edition, behind a web app on
# Vercel (ADR-0051 addendum 2). The engine's intelligence is the vendor layer, so this image carries no GPU; GPU
# work belongs to the artifact projects and, later, to cloud GPU. Build with the release the web app expects:
#
#   docker build --build-arg PRAVRUDHI_VERSION=0.5.5 -t pravrudhi-engine deploy/docker
#
# Run with the environment deploy/docker/README.md lists (identity required, local guard off, the Vercel origin
# allowed, a Supabase project to verify tokens against) and a volume at /data for the root.
FROM python:3.13-slim

ARG PRAVRUDHI_VERSION
RUN test -n "$PRAVRUDHI_VERSION" || (echo "PRAVRUDHI_VERSION is required" >&2 && exit 1)
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends git curl ca-certificates > /dev/null \
    && rm -rf /var/lib/apt/lists/*

# The two wheels every AxisMeru/pravrudhi release publishes. The kernel's own version is read from the release
# assets rather than assumed, so a kernel release does not need this file changed.
RUN set -eu; \
    api="https://api.github.com/repos/AxisMeru/pravrudhi/releases/tags/v${PRAVRUDHI_VERSION}"; \
    data="$(curl -sSf ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} "$api")"; \
    kernel="$(printf '%s' "$data" | grep -o '"browser_download_url": *"[^"]*pravrudhi_kernel[^"]*\.whl"' | head -1 | sed 's/.*"\(https[^"]*\)"/\1/')"; \
    engine="$(printf '%s' "$data" | grep -o '"browser_download_url": *"[^"]*pravrudhi-[0-9][^"]*\.whl"' | head -1 | sed 's/.*"\(https[^"]*\)"/\1/')"; \
    test -n "$kernel" && test -n "$engine"; \
    pip install --no-cache-dir --quiet "$kernel" "$engine"

VOLUME /data
ENV PRAVRUDHI_DISABLE_LOCAL_GUARD=1 \
    PRAVRUDHI_AUTH=required \
    PORT=8765
EXPOSE 8765
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
