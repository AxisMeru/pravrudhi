#!/usr/bin/env python3
"""Build the case-law FTS5 index (P3, `docs/decisions/LEG-PLAN-2026-09-23.md`).

Indexes the Supreme Court judgment PDFs (`--sc-root`, default the corpus-raw mount) and the
InJudgements parquet shards (`--injudgements-dir`), reporting extraction coverage as it goes so a run that
is killed partway still leaves a real number, not a guess. Output DB is a build artifact under
`research/nyaya/case_index/` -- gitignored, never committed (see `research/` in `.gitignore`).

Usage:
    uv run python3 scripts/build_case_index.py --out research/nyaya/case_index/cases.sqlite3
    uv run python3 scripts/build_case_index.py --out /tmp/x.sqlite3 --sc-limit 200  # smoke test
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravrudhi.application.case_index import (  # noqa: E402
    CaseRecord,
    case_id,
    extract_pdf_text,
    insert_case,
    iter_sc_pdfs,
    open_index,
    title_and_year_from_filename,
)


def build_from_sc_pdfs(conn, root: Path, limit: int | None, report_every: int = 200) -> dict:
    pdfs = list(iter_sc_pdfs(root))
    if limit:
        pdfs = pdfs[:limit]
    ok = 0
    failed: list[str] = []
    t0 = time.monotonic()
    for i, path in enumerate(pdfs, 1):
        result = extract_pdf_text(path)
        if result.ok:
            title, year = title_and_year_from_filename(path)
            insert_case(
                conn,
                CaseRecord(
                    case_id=case_id("sc_pdf", str(path)),
                    title=title,
                    court="Supreme Court",
                    year=year,
                    source="sc_pdf",
                    path_or_url=str(path),
                    text=result.text,
                ),
            )
            ok += 1
        else:
            failed.append(f"{path.name}: {result.error}")
        if i % report_every == 0:
            conn.commit()
            elapsed = time.monotonic() - t0
            print(
                f"[sc_pdf] {i}/{len(pdfs)} ({ok} ok, {i - ok} failed) "
                f"{elapsed:.0f}s elapsed, {elapsed / i:.2f}s/doc",
                file=sys.stderr,
            )
    conn.commit()
    return {"total": len(pdfs), "ok": ok, "failed": len(pdfs) - ok, "failed_examples": failed[:20]}


def build_from_injudgements(conn, parquet_dir: Path, report_every: int = 1000) -> dict:
    total = 0
    ok = 0
    for shard in sorted(parquet_dir.glob("*.parquet")):
        table = pq.read_table(
            shard, columns=["Titles", "Court_Name_Normalized", "Doc_url", "Text"]
        )
        rows = table.to_pylist()
        for i, row in enumerate(rows, 1):
            total += 1
            text = row.get("Text") or ""
            if not text.strip():
                continue
            insert_case(
                conn,
                CaseRecord(
                    case_id=case_id("injudgements", row.get("Doc_url") or str(total)),
                    title=row.get("Titles") or "",
                    court=row.get("Court_Name_Normalized") or "",
                    year=None,
                    source="injudgements",
                    path_or_url=row.get("Doc_url") or "",
                    text=text,
                ),
            )
            ok += 1
            if i % report_every == 0:
                conn.commit()
                print(f"[injudgements] {shard.name}: {i}/{len(rows)}", file=sys.stderr)
        conn.commit()
    return {"total": total, "ok": ok, "failed": total - ok}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--sc-root", type=Path, default=Path("/home/ss/fusion-project/corpus-raw/supreme_court_judgments")
    )
    ap.add_argument(
        "--injudgements-dir", type=Path, default=Path("/home/ss/fusion-project/corpus-raw/hf/injudgements")
    )
    ap.add_argument("--sc-limit", type=int, default=None)
    ap.add_argument("--skip-sc", action="store_true")
    ap.add_argument("--skip-injudgements", action="store_true")
    args = ap.parse_args()

    conn = open_index(args.out)
    report: dict = {}
    if not args.skip_sc:
        report["sc_pdf"] = build_from_sc_pdfs(conn, args.sc_root, args.sc_limit)
        print("SC PDF coverage:", report["sc_pdf"]["ok"], "/", report["sc_pdf"]["total"], file=sys.stderr)
    if not args.skip_injudgements:
        report["injudgements"] = build_from_injudgements(conn, args.injudgements_dir)
        print(
            "InJudgements coverage:",
            report["injudgements"]["ok"],
            "/",
            report["injudgements"]["total"],
            file=sys.stderr,
        )
    conn.close()
    print(report)


if __name__ == "__main__":
    main()
