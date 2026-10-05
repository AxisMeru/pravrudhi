# syntax=docker/dockerfile:1
#
# A hosted Pravrudhi engine: the multitenant design's shape (c), one container per edition, behind a web app on
# Vercel (ADR-0051 addendum 2). The engine's intelligence is the vendor layer, so this image carries no GPU; GPU
# work belongs to the artifact projects and, later, to cloud GPU. Build with the release the web app expects:
#
#   docker build --build-arg PRAVRUDHI_VERSION=0.5.7 -t pravrudhi-engine deploy/docker
#
# With a GitHub token, to raise the unauthenticated 60/hour API limit (never as --build-arg, see below):
#
#   printf '%s' "$GITHUB_TOKEN" > /tmp/gh_token && \
#   docker build --secret id=github_token,src=/tmp/gh_token --build-arg PRAVRUDHI_VERSION=0.5.7 \
#     -t pravrudhi-engine deploy/docker && rm -f /tmp/gh_token
#
# Run with the environment deploy/docker/README.md lists (identity required, local guard off, the Vercel origin
# allowed, a Supabase project to verify tokens against) and a volume at /data for the root.
FROM python:3.13-slim

ARG PRAVRUDHI_VERSION
RUN test -n "$PRAVRUDHI_VERSION" || (echo "PRAVRUDHI_VERSION is required" >&2 && exit 1)
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends git curl ca-certificates > /dev/null \
    && rm -rf /var/lib/apt/lists/*

# The two wheels every AxisMeru/pravrudhi release publishes. The kernel's own version is read from the release
# assets rather than assumed, so a kernel release does not need this file changed. The asset list comes from
# the release's own assets_url: the by-tag view has been seen listing no assets long after they were published.
#
# A GitHub token, if provided, must NEVER be an ARG or ENV (2026-09-26 incident: an earlier version of this
# file used `ARG GITHUB_TOKEN` -- BuildKit prints every RUN command's fully-expanded shell line in its build
# log and in `docker history`, so the token value was printed in cleartext the moment a real value was passed,
# even though the image itself never persisted it in a layer. BuildKit's own SecretsUsedInArgOrEnv warning
# was flagging exactly this and was ignored under time pressure -- token was rotated as a result.) A
# `--mount=type=secret` is read from a file at /run/secrets/<id> inside the RUN step only, never recorded in
# the build log, never baked into any layer, never visible via `docker history`.
#
# Usage: docker build --secret id=github_token,src=<path to a file containing just the token> ...
# The secret is OPTIONAL: with none provided, requests go out unauthenticated (the ~60/hour GitHub limit,
# fine for infrequent builds; provide the secret for anything more frequent).
RUN --mount=type=secret,id=github_token set -eu; \
    GITHUB_TOKEN=""; \
    [ -f /run/secrets/github_token ] && GITHUB_TOKEN="$(cat /run/secrets/github_token)"; \
    api="https://api.github.com/repos/AxisMeru/pravrudhi/releases/tags/v${PRAVRUDHI_VERSION}"; \
    release="$(curl -sSf ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} "$api")"; \
    assets_url="$(printf '%s' "$release" | grep -o '"assets_url": *"[^"]*"' | head -1 | sed 's/.*"\(https[^"]*\)"/\1/')"; \
    [ -n "$assets_url" ] || { echo "FATAL: release v${PRAVRUDHI_VERSION} not found" >&2; exit 1; }; \
    data="$(curl -sSf ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} "${assets_url}?per_page=100")"; \
    kernel="$(printf '%s' "$data" | grep -o '"browser_download_url": *"[^"]*pravrudhi_kernel[^"]*\.whl"' | head -1 | sed 's/.*"\(https[^"]*\)"/\1/')"; \
    engine="$(printf '%s' "$data" | grep -o '"browser_download_url": *"[^"]*pravrudhi-[0-9][^"]*\.whl"' | head -1 | sed 's/.*"\(https[^"]*\)"/\1/')"; \
    [ -n "$kernel" ] || { echo "FATAL: no pravrudhi_kernel wheel in release v${PRAVRUDHI_VERSION} (assets not published yet?)" >&2; exit 1; }; \
    [ -n "$engine" ] || { echo "FATAL: no pravrudhi wheel in release v${PRAVRUDHI_VERSION} (assets not published yet?)" >&2; exit 1; }; \
    pip install --no-cache-dir --quiet "$kernel" "$engine"; \
    command -v pravrudhi >/dev/null || { echo "FATAL: pravrudhi not on PATH after install" >&2; exit 1; }

# prabhasa-nyaya's compiled Lean `score` binary (2026-09-23: found missing from every prior image --
# every Lean-checker route 500'd in production). Optional at build time: `nyaya/` in the build context
# always exists (ensure_image places either the real binary or just `.keep` there, see run_gateway.sh),
# so this COPY never fails the build either way. A build WITHOUT the real binary must still succeed --
# the engine's own registry_elements/registry_check (pravrudhi/application/nyaya.py) already convert a
# missing binary into a clean 503, not a crash, so a real-binary-less image is a degraded-but-honest
# image, never a broken build.
# 2026-09-26: re-pinned to the 37-contract build, assistant/trackA/wave23-integration @636c540 in
# prabhasa-nyaya -- see configs/nyaya_agent.yaml's pinned_score_sha256 comment for the full provenance.
ARG NYAYA_SCORE_SHA256=700de3aadf50b482f4cf23499a03fd518cf72734bf8f51b950b5983555dea229
COPY nyaya/ /opt/nyaya/
RUN set -eu; \
    if [ -f /opt/nyaya/score ]; then \
        echo "nyaya score binary present -- verifying sha256 against NYAYA_SCORE_SHA256=$NYAYA_SCORE_SHA256"; \
        actual="$(sha256sum /opt/nyaya/score | cut -d' ' -f1)"; \
        if [ "$actual" != "$NYAYA_SCORE_SHA256" ]; then \
            echo "FATAL: nyaya score binary sha256 mismatch: got $actual, expected $NYAYA_SCORE_SHA256" >&2; \
            exit 1; \
        fi; \
        chmod +x /opt/nyaya/score; \
        echo "nyaya score binary verified and installed at /opt/nyaya/score"; \
    else \
        echo "WARNING: no nyaya score binary in the build context (nyaya/score) -- image built WITHOUT it;" >&2; \
        echo "         every Lean-checker route (checker=lean, /api/nyaya/registry/*) will answer 503 until" >&2; \
        echo "         a redeploy includes it. See deploy/docker/README.md." >&2; \
    fi

# The unforgeable hosted-image marker: the engine's boot refuses PRAVRUDHI_AUTH unset or `disabled` whenever this file exists,
# whatever the environment says (pravrudhi/deployment.py HOSTED_FILE; PRAVRUDHI_HOSTED_IMAGE below is only a display label).
RUN mkdir -p /etc/pravrudhi && printf 'hosted image\n' > /etc/pravrudhi/hosted-image
VOLUME /data
ENV PRAVRUDHI_DISABLE_LOCAL_GUARD=1 \
    PRAVRUDHI_AUTH=required \
    PRAVRUDHI_HOSTED_IMAGE=1 \
    PORT=8765 \
    PRABHASA_NYAYA_SCORE_BIN=/opt/nyaya/score
EXPOSE 8765
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
