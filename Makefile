.PHONY: init smoke hooks-test ledger-replay reproduce contract-check decorative-check glossary-lint headline-check kernel-image exec-image paper

UV ?= uv

init:            ## git identity, hooks path, uv sync
	git config user.name "SharathSPhD"
	git config user.email "qbz506@york.ac.uk"
	git config core.hooksPath .githooks
	chmod +x .githooks/*
	$(UV) sync --all-groups

smoke:           ## TECHNICAL closure without GPU
	$(UV) run python scripts/import_guard.py
	$(UV) run ruff check pravrudhi_kernel src tests scripts
	$(UV) run mypy pravrudhi_kernel/src src
	$(UV) run pytest -q

hooks-test:
	$(UV) run pytest tests/governance -q

contract-check:  ## usage: make contract-check GATE=gates/gate_L0.json
	$(UV) run pravrudhi gate check $(GATE)

paper:
	$(MAKE) -C paper

ledger-replay:   ## rebuild state.json from the ledger and verify the chain and byte-equality
	$(UV) run pravrudhi replay --verify
reproduce:       ## regenerate docs/evidence from the ledger; diff must be empty
	$(UV) run pravrudhi evidence noise_floor --check
	$(UV) run pravrudhi evidence night1 --check
	$(UV) run pravrudhi evidence night2 --check
	$(UV) run pravrudhi evidence summary --check
	$(UV) run pravrudhi evidence noise_floor1 --check
	$(UV) run pravrudhi evidence noise_floor2 --check
	$(UV) run pravrudhi evidence night4 --check
	$(UV) run pravrudhi evidence summary4 --check
	$(UV) run pravrudhi evidence night5 --check
	$(UV) run pravrudhi evidence summary4-5 --check
	$(UV) run pravrudhi evidence hnight1 --check
	$(UV) run pravrudhi evidence external --check
decorative-check:  ## decorative-controller check on the last select batch (research/last_select.json)
	$(UV) run python scripts/decorative_check.py --batch research/last_select.json
glossary-lint:
	@echo "not implemented: P2" >&2; exit 2
headline-check:  ## numbers in README/paper/evidence must trace to a gate JSON or prereg file
	$(UV) run python scripts/headline_check.py
kernel-image:
	@echo "not implemented: L3" >&2; exit 2
BASE_IMAGE ?= nvcr.io/nvidia/pytorch:25.06-py3
exec-image:      ## build pravrudhi/exec-5090 from the local NVIDIA 25.06 lineage (ADR-0003)
	# BASE_IMAGE defaults to the host's lineage image when it exists. Running this without it rebuilt from the
	# PUBLIC nvcr base, which carries no transformers/TRL/PEFT: the image built fine, replaced a working
	# `:latest`, and every GPU job then died on `ModuleNotFoundError: No module named 'transformers'`.
	docker build --build-arg BASE_IMAGE=$${BASE_IMAGE:-$$(docker image inspect rtx5090-train:latest >/dev/null 2>&1 && echo rtx5090-train:latest || echo nvcr.io/nvidia/pytorch:25.06-py3)} \
	  -f docker/exec-5090.Dockerfile -t pravrudhi/exec-5090:$$(git describe --always --dirty) -t pravrudhi/exec-5090:latest .

# The external-scorers image. It carries `docker/jobs`, so it goes stale every time a job is added -- and a
# stale one does not fail loudly: `score_apps.py` was missing from it, so the `apps` bench scored 0.0000
# everywhere (ADR-0037). Needs network to build; runs without it.
.PHONY: ext-image
ext-image:
	docker build -f docker/ext-scorers.Dockerfile -t pravrudhi/ext-scorers:latest .
	docker run --rm --entrypoint sh pravrudhi/ext-scorers:latest -c 'for j in $$(ls /opt/pravrudhi/jobs/*.py); do echo "$$j"; done' | sort > /tmp/pravrudhi-ext-jobs.txt
	@for f in docker/jobs/*.py; do \
		b=$$(basename $$f); \
		grep -q "/opt/pravrudhi/jobs/$$b" /tmp/pravrudhi-ext-jobs.txt || { echo "MISSING from image: $$b"; exit 1; }; \
	done
	@echo "ext-scorers:latest carries every docker/jobs/*.py"
