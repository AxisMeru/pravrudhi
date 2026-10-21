# ruff: noqa: E501
"""Parse the `pdftotext -layout` text of the official IPC 1860 PDF (Ministry of Home Affairs copy) into {section: {title, text}}.

Structure only: the page footers, footnote blocks, chapter and sub-headings and the amendment markers `N*[ ... ]` are removed; the words of a
section are the PDF text layer's, with line breaks and runs of spaces collapsed. Nothing is re-spelled (typesetting slips in the PDF stay)."""
import re

DASH = re.compile(r"^-{20,}\s*$")
FOOT = re.compile(r"^\s*\d+\.\s{1,12}\S")
PAGE = re.compile(r"^\s*\d{1,3}\s*$")
HEAD = re.compile(r"(?:^|\n)(\d+[A-Z]{0,2})\.\n\n+([^\n]+(?:\n[^\n]+)?)\n\n")


def strip_footnotes(raw: str) -> str:
    lines = raw.split("\n")
    out, i = [], 0
    while i < len(lines):
        if DASH.match(lines[i]):
            i += 1
            while i < len(lines):
                ln = lines[i]
                if DASH.match(ln):
                    i += 1
                    break
                if ln.strip() == "" or FOOT.match(ln) or ln.startswith("    "):
                    i += 1
                    continue
                break
            continue
        out.append(lines[i])
        i += 1
    txt = "\n".join(out)
    txt = re.sub(r"\n[ \t]*\d{1,3}[ \t]*\n(?=\n*(?:\d+[A-Z]{0,2}\.\n|\s))", "\n", txt)  # standalone page numbers
    return txt


def strip_markers(s: str, opened: bool = False) -> str:
    """Remove amendment markers 'N*[' and the bracket they open, keeping the words inside; drop lone 'N*' / '***' marks.
    `opened`: the section's own start line consumed an opener, so the first unmatched ']' closes it."""
    out, stack, i = [], ([True] if opened else []), 0
    while i < len(s):
        m = re.match(r"\d+\*+\s*\[", s[i:])  # the mark and its bracket may sit on two lines
        if m:
            stack.append(True)
            i += m.end()
            continue
        c = s[i]
        if c == "[":
            stack.append(False)
            out.append(c)
        elif c == "]":
            if stack and stack.pop():
                pass
            else:
                out.append(c)
        else:
            out.append(c)
        i += 1
    t = "".join(out)
    t = re.sub(r"\d+\*{2,}", "", t)  # a mark of two or more asterisks, even glued to the next word ('3****State')
    t = re.sub(r"\d+\*+(?=[\s,;.\)]|$)", "", t)
    t = re.sub(r"\*{3,}", "", t)
    return t


def tidy(body: str) -> str:
    paras = [re.sub(r"[ \t]+", " ", " ".join(p.split("\n"))).strip() for p in re.split(r"\n\s*\n", body)]
    return "\n".join(p for p in paras if p)


# The title runs to the first '.--' and may wrap onto the next line, but never across a blank line: a repealed section has no '.--',
# and a title allowed to cross a blank line swallowed the NEXT section's heading and text (62 -> 63, 226 -> 227, ...). A repealed
# section's bracketed line '[Title.] Rep. by ...' is its own start.
_NOBLANK = r"(?:(?!\n[ \t]*\n).)"
START = re.compile(
    r"(?m)^(?:[\x0c \t]+(?:\d+\*+\[?)?|(?=\d{1,3}[A-Z]{0,2}\.[ \t]))(\d{1,3}[A-Z]{0,2})(?:\.|(?=[ \t]+\"))[ \t]+"
    r"(?:(" + _NOBLANK + r"{3,260}?)(?:[.;][ \t]*-{1,2}|(?<=[a-z])--(?=[A-Z]))|(\[" + _NOBLANK + r"{3,260}?\]))",
    re.S,
)


def _trim_tail(chunk: str, next_id: str) -> str:
    """Cut the heading block of the NEXT section (and any chapter or sub-heading before it) off the end of this section's chunk."""
    nid = re.escape(next_id)
    pat = re.compile(
        r"(?:\n[ \t]*(?:CHAPTER [IVXLC]+[A-Z]*|[A-Z][A-Z ,'\-]{4,}|Of [^\n.]{3,140}|OF [^\n]{3,140}|[A-Z][a-z][^\n.]{3,120}[a-z])[ \t]*)*"
        r"\n+(?:[ \t]*" + nid + r"\.[ \t]*\n+[^\n]+\n+|" + nid + r"\.[ \t]+[^\n]+\n+)?[ \t]*$"
    )
    return pat.sub("", chunk.rstrip() + "\n")


def _drop_tail_paras(body: str, nid: str, ntitle: str) -> str:
    """Drop the NEXT section's heading block ('115.' then its title) and any chapter / sub-heading lines off the end of this body."""
    paras = body.split("\n")
    nt = re.sub(r"\W+", "", ntitle.lower())
    num = re.compile(r"\d+[A-Z]{0,2}\.?")
    while paras:
        last = paras[-1].strip()
        key = re.sub(r"\W+", "", last.lower())
        prev = paras[-2].strip() if len(paras) > 1 else ""
        if (
            not last
            or num.fullmatch(last)
            or (prev and num.fullmatch(prev) and not num.fullmatch(last))  # the title paragraph under a bare number
            or last.startswith(f"{nid}. ")
            or (nt and key and (key == nt or (nt.startswith(key) and len(key) > 12)))
            or re.match(r"^(CHAPTER [IVXLC]+[A-Z]*|OF |Of )", last)
            or re.fullmatch(r"[A-Z][A-Z ,'\-]{4,}", last)
            or re.fullmatch(r"[\]\s\d.]*", last)
        ):
            paras.pop()
            continue
        break
    # the next number alone at the top of a page can end up glued to this section's last line
    return re.sub(r"\s*\x0c" + re.escape(nid) + r"\.\s*$", "", "\n".join(paras))


def parse(raw: str) -> dict[str, dict[str, str]]:
    txt = strip_footnotes(raw)
    marks = list(START.finditer(txt))
    out: dict[str, dict[str, str]] = {}
    for k, m in enumerate(marks):
        sid = m.group(1)
        title = re.sub(r"\s+", " ", m.group(2) or m.group(3)).strip()
        end = marks[k + 1].start() if k + 1 < len(marks) else len(txt)
        chunk = txt[m.end():end]
        if k + 1 < len(marks):
            chunk = _trim_tail(chunk, marks[k + 1].group(1))
        body = tidy(strip_markers(chunk, opened=bool(re.match(r"\s*\d+\*+\[", m.group(0)))))
        nxt = marks[k + 1] if k + 1 < len(marks) else None
        if nxt is not None:
            body = _drop_tail_paras(body, nxt.group(1), re.sub(r"\s+", " ", nxt.group(2) or nxt.group(3)).strip())
        title = re.sub(r"\d+\*+\[?|\]", "", title).strip()
        if sid in out:  # a repeated number (a sub-clause line that looks like a section start): keep the first
            continue
        out[sid] = {"title": title, "text": body}
    return out
