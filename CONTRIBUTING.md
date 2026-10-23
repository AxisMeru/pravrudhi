# Contributing

Thank you for helping. The short version:

1. Work on a branch and open a pull request into `main`. Do not work directly in a shared checkout.
2. Run `make smoke` before you push: the import guard, `ruff`, `mypy` and the test suite, none of which needs a GPU.
3. Keep a change to one purpose. Constants go in `configs/`, not in code; a new behaviour starts with a failing test.
4. Commit messages carry no attribution trailers; the repository's git hook and a CI check enforce it.
5. Documentation is part of a change. If you add a command, a configuration key, an environment variable or a route,
   run `cd sphinx && make reference` and commit the regenerated pages; CI fails when they are stale.

The full guide (setup, every make target, the test layout, what each CI job checks, and how to build the
documentation) is [sphinx/narrative/development.md](sphinx/narrative/development.md). The map of the repository is
[sphinx/narrative/overview.md](sphinx/narrative/overview.md), and the design write-up is
[docs/architecture.md](docs/architecture.md).

Do not put secrets, account details, endpoint identifiers, real names or evaluation data in the repository or in a
pull request.
