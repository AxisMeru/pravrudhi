# Changelog

Release notes for the engine (`pravrudhi`). Versions before 0.5.44 are described in their GitHub release notes and commit
messages.

## 0.5.44

Fail-closed deployment hardening, release probe and gateway hardening. Nothing here changes a correctly configured
deployment; it makes a misconfigured one refuse to start instead of running open.

### Boot refusals (breaking for misconfigured environments)

- **Unrecognised `PRAVRUDHI_AUTH` refuses to start.** Only `disabled`, `optional` and `required` (case-insensitive, trimmed)
  are accepted; unset or blank means `disabled` on a LOCAL install only. Before, any other value (`requried`, `off`, `true`,
  a quoted `"required"`) silently meant `disabled`, so every anonymous caller was the operator. In process an unrecognised
  value is treated as `required`. (#282)
- **Hosted engines refuse unset, blank and `disabled` `PRAVRUDHI_AUTH`.** Only `required` and `optional` start on a hosted
  image; there is no opt-in for `disabled`. A hosted image is identified by the file `/etc/pravrudhi/hosted-image`, baked in by the
  Dockerfile and not removable by any environment value (`PRAVRUDHI_HOSTED_IMAGE=0` or blank cannot switch it off; it needs no
  `/.dockerenv`, so it works on containerd, Kubernetes and RunPod). (#282)
- **Unrecognised `PRAVRUDHI_EDITION` or `PRAVRUDHI_HOSTED_IMAGE` refuses to start.** Editions are `studio`, `product` and `dev`;
  the hosted marker is a true (`1`, `true`, `yes`, `on`) or false (`0`, `false`, `no`, `off`) value. (#282)
- **One resolved edition.** The edition used by the routes, the whole-surface Studio gate and the vendor carve-out is now a single
  function, so an unlabelled container or release install is the product and can no longer serve the Studio routes without the
  gate. (#282)

### Deployment

- **The gateway pins `PRAVRUDHI_AUTH=required` for the product container as well as Studio** (`-e`, after `--env-file`), so the
  product engine cannot be set to `AUTH=optional` or weakened through `chat.env`. (#283, #266)
- **`STUDIO_HOLD_KV=1`** starts the Studio tunnel without writing `engine_url_studio` to KV; the flag is read on every start, so keep
  it in `gateway.env` or the unit and in the restart checklist. (#279, #281)
- **Do not quote values in `chat.env`.** Docker keeps the quote characters, so `PRAVRUDHI_AUTH="required"` reaches the engine as
  `"required"` including the quotes and now refuses to boot (before it silently meant `disabled`).

### Engine

- **Hosted image: unavailable agents say "hosted image: agents run on the host"** instead of "CLI not installed" / "needs xvfb".
  The image sets `PRAVRUDHI_HOSTED_IMAGE=1`. (#277)

### Tooling

- **Release probe** (`scripts/release_probe.py`): `PROBE_ADMIN_TOKEN` is optional; the admin checks print `SKIPPED` and a run
  without them exits `3` (INCOMPLETE). Exit codes: 0 all passed, 1 a check failed (wins over 3), 2 configuration error or
  unreachable, 3 incomplete. The probe never prints a token, and `PROBE_BASE_URL` must be an origin only. (#278)
