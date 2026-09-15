"""Flatten accepted.jsonl (P2a teacher tier) into reader-friendly JSON for the seat-2 content read.
Usage: python3 flatten_accepted.py accepted.jsonl score_bin out.json
Only reads files; the reader agent never runs python or git."""
import json, subprocess, sys
acc, score_bin, out = sys.argv[1:4]
names = {}
rows = []
for line in open(acc, encoding="utf-8"):
    if not line.strip():
        continue
    r = json.loads(line)
    cid = r["contract_id"]
    if cid not in names:
        names[cid] = [l for l in subprocess.run([score_bin, "--describe-contract", cid], capture_output=True, text=True).stdout.split("\n") if l]
    rec = r["record"]; t = rec["target"]
    rows.append({
        "attempt_id": r["attempt_id"], "contract_id": cid, "shape": r.get("shape"), "teacher_model": r.get("teacher_model"),
        "required_elements": names[cid],
        "facts": {f["id"]: f["text"] for f in rec["prompt"]["facts"]},
        "elements": [{"id": e["id"], "status": e["status"], "evidence": [v.get("source_id") for v in (e.get("evidence") or [])]} for e in t["elements"]],
        "abstain": t.get("abstain"), "abstain_reason": t.get("abstain_reason"), "answer": t.get("answer"),
    })
json.dump({"n": len(rows), "records": rows}, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(len(rows), "records ->", out)
