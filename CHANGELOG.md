# Changelog

Release notes for the engine (`pravrudhi`). Versions before 0.5.44 are described in their GitHub release notes and commit
messages.

## 0.5.44

Fail-closed deployment hardening, release probe and gateway hardening. Nothing here changes a correctly configured
deployment, with one exception: a hosted image with an explicit `PRAVRUDHI_AUTH=disabled` (or auth unset or blank) now
refuses to start. A misconfigured deployment refuses to start instead of running open.

**Rebuild before you deploy.** The hosted-image marker below is baked into images BUILT FROM THIS RELEASE. An image built
from an earlier release has no marker, so none of the hosted-image refusals apply to it. Rebuild Studio, the engine containers
and the RunPod worker template from 0.5.44; do not rely on a pip upgrade or a restart of the old image.

### Boot refusals (breaking for misconfigured environments)

- **Unrecognised `PRAVRUDHI_AUTH` refuses to start.** Only `disabled`, `optional` and `required` (case-insensitive, trimmed)
  are accepted; unset or blank means `disabled` on a LOCAL install only. Before, any other value (`requried`, `off`, `true`,
  a quoted `"required"`) silently meant `disabled`, so every anonymous caller was the operator. In process an unrecognised
  value is treated as `required`. (#282)
- **Hosted engines refuse unset, blank and `disabled` `PRAVRUDHI_AUTH`.** Only `required` and `optional` start on a hosted
  image; there is no opt-in for `disabled`. A hosted image is identified by the file `/etc/pravrudhi/hosted-image`, baked in by the
  Dockerfile and not removable by any environment value (`PRAVRUDHI_HOSTED_IMAGE=0` or blank cannot switch it off; it needs no
  `/.dockerenv`, so it works on containerd, Kubernetes and RunPod). Only images built from this release carry the file. (#282)
- **Unrecognised `PRAVRUDHI_EDITION` refuses to start.** Editions are `studio`, `product` and `dev`. (#282)
- **Unrecognised `PRAVRUDHI_HOSTED_IMAGE` refuses to start, but only on an image WITHOUT the baked marker file.** The value
  is true (`1`, `true`, `yes`, `on`) or false (`0`, `false`, `no`, `off`). On an image that has `/etc/pravrudhi/hosted-image`
  the file decides and this variable cannot change the outcome. (#282)
- **One resolved edition.** The edition used by the routes, the whole-surface Studio gate and the vendor carve-out is now a single
  function. An unlabelled container resolves to the product ONLY when it is recognised as hosted (the baked marker, a true
  `PRAVRUDHI_HOSTED_IMAGE`, an older image's local-guard-off container, or a release install); a plain unlabelled container or a source checkout still resolves to `dev`. A hosted or release install
  can therefore no longer serve the Studio routes without the gate. (#282)

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
- **Request context reaches the worker threads.** The ContextVars of a request (the serving organisation and the like) are
  carried into the async-job pool and the agent's judge pool, so the serving guards hold inside those threads. (#256, #236;
  `api/partner.py`, `application/nyaya_agent.py`)
- **Tri-state element status in the agent.** The per-element map that feeds the assertions is now tri-state (`True`,
  `False`, `None`): an element nobody evaluated (`not_evaluated_second_unavailable`) can no longer read as a verdict.
  This changes `nyaya_agent`'s per-element status handling only; the wire format and the `assertions` the API returns are
  still booleans, so #56 is not closed for callers that read `assertions`. (#60)
- **Demo-snapshot export refuses to publish private or licensed text.** `demo-export` (`application/demo_export.py`) now
  refuses to write the snapshot if it still carries a private marker (home paths, personal and project emails, seat names,
  relay and team-session text, hostnames and addresses, endpoint ids; any case), or any 64-character window of statute text
  from the corpus; statute text in recorded prompts is replaced by a marker and team chatter strings are dropped whole.
  The public `demo.json` was regenerated with it. (#290, #292, #293)

### Tooling (not in the image)

Of the entries added after the boot hardening (#282/#283), only `api/partner.py`, `application/nyaya_agent.py` and
`application/demo_export.py` change the engine image (#256, #60, #290, #292); the boot-refusal and deployment entries above are
image behaviour too. The items below are scripts and CI and do not ship in the image.

- **Release probe** (`scripts/release_probe.py`): `PROBE_ADMIN_TOKEN` is optional; the admin checks print `SKIPPED` and a run
  without them exits `3` (INCOMPLETE). Exit codes: 0 all passed, 1 a check failed (wins over 3), 2 configuration error or
  unreachable, 3 incomplete. The probe never prints a token, and `PROBE_BASE_URL` must be an origin only. (#278)
- **Partner API smoke script** (`scripts/partner_smoke.py`): anonymous demo paths and a non-admin test key only, read-only
  or deliberately invalid requests, exit codes as the probe. Note that with a key the empty-fact request is metered before
  its 422, and the product Worker may refuse POSTs under its write block. (#288)
- **Scheduled whole-tree guard audit** with an alert that fails closed. (#88)
- **Fail-closed Hugging Face revision verifier** for the house judge. (#117)
