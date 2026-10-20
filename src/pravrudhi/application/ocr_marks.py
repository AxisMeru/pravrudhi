"""Mark, in a tool's answer, each item whose text was read from a scanned page (#575).

A2 records which pages of a paper were read from an image (`ocr_pages`, only for a matter that holds one). A tool whose rows
name a paper and a page (`doc_id` with `page`, or with `pages`) calls `mark_tree` on its finished answer: each such row gets
`from_ocr` and, when true, R1's one `ocr_marker` line; a row inside a paper's entry (no `doc_id` of its own) takes the paper's.
A matter with no scanned page is returned untouched (no key, and the
strings module is not imported).
"""

from __future__ import annotations

from typing import Any


def ocr_page_map(docs: list[dict[str, Any]]) -> dict[str, tuple[int, ...]]:
    """doc id -> its image-read pages, from A2's paper list; EMPTY unless a paper of the matter has any (nothing is marked)."""
    out = {str(d["doc_id"]): tuple(int(p) for p in d.get("ocr_pages", ())) for d in docs if "from_ocr" in d}
    return out if any(out.values()) else {}


def mark_tree(tree: Any, pages: dict[str, tuple[int, ...]]) -> Any:
    """Set `from_ocr` (and `ocr_marker` when true) on every dict in `tree` that has a known `doc_id` and a `page` or `pages`."""
    if not pages:
        return tree
    from pravrudhi.application.scanned_intake_strings import OCR_MARKER_LINE

    def walk(node: Any, inherited: str | None = None) -> None:
        if isinstance(node, dict):
            doc = node.get("doc_id") if isinstance(node.get("doc_id"), str) else inherited  # a row inside a paper's entry
            where = [node["page"]] if isinstance(node.get("page"), int) else node.get("pages")
            if doc in pages and isinstance(where, list) and where:
                node["from_ocr"] = any(p in pages[doc] for p in where)
                if node["from_ocr"]:
                    node["ocr_marker"] = OCR_MARKER_LINE
                else:
                    node.pop("ocr_marker", None)
            for v in node.values():
                walk(v, doc)
        elif isinstance(node, list):
            for v in node:
                walk(v, inherited)

    walk(tree)
    return tree
