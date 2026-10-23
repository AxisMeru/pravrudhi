# Development

How to set up, check, test and document a change. Names and meanings only: the values of any secret or setting
belong to the deployment, never to the repository.

## Set up

```bash
make init        # sets the git hooks path and runs `uv sync --all-groups`
```

The engine needs Python 3.13. `make init` does not touch your git identity.

## The one command that checks a change without a GPU

```bash
make smoke
```

It runs, in order: the import guard (`scripts/import_guard.py`), `ruff check` over the kernel, engine, tests and
scripts, `mypy` over the kernel and the engine, then the test suite. Run it before opening a pull request.

## Other make targets

`make hooks-test` runs the governance tests under `tests/governance`. `make glossary-lint` and
`make headline-check` run the vocabulary and the figures-must-trace checks. `make reproduce` regenerates the evidence
documents from the ledger and fails on any difference. `make ledger-replay` rebuilds the state view from the ledger
and verifies the chain. `make paper` builds the paper. The container images are built by `make kernel-image`,
`make exec-image` and `make ext-image`. The full list, with one line each, is in the `Makefile`.

## Tests

Kernel tests live in `pravrudhi_kernel/tests/`, engine tests in `tests/`. The kernel is held to a coverage floor in
CI. Tests that need a pinned external binary are skipped when it is absent; CI builds it so they run there.

## Continuous integration

`.github/workflows/` holds the jobs: `ci.yml` (kernel tests and type check, a Windows import smoke job, the engine's
tests and guards), `release.yml`, `pages.yml`, `workflow-change.yml` (a change to a workflow file needs its label),
`history-trailer-canary.yml` and `scheduled-guard-audit.yml`, plus `docs.yml`, which builds this documentation.
Guards in `scripts/check_*.py` run in CI and can be run locally.

## Documentation

This documentation is Sphinx. Build it with:

```bash
pip install -r sphinx/requirements.txt
pip install -e pravrudhi_kernel -e .
cd sphinx && make html          # output: sphinx/_build/html (not committed)
```

`make html` first regenerates `sphinx/reference/*.md` from the code (the CLI tree, the configuration files, the
environment variables, the HTTP routes and the package map), then runs `sphinx-build`. `make check-reference` fails if
the committed reference pages differ from what the code produces, which is the drift check CI runs. The `API
reference` pages are read from docstrings; to improve one, edit the docstring.

Write for a reader who has not seen the project: say what a thing does and what it does not do; give no
measurement here (figures live in the ledger and the evidence documents); never paste a secret, an endpoint id, a
host name or an account detail.

## Commits and branches

Work on a branch, never directly in a shared checkout. Open a pull request into `main`; the checks above must pass.
Commit messages carry no attribution trailers (a git hook and a CI canary enforce it).
