"""Take the page-break leftovers out of the IPC bodies (`ipc_bodies_v1.json`): the PDF's page furniture that the parse kept
inside a section's text. Six shapes, each a plain regular expression on one entry's text (nothing else in a text is touched):

1. a page break inside a sentence (a form feed between words)       -> one space;
2. the NEXT section's number alone at the top of the next page, at the end of the text ("... India. \\f4.")  -> removed;
3. the NEXT section's number and its margin titles at the end ("\\n43.\\n"Illegal".\\n"Legally bound to do".")  -> removed;
4. a footnote's continuation at a page top ("\\f 1964, s. 2.", section 25)  -> removed;
5. a page number alone at a page top ("\\f116\\n", section 89)  -> removed;
6. the footnote separator rule (a line of dashes) at a page top (section 403)  -> removed.
Usage: python scripts/ipc_page_artifacts.py ASSET_JSON (rewrites the file; prints what it did per shape)."""

import json
import re
import sys
from pathlib import Path

NEXT_NUMBER = r"\s*\x0c{n}\.\s*$"
NEXT_HEADINGS = r"\n{n}\.\n(?:\"[^\"\n]+\"\.\n?)+$"
SHAPES = (
    ("footnote_continuation", re.compile(r"\s*\x0c\s*1964, s\. 2\.\s*$"), ""),  # section 25 only (anchored on its own words)
    ("page_number", re.compile(r"\s*\x0c116\n"), " "),  # section 89 only
    ("footnote_rule", re.compile(r" ?\x0c-{20,}\n"), "\n"),  # a line of dashes
)


def clean(text: str, own: str, next_id: str | None, counts: dict[str, int]) -> str:
    """The text without its page-break leftovers; `counts` gets +1 for each shape found."""
    if next_id:
        n = re.escape(next_id)
        for name, pat in (("next_number", NEXT_NUMBER.format(n=n)), ("next_headings", NEXT_HEADINGS.format(n=n))):
            new = re.sub(pat, "", text)
            if new != text:
                counts[name] = counts.get(name, 0) + 1
                text = new
    for name, pat, repl in SHAPES:
        if (own, name) in {("25", "footnote_continuation"), ("89", "page_number")} or name == "footnote_rule":
            new = pat.sub(repl, text)
            if new != text:
                counts[name] = counts.get(name, 0) + 1
                text = new
    new = re.sub(r"\s*\x0c\s*", " ", text)
    if new != text:
        counts["mid_sentence"] = counts.get("mid_sentence", 0) + text.count("\x0c")
        text = new
    return text


def main(asset: str) -> dict[str, int]:
    path = Path(asset)
    data = json.loads(path.read_text(encoding="utf-8"))
    docs = data["documents"]
    ids = [d["id"].rsplit(" ", 1)[1] for d in docs]
    counts: dict[str, int] = {}
    for i, d in enumerate(docs):
        d["text"] = clean(d["text"], ids[i], ids[i + 1] if i + 1 < len(ids) else None, counts)
    assert not [d["id"] for d in docs if "\x0c" in d["text"] or "\x0c" in d["title"]]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return counts


if __name__ == "__main__":
    print(main(sys.argv[1]))
