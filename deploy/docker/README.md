# Hosted engine

One container per edition, behind its web app (ADR-0051, addendum 2): Studio's engine behind
`pravrudhi.vercel.app`, the product's behind `pravrudhi-app.vercel.app`. No GPU in the image; the engine's
intelligence is the vendor layer.

Build: `docker build --build-arg PRAVRUDHI_VERSION=0.5.7 -t pravrudhi-engine deploy/docker`.

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
