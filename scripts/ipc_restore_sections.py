"""Apply the fixed `ipc_pdf_parse` to the held MHA PDF text (`pdftotext -layout` of the PDF whose sha256 is in the asset's
`source`) and update `ipc_bodies_v1.json` for exactly these ids: the sections the old parser lost to a swallowed heading or a
leading footnote mark (17, 63, 139, 158, 213, 227, 481, 490, and 479, 364A) and the entries whose text had swallowed the NEXT
section (14, 57, 62, 138A, 157, 212, 226, 478, 480, 364). Every other entry is left byte-identical.
Usage: python scripts/ipc_restore_sections.py LAYOUT_TXT ASSET_JSON"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ipc_pdf_parse as P  # noqa: E402

RESTORE = ("17", "63", "139", "158", "213", "227", "481", "490", "479", "364A")  # the last two: found by the same fix
FIXED = ("14", "57", "62", "138A", "157", "212", "226", "478", "480", "364")
SHIPPED_NOW = {"IPC/Section " + s for s in ("17", "139", "213", "227", "490")}  # were listed as unreliable; now read from the PDF
PARSE_LIMITS = (
    "all sections are read from this one PDF; the printed amendment marks and footnotes are left out; sections whose text could "
    "not be read reliably are not shipped (listed in unreliable_ids_not_shipped); page refs are PDF page numbers"
)


def pdf_pages(raw: str) -> dict[str, int]:
    """The first PDF page on which each section's start line is found."""
    first: dict[str, int] = {}
    for i, pg in enumerate(raw.split("\x0c"), 1):
        for m in P.START.finditer("\x0c" + P.strip_footnotes(pg)):
            first.setdefault(m.group(1), i)
    return first


def main(layout: str, asset: str) -> None:
    raw = Path(layout).read_text(encoding="utf-8")
    parsed, pages = P.parse(raw), pdf_pages(raw)
    path = Path(asset)
    data = json.loads(path.read_text(encoding="utf-8"))
    old = {d["id"]: d for d in data["documents"]}
    docs = []
    for sid, body in parsed.items():
        key = "IPC/Section " + sid
        if key in old and sid not in FIXED + RESTORE:
            docs.append(old[key])
        elif sid in FIXED + RESTORE:
            prev = old.get(key, {})
            docs.append({"id": key, "act": "Indian Penal Code, 1860", "section": "Section " + sid, "title": body["title"],
                         "text": body["text"], "pdf_page": prev.get("pdf_page", pages[sid])})  # fmt: skip
    kept = [k for k in old if k not in {d["id"] for d in docs}]
    assert not kept, f"entries the parse no longer yields: {kept}"
    data["documents"] = docs
    data["unreliable_ids_not_shipped"] = [i for i in data["unreliable_ids_not_shipped"] if i not in SHIPPED_NOW]
    data["source"]["parse_limits"] = PARSE_LIMITS
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
