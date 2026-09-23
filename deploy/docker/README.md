# Hosted engine

One container per edition, behind its web app (ADR-0051, addendum 2): Studio's engine behind
`pravrudhi.vercel.app`, the product's behind `pravrudhi-app.vercel.app`. No GPU in the image; the engine's
intelligence is the vendor layer.

Build: `docker build --build-arg PRAVRUDHI_VERSION=0.5.7 -t pravrudhi-engine deploy/docker`.

## The Lean checker binary (2026-09-23)

Every Lean-checker route (`checker="lean"` on ask/audit, and `/api/nyaya/registry/*`) calls out to
prabhasa-nyaya's compiled `score` binary via `nyaya_gold_score.score_bin_path()`, which resolves
`PRABHASA_NYAYA_SCORE_BIN` first. This image sets that env var to `/opt/nyaya/score`, but a plain
`docker build deploy/docker` on its own does **not** put a binary there — `deploy/docker/nyaya/` in
this repo holds only a `.keep` placeholder, deliberately (the binary is 5 MB, dynamically linked to
glibc, and not something to commit into a public repo's history).

`deploy/gateway/run_gateway.sh`'s `ensure_image()` is what actually supplies it: it copies the binary
from `NYAYA_SCORE_BIN_SRC` (default `$HOME/projects/prabhasa-nyaya/lean/.lake/build/bin/score`, this
host's sibling checkout) into a **temporary copy** of this directory before building, so the real
`deploy/docker/` tree is never modified. The Dockerfile verifies the copied binary's sha256 against
`NYAYA_SCORE_SHA256` (build-arg, defaults to the pinned release sha) and **fails the build** on a
mismatch — a build should never ship a binary silently different from what was verified to run.

**A build genuinely without the binary must still succeed.** If `nyaya/score` is absent from the
build context, the Dockerfile logs a clear warning and continues; the running container then has
`PRABHASA_NYAYA_SCORE_BIN` pointed at a file that doesn't exist, and every Lean-checker route answers
a clean `503` (`registry_elements`/`registry_check` in `application/nyaya.py` catch exactly this and
convert it, rather than crashing uncaught the way this was found in production on 2026-09-23) — a
degraded-but-honest image, never a broken build or a silent wrong answer.

**An image tag that already exists is skipped by default** (`ensure_image()`'s own short-circuit,
correct for an ordinary version bump). Run `run_gateway.sh --rebuild` to force a fresh build under the
SAME version tag — needed when the binary itself changed (added, updated) without a version bump.

To verify locally that a build actually has the binary and it works:
```bash
docker build --build-arg PRAVRUDHI_VERSION=0.5.24 -t pravrudhi-engine:test deploy/docker
docker run --rm pravrudhi-engine:test /opt/nyaya/score --describe-contract ipc405_misappropriation
```
(that second command only works if you first put a real binary at `deploy/docker/nyaya/score` before
building, or built via `run_gateway.sh` — a plain `docker build` against the checked-in `.keep`
placeholder will correctly report the file missing.)

Environment the container needs:

| variable | value | why |
|---|---|---|
| `PRAVRUDHI_EDITION` | `studio` or `product` | which surfaces `api/roles.py` exposes and what the engine calls itself |
| `PRAVRUDHI_AUTH` | `required` (image default) | every request carries a verified Supabase token; the engine is on the internet |
| `PRAVRUDHI_DISABLE_LOCAL_GUARD` | `1` (image default) | the loopback and same-origin token guard is for a local install |
| `PRAVRUDHI_ALLOWED_ORIGINS` | the web app's origin, e.g. `https://pravrudhi-app.vercel.app` | CORS for the browser that serves the interface |
| `SUPABASE_URL` | the project URL | JWKS and introspection for token verification |
| `PRAVRUDHI_ADMINS` (Studio) | the operator's account email | Studio admits only the operator (`api/roles.py` `ADMIN_ENV`) |
| vendor keys | per `configs/panel.yaml` | the models the engine routes to; each is a cost the operator has accepted |

The web app then gets `NEXT_PUBLIC_API_BASE=https://<engine host>` in its Vercel project environment.

Where the containers run, and on whose bill, is the operator's decision; this recipe makes it one deploy.
