# Build the documentation: `make html` writes sphinx/_build/html (not committed).
# `make reference` regenerates reference/*.md from the code; `make check-reference` fails on any drift.
SPHINXBUILD ?= sphinx-build
SOURCEDIR   = .
BUILDDIR    = _build

.PHONY: html reference check-reference clean

html: reference
	$(SPHINXBUILD) -b html $(SOURCEDIR) $(BUILDDIR)/html

reference:
	python gen_reference.py --write

check-reference:
	python gen_reference.py --check

clean:
	rm -rf $(BUILDDIR)
