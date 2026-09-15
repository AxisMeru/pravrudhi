# P2a reachability evidence — signature (Track B reviewer), 2026-09-15 12:06 BST

`prabhasa-nyaya` `assistant/trackA/p2a-reachability` @ `6465552`: `scripts/p2a_yield_gate.py` (only `reachability` implemented; `inventory`/`exclude`/`replay`/`decide` exit 2 with an explicit refusal, verified) and `research/gates/P2a/reachability.json` (status PASS, 14 ids, control grounded + omission flagged naming the dropped element, unknown id MALFORMED).

Verified by me: rebuilt the `score` binary from a throwaway worktree at `6465552` (main `df759e5` + this branch's non-Lean files); its sha256 `fc319e75…1773a` equals the sha recorded in the JSON; re-ran the `reachability` subcommand against my binary into a scratch dir and compared: status PASS, per-id verdicts and omitted fields identical to the committed file. No teacher, no GPU.

**SIGNED at `6465552`** as satisfying the frozen prereg's reachability requirement (prereg `4aca28d`, "record one passing control and one correctly diagnosed omission through the actual check path per ID; unknown IDs must refuse"). The prereg's sampling gate is open on this requirement only; the tier floors, leakage index, teacher batch and the other four subcommands remain to be built before any `decide`.

Denial-scope framing (Track A, via lead) confirmed as the final spec: a denial refutes the Contract that declares it; an inner route's denial refutes that route only and the composition falls back to other routes (R2 operator semantics); an offence-level defeater is authored in the outer Contract's `denials` and refutes the composition regardless of route (authoring/placement rule). Never widen a limb defeater to the offence.
