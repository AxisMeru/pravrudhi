#!/bin/sh
# Initialise the root once, then serve. `pravrudhi init` is idempotent on an initialised root, but a fresh volume
# is the common first boot and must not need a person.
set -eu
ROOT="${PRAVRUDHI_ROOT:-/data}"
if [ ! -d "$ROOT/.pravrudhi" ]; then
  pravrudhi init --root "$ROOT"
fi
exec pravrudhi app --root "$ROOT" --host 0.0.0.0 --port "${PORT:-8765}" --no-browser
