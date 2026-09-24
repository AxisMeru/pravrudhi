"""Where a route's config file is read from.

An installed engine carries its release's configs inside the wheel (`pravrudhi/assets/configs/`, pyproject's
`force-include`), and those are authoritative: the config belongs to the code it was released with. The engine's
root in a deployment is a data directory -- the hosted image mounts it at /data, and the Studio container mounts
a working checkout that may sit on any branch -- so a `configs/` found there can be from a different version
(2026-09-24: Studio's root pinned a score-binary sha three releases old). A source checkout or editable install
has no packaged copy, and reads `<root>/configs/<name>` as it always has. Neither present is a refusal, never a
default invented in code.
"""

from __future__ import annotations

from pathlib import Path

PACKAGED_CONFIG_DIR = Path(__file__).resolve().parent.parent / "assets" / "configs"


def config_file(root: Path, name: str) -> Path:
    packaged = PACKAGED_CONFIG_DIR / name
    if packaged.is_file():
        return packaged
    local = Path(root) / "configs" / name
    if local.is_file():
        return local
    raise FileNotFoundError(f"{name}: this release ships no copy ({packaged}) and {local} does not exist")
