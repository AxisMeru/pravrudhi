import json
from pathlib import Path
from typing import Any

import typer

from pravrudhi import KERNEL_VERSION, __version__
from pravrudhi.application.gate import check_gate, emit_gate, sign_gate
from pravrudhi.application.replay import replay_command

app = typer.Typer(name="pravrudhi", no_args_is_help=True, help="Recursive self-improvement engine.")
gate_app = typer.Typer(help="Emit, check and sign gate JSON. Gates are never hand-edited.")
contract_app = typer.Typer(help="House alias for `gate check`.")
app.add_typer(gate_app, name="gate")
app.add_typer(contract_app, name="contract")

ROOT_OPT = typer.Option(Path("."), "--root", help="Repository root holding contracts/ and gates/.")
VERSION_OPT = typer.Option(False, "--version")
EVIDENCE_OPT = typer.Option(..., "--evidence")
BY_OPT = typer.Option(..., "--by")
NOTE_OPT = typer.Option("", "--note")
PROPOSER_ENDPOINT_OPT = typer.Option(
    "", "--proposer-endpoint",
    help="OpenAI-compatible endpoint to borrow for the proposer (e.g. a fleet host serving llama.cpp), leaving this GPU free",
)
EXT_RESULT_ARG = typer.Argument(..., help="lm-eval results.json or EvalPlus *_eval_results.json")
PLACE_JOB_ARG = typer.Argument("train", help="train | serve | agent | any")
POLICY_OPT = typer.Option(
    None, "--policy", help="selection arm for H1: efe (default, from prereg) | greedy | thompson | random"
)
NIGHT_OPT = typer.Option(1, "--night")
# Which track a night runs. Neither flag is the model track, unchanged, so every existing unit keeps working.
OBJECTIVE_OPT: str | None = typer.Option(
    None, "--objective", help="Run the night for this objective; the prereg that claims it supplies the rest."
)
NIGHT_CONFIG_OPT: Path | None = typer.Option(
    None, "--config", help="Frozen night pre-registration to run; default research/prereg/lora_night.yaml."
)
# The harness track's default is a different file, and one shared help string said `lora_night.yaml` on both.
# A config selector that misnames its own default is how the wrong night gets run.
HARNESS_CONFIG_OPT: Path | None = typer.Option(
    None, "--config", help="Frozen night pre-registration to run; default research/prereg/harness_night.yaml."
)
CASEHOLD_SOURCE_OPT = typer.Option(..., "--source", help="CaseHOLD val CSV on disk, fetched separately")
CASEHOLD_BENCH_OPT = typer.Option("casehold-val", "--bench")
APPS_SOURCE_OPT = typer.Option(..., "--source", help="APPS split on disk (test.jsonl or parquet), fetched separately")
APPS_COUNT_OPT = typer.Option(400, "--count")
APPS_SEED_OPT = typer.Option(0, "--seed")
APPS_BENCH_OPT = typer.Option("apps", "--bench")
SEED_RECIPE_OPT = typer.Option(
    [], "--seed-recipe",
    help="harness recipe JSON to admit as a candidate through the kernel's own door, alongside the proposer's; repeatable",
)
BUDGET_OPT = typer.Option(None, "--budget", help="GPU-hours; default from research/prereg/lora_night.yaml")
K_OPT = typer.Option(None, "--k")
TRAIN_PARQUET_OPT = typer.Option(Path(".pravrudhi/data/gsm8k-train.parquet"), "--train-parquet")
GGUF_OPT = typer.Option(None, "--gguf")
CACHE_OPT = typer.Option(Path(".pravrudhi/ext_cache"), "--cache")
LEDGER_OPT = typer.Option(None, "--ledger", help="default <root>/research/ledger.jsonl")
STATE_OPT = typer.Option(None, "--state", help="default <root>/research/state.json")
VERIFY_OPT = typer.Option(
    False,
    "--verify",
    help="Verify the hash chain and compare against the committed state; exit 1 on any difference.",
)
BUILD_PLAN_ARG = typer.Argument(..., help="Self-build plan YAML; see application.selfbuild.PACKAGED_EXAMPLE.")
BUILD_RUN_OPT = typer.Option(False, "--run", help="Dispatch the plan; otherwise preview only.")
REQUESTS_JSON_OPT = typer.Option(False, "--json")
REQUEST_ID_ARG = typer.Argument(..., help="request id, e.g. r-1a2b3c4d")
REQUEST_TEXT_ARG = typer.Argument(..., help="the operator's ask, verbatim")
REQUEST_INDEX_ARG = typer.Argument(..., help="criterion index, 0-based")
REQUEST_STATE_ARG = typer.Argument(
    ..., help="captured | clarified | planned | in_progress | delivered | verified | declined"
)
REQUEST_KIND_OPT = typer.Option(..., "--kind", help="commit | ledger_seq | file | command | screenshot | url")
REQUEST_REF_OPT = typer.Option(..., "--ref")


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context, version: bool = VERSION_OPT) -> None:
    if version:
        typer.echo(f"pravrudhi {__version__} (kernel {KERNEL_VERSION})")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@gate_app.command("emit")
def gate_emit(card_id: str, evidence: Path = EVIDENCE_OPT, root: Path = ROOT_OPT) -> None:
    out = emit_gate(
        card_id,
        contracts_dir=root / "contracts",
        gates_dir=root / "gates",
        evidence_file=evidence,
        kernel_release=KERNEL_VERSION,
    )
    typer.echo(f"wrote {out}")


def _check(path: Path, root: Path) -> None:
    problems = check_gate(path, contracts_dir=root / "contracts")
    if problems:
        for p in problems:
            typer.echo(f"FAIL {p}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"pass {path}")


@gate_app.command("check")
def gate_check(path: Path, root: Path = ROOT_OPT) -> None:
    _check(path, root)


@contract_app.command("check")
def contract_check(path: Path, root: Path = ROOT_OPT) -> None:
    _check(path, root)


@gate_app.command("sign")
def gate_sign(path: Path, by: str = BY_OPT, note: str = NOTE_OPT) -> None:
    sign_gate(path, by=by, note=note)
    typer.echo(f"signed {path} by {by}")


@app.command("replay")
def replay_cmd(
    ledger: Path | None = LEDGER_OPT, state: Path | None = STATE_OPT, verify: bool = VERIFY_OPT, root: Path = ROOT_OPT
) -> None:
    """Rebuild state.json from the ledger alone, and verify the chain with --verify."""
    ledger = ledger or root / "research" / "ledger.jsonl"
    state = state or root / "research" / "state.json"
    code, lines = replay_command(ledger, state, check=verify)
    for line in lines:
        typer.echo(line, err=code != 0)
    if code:
        raise typer.Exit(code=code)


study_app = typer.Typer(help="Pre-registered studies that write real observe rows.")
pool_app = typer.Typer(help="Seal benchmark pools into the kernel state directory.")
app.add_typer(study_app, name="study")
app.add_typer(pool_app, name="pool")
MODEL_OPT = typer.Option("Qwen/Qwen3-4B", "--model")
# A track whose floor must not share the model track's file (see noise_floor's dest comment).
NF_OUT_OPT: Path | None = typer.Option(None, "--out", help="Where to write the floor; default research/prereg/variance.json.")
POOL_OPT = typer.Option("gsm8k-test", "--bench")
TEMPLATE_OPT = typer.Option(Path("harness/prompts/eval/gsm8k_v1.md"), "--template")


@pool_app.command("seal-gsm8k")
def pool_seal(
    parquet: Path,
    bench: str = POOL_OPT,
    root: Path = ROOT_OPT,
    offset: int = typer.Option(0, "--offset"),
    count: int | None = typer.Option(None, "--count"),
) -> None:
    from pravrudhi.application.pool_admin import seal_gsm8k

    m = seal_gsm8k(root, parquet, bench, offset=offset, count=count)
    typer.echo(f"sealed {m['bench']}: {m['n_items']} items, pool_version {m['pool_version'][:16]}")


@pool_app.command("seal-apps")
def pool_seal_apps(
    source: Path = APPS_SOURCE_OPT,
    count: int = APPS_COUNT_OPT,
    seed: int = APPS_SEED_OPT,
    bench: str = APPS_BENCH_OPT,
    root: Path = ROOT_OPT,
) -> None:
    """Seal a held-out APPS slice as the harness track's second internal code pool (ADR-0029)."""
    from pravrudhi.application.pool_admin import seal_apps

    m = seal_apps(root, source, bench, count=count, seed=seed)
    typer.echo(json.dumps({k: v for k, v in m.items() if not isinstance(v, (list, dict))}, indent=2, sort_keys=True))


@pool_app.command("seal-mmlu")
def pool_seal_mmlu(
    bench: str = typer.Option("mmlu-law-val", "--bench"),
    cache: Path = CACHE_OPT,
    root: Path = ROOT_OPT,
) -> None:
    """Seal a law slice of MMLU as the product objective's internal choice pool (ADR-0035).

    Validation and dev splits only: the test splits are what the external proof tier scores, and the loop must
    not select on the items its own proof is measured against.
    """
    from pravrudhi.application.pool_admin import seal_mmlu

    m = seal_mmlu(root, cache, bench)
    typer.echo(
        f"sealed {m['bench']}: {m['n_items']} items, answer_kind {m['answer_kind']}, "
        f"pool_version {m['pool_version'][:16]}"
    )


@pool_app.command("seal-casehold")
def pool_seal_casehold(
    source: Path = CASEHOLD_SOURCE_OPT,
    bench: str = CASEHOLD_BENCH_OPT,
    root: Path = ROOT_OPT,
) -> None:
    """Seal CaseHOLD's validation split as the law tracks' internal choice pool (ADR-0041).

    Validation only. `train` is what the model track does rejection sampling on, and `test` is the external
    proof: selecting on either would have the loop score itself on what it learned from or is judged by.
    """
    from pravrudhi.application.pool_admin import seal_casehold

    m = seal_casehold(root, source, bench)
    typer.echo(
        f"sealed {m['bench']}: {m['n_items']} items, answer_kind {m['answer_kind']}, "
        f"pool_version {m['pool_version'][:16]}"
    )


@pool_app.command("seal-mbppplus")
def pool_seal_mbpp(root: Path = ROOT_OPT, cache: Path = CACHE_OPT) -> None:
    """Seal EvalPlus MBPP+ as a kernel pool (hidden tests executed only inside the sandbox)."""
    from pravrudhi.application.pool_admin import seal_mbpp_plus

    m = seal_mbpp_plus(root, cache)
    typer.echo(f"sealed {m['bench']}: {m['n_items']} items, pool_version {m['pool_version'][:16]}")


@app.command("preflight")
def preflight_cmd(
    model: str = MODEL_OPT,
    bench: str = POOL_OPT,
    template: Path = TEMPLATE_OPT,
    root: Path = ROOT_OPT,
    n_items: int = typer.Option(32, "--n-items"),
    batch_size: int = typer.Option(16, "--batch-size"),
) -> None:
    """Measure peak VRAM and tokens/s on this card with one real job; write
    research/prereg/measured_stack.json."""
    from pravrudhi.application.preflight import preflight

    out = preflight(
        root,
        model=model,
        bench_pool=root / ".pravrudhi" / "kernel" / "pools" / bench,
        template=template,
        n_items=n_items,
        batch_size=batch_size,
    )
    for k in (
        "gpu",
        "image_digest",
        "peak_gib_torch_allocated",
        "peak_gib_nvidia_smi",
        "tok_s_batched",
        "load_s",
        "gen_s",
    ):
        typer.echo(f"{k}: {out[k]}")


@study_app.command("noise-floor")
def study_noise_floor(
    model: str = MODEL_OPT,
    bench: str = POOL_OPT,
    template: Path = TEMPLATE_OPT,
    root: Path = ROOT_OPT,
    rotations: int = typer.Option(10, "--rotations"),
    seeds: int = typer.Option(3, "--seeds"),
    k: int = typer.Option(100, "--k"),
    exposure_cap: int = typer.Option(3, "--exposure-cap"),
    temperature: float = typer.Option(0.7, "--temperature"),
    max_new_tokens: int = typer.Option(512, "--max-new-tokens"),
    batch_size: int = typer.Option(16, "--batch-size"),
    night: int = typer.Option(0, "--night"),
    out: Path | None = NF_OUT_OPT,
) -> None:
    """R rotations x S seeds of the unmodified trainee, each a real kernel-scored observe row; writes
    variance.json, or --out for a track that must not share the model track's floor."""
    from pravrudhi.application.noise_floor import noise_floor

    var = noise_floor(
        root,
        model=model,
        pool_dir=root / ".pravrudhi" / "kernel" / "pools" / bench,
        template=template,
        rotations=rotations,
        seeds=seeds,
        k=k,
        exposure_cap=exposure_cap,
        temperature=temperature,
        max_new_tokens=max_new_tokens,
        batch_size=batch_size,
        night=night,
        out=out,
        log=typer.echo,
    )
    typer.echo(
        f"n_runs={var['n_runs']} mean={var['mean_pass_rate']:.4f} wilson={var['wilson_95']} "
        f"sigma_seed={var['sigma_seed']:.4f} sigma_rot={var['sigma_rot']:.4f}"
    )


@app.command("evidence")
def evidence_cmd(
    name: str = typer.Argument("noise_floor"),
    root: Path = ROOT_OPT,
    check: bool = typer.Option(False, "--check", help="Compare with the committed document; exit 1 on any difference."),
) -> None:
    """Render docs/evidence/<name>.md from the ledger alone (make reproduce)."""
    from pravrudhi.application.evidence import render_first_night, render_nights_summary, render_noise_floor

    if name == "noise_floor":
        text = render_noise_floor(root / "research" / "ledger.jsonl", root / "research" / "prereg" / "variance.json")
        dest = root / "docs" / "evidence" / "L3_noise_floor.md"
    elif name.startswith("noise_floor"):
        idx = int(name.removeprefix("noise_floor"))
        text = render_noise_floor(root / "research" / "ledger.jsonl", root / "research" / "prereg" / "variance.json", idx)
        dest = root / "docs" / "evidence" / f"P1_noise_floor_{idx}.md"
    elif name == "summary":
        text = render_nights_summary(root / "research" / "ledger.jsonl", (1, 2))
        dest = root / "docs" / "evidence" / "L4_summary.json"
    elif name.startswith("summary"):
        nights = tuple(int(x) for x in name.removeprefix("summary").split("-") if x)
        text = render_nights_summary(root / "research" / "ledger.jsonl", nights)
        dest = root / "docs" / "evidence" / f"P1_summary_{'_'.join(str(n) for n in nights)}.json"
    elif name == "search":
        from pravrudhi.application.evidence import render_search

        text = render_search(root / "research" / "ledger.jsonl")
        dest = root / "docs" / "evidence" / "P1_search_shape.md"
    elif name == "sensors":
        from pravrudhi.application.sensors import render_sensors

        text = render_sensors(root / "research" / "ledger.jsonl")
        dest = root / "docs" / "evidence" / "H6_sensor_screen.md"
    elif name == "external":
        from pravrudhi.application.external import render_external

        text = render_external(root / "research" / "ledger.jsonl")
        dest = root / "docs" / "evidence" / "P1_external.md"
    elif name.startswith("h1-"):
        from pravrudhi.application.evidence import render_h1

        spec = name.removeprefix("h1-")
        trk, _, nn = spec.partition(":")
        ns = tuple(int(x) for x in (nn or trk).split("-") if x)
        trk = trk if nn else "lora"
        text = render_h1(root / "research" / "ledger.jsonl", ns, track=trk)
        dest = root / "docs" / "evidence" / f"H1_{trk}_{'_'.join(str(n) for n in ns)}.md"
    elif name.startswith("hnight"):
        n = int(name.removeprefix("hnight") or "1")
        text = render_first_night(root / "research" / "ledger.jsonl", n, track="harness")
        dest = root / "docs" / "evidence" / f"P1_harness_night{n}.md"
    elif name.startswith("night"):
        text = render_first_night(root / "research" / "ledger.jsonl", int(name.removeprefix("night") or "1"))
        dest = root / "docs" / "evidence" / f"L4_{name}.md"
    else:
        typer.echo(f"unknown evidence document {name}", err=True)
        raise typer.Exit(code=2)
    if check:
        if not dest.exists() or dest.read_text() != text:
            typer.echo(f"DIFFERS: {dest}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"reproduced {dest} byte-identically")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    typer.echo(f"wrote {dest}")


@app.command("search")
def search_cmd(root: Path = ROOT_OPT) -> None:
    """Show the shape of the search: how it branches, and how often selection actually had a choice."""
    from pravrudhi.application.archive import ancestry_report, parent_map, selection_pressure

    ledger = root / "research" / "ledger.jsonl"
    if not ledger.exists():
        typer.echo("no ledger at research/ledger.jsonl")
        raise typer.Exit(1)

    parents = parent_map(ledger)
    report = ancestry_report(parents)
    typer.echo("ancestry")
    typer.echo(f"  candidates proposed   {report.nodes}")
    typer.echo(f"  roots                 {report.roots}")
    typer.echo(f"  distinct parents      {report.distinct_parents}")
    typer.echo(f"  deepest lineage       {report.max_depth} generation(s)")
    if report.widest:
        cid, n = report.widest
        typer.echo(f"  widest parent         {cid} with {n} children")

    pressure = selection_pressure(ledger)
    binding = [p for p in pressure if p.binding]
    typer.echo("")
    typer.echo("selection pressure (live pool on the night's own bench, against what the budget ran)")
    typer.echo(f"  {'night':>5} {'live':>6} {'ran':>5} {'declined':>9}")
    for p in pressure:
        typer.echo(f"  {p.night:>5} {p.live:>6} {p.selected:>5} {p.declined:>9}")
    typer.echo("")
    typer.echo(f"  nights where the budget forced a choice: {len(binding)} of {len(pressure)}")
    typer.echo(f"  candidate-nights declined in total:      {sum(p.declined for p in pressure)}")


@app.command("watch")
def watch_cmd(
    root: Path = ROOT_OPT,
    notify: bool = typer.Option(False, "--notify", help="send the findings to the configured chat, if any"),
    json_out: bool = typer.Option(False, "--json", help="machine-readable findings"),
) -> None:
    """Look for the shape of a stall: a loop repeating itself, a night that cost nothing, a cheap seat down.

    Everything in this engine reports success, so a check for errors finds nothing. These look for a decision
    that never changes and a night that spent nothing — the two shapes every stall this project has had shared.
    """
    from pravrudhi.application import watchdog

    findings = watchdog.check(root)
    if json_out:
        typer.echo(json.dumps({"findings": [f.to_dict() for f in findings]}, indent=1))
    else:
        typer.echo(watchdog.render(findings, root=root))
    # Only when the picture CHANGES. "Only when something is wrong" was not enough: the same cooling route was
    # reported every thirty minutes, which is the same overload by a slower route.
    if notify and watchdog.worth_announcing(root, findings) and findings:
        import os

        from pravrudhi.application import reach
        from pravrudhi.application.credentials import Secret
        from pravrudhi.application.telegram_inbox import paired_chat

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or paired_chat(root) or ""
        if token and chat:
            reach.send(root, kind="operator_reply", title=watchdog.render(findings), detail="",
                       token=Secret(provider="telegram", value=token), chat_id=chat,
                       filter_config=reach.NotificationFilter(kinds={"operator_reply"}))


@app.command("routes")
def routes_cmd(root: Path = ROOT_OPT) -> None:
    """Which model can be used right now, how well it has done, and when a spent one comes back."""
    from pravrudhi.application.roster import roster

    seats = roster(root)
    typer.echo(f"  {'route':<14} {'agent':<18} {'cost':>5} {'record':>9}  {'tiers':<34} status")
    for s in seats:
        rate = f"{s.successes}/{s.trials}" if s.trials else "-"
        status = "ready" if s.usable else f"back at {s.returns_at}"
        mark = "*" if s.sentinel else " "
        typer.echo(
            f" {mark}{s.id:<14} {s.agent:<18} {s.relative_cost:>5.2f} {rate:>9}  "
            f"{','.join(s.tiers) or '-':<34} {status}"
        )
    waiting = [s for s in seats if not s.usable]
    typer.echo("")
    typer.echo(f"  {len(seats) - len(waiting)} of {len(seats)} routes ready; * marks a sentinel")
    if waiting:
        soonest = min(s.returns_at or "" for s in waiting)
        typer.echo(f"  next to return: {soonest}")


@app.command("paper-data")
def paper_data_cmd(root: Path = ROOT_OPT) -> None:
    """Regenerate paper/generated/*.tex from the ledger alone, so the paper's tables never drift from it."""
    from pravrudhi.application.paper_data import write_tables

    for path in write_tables(root):
        typer.echo(f"wrote {path}")


@app.command("night")
def night_cmd(
    night: int = NIGHT_OPT,
    budget: float | None = BUDGET_OPT,
    k: int | None = K_OPT,
    policy: str | None = POLICY_OPT,
    proposer_endpoint: str = PROPOSER_ENDPOINT_OPT,
    root: Path = ROOT_OPT,
    train_parquet: Path = TRAIN_PARQUET_OPT,
    gguf: Path | None = GGUF_OPT,
    objective: str | None = OBJECTIVE_OPT,
    config: Path | None = NIGHT_CONFIG_OPT,
) -> None:
    """Run one budgeted night: propose -> deliberate -> execute -> dispose. Every observation is kernel-scored."""
    from pravrudhi.application.night import run_night
    from pravrudhi.application.night_plan import resolve
    from pravrudhi.application.spine import resolve_model_snapshot

    plan = resolve(root, objective=objective, config=config)
    typer.echo(
        f"night {night}: {plan.config.name} on {plan.bench} ({plan.answer_kind}), floor {plan.variance.name}"
        + (f", objective {plan.objective}" if plan.objective else "")
    )
    if gguf is None:
        # The proposer's GGUF comes from whichever config is running, not from lora_night unconditionally.
        gguf = resolve_model_snapshot("Qwen/Qwen3-30B-A3B-GGUF") / str(plan.cfg["proposer"]["gguf"])
    # The config's corpus wins over the CLI default, because a track's training data belongs with the track:
    # a choice night sampling the GSM8K parquet would hand `#### 18` to a scorer that only reads letters.
    corpus = plan.train_corpus or train_parquet
    if plan.train_corpus is not None:
        typer.echo(f"           training corpus {corpus}")
    out = run_night(
        root, night=night, budget_gpu_h=budget, k=k, train_parquet=corpus, gguf=gguf,
        log=typer.echo, selection_policy=policy, proposer_endpoint=proposer_endpoint, plan=plan,
    )
    typer.echo(json.dumps(out, indent=2))


panel_app = typer.Typer(help="Ask many vendors the same thing and record every answer identically.")
PANEL_PROMPTS_ARG = typer.Argument(..., help="JSONL with an `id` and a `prompt` per line.")
PANEL_VENDOR_OPT: list[str] = typer.Option([], "--vendor", help="Repeatable. Default: every reachable vendor.")
PANEL_OUT_OPT = typer.Option(Path("research/panel"), "--out")
PANEL_CONFIG_OPT: Path | None = typer.Option(
    None, "--panel-config", help="Per-vendor parameters; default configs/panel.yaml when it exists."
)
PANEL_PARAM_OPT: list[str] = typer.Option(
    [], "--param", help="Repeatable tuning override, `vendor:key=value` (e.g. codex-cli:temperature=0.7)."
)
app.add_typer(panel_app, name="panel")


@panel_app.command("vendors")
def panel_vendors_cmd() -> None:
    """Every declared vendor, whether it can be asked right now, and why not if it cannot.

    Declared and unreachable is a normal state: the direct-API vendors are built before their keys exist so
    that when a key lands nothing has to be designed in a hurry. Bring-your-own-key reads the named
    environment variable, so a vendor record never holds a secret.
    """
    from pravrudhi.application.panel import VENDORS

    for vid in sorted(VENDORS):
        v = VENDORS[vid]
        ok, why = v.reachable
        mark = "ready" if ok else "-"
        typer.echo(f"{vid:16s} {v.interface:15s} {mark:6s} {why}")


@panel_app.command("run")
def panel_run_cmd(
    prompts: Path = PANEL_PROMPTS_ARG,
    vendor: list[str] = PANEL_VENDOR_OPT,
    out: Path = PANEL_OUT_OPT,
    panel_config: Path | None = PANEL_CONFIG_OPT,
    param: list[str] = PANEL_PARAM_OPT,
) -> None:
    """Ask every named vendor every prompt. A vendor that cannot answer is recorded as a gap.

    Never substituted: `swarm` falls back to a working seat because its job is to get work done, and doing
    that here would attribute one vendor's answer to another, which in a comparison reads as a result.

    Parameters come from `configs/panel.yaml` and then from `--param`, and whichever ran is what the run's own
    manifest records -- the same vendor at a different temperature is a different measurement.
    """
    from pravrudhi.application.panel import PANEL_CONFIG, VENDORS, load_vendors, run_panel, tuned

    rows = [json.loads(line) for line in prompts.read_text().splitlines() if line.strip()]
    ids = list(vendor) if vendor else [v for v in sorted(VENDORS) if VENDORS[v].reachable[0]]
    vendors = tuned(load_vendors(ids), config=panel_config or PANEL_CONFIG, overrides=param)
    typer.echo(f"asking {len(vendors)} vendor(s) {len(rows)} prompt(s): {', '.join(v.id for v in vendors)}")
    answers = run_panel(out, rows, vendors)
    gaps = [a for a in answers if a.error]
    for v in vendors:
        got = [a for a in answers if a.vendor == v.id and not a.error]
        typer.echo(f"  {v.id:16s} answered {len(got)}/{len(rows)}")
    if gaps:
        typer.echo(f"{len(gaps)} gap(s) recorded, not filled:")
        for a in gaps[:5]:
            typer.echo(f"  {a.vendor} {a.prompt_id}: {(a.error or '')[:120]}")
    typer.echo(f"wrote {out / 'panel' / 'answers.jsonl'}")


@app.command("inbox")
def inbox_cmd(root: Path = ROOT_OPT) -> None:
    """List promotion packs awaiting the operator (badge from replay; nothing here is hand-set)."""
    from pravrudhi.application.night import inbox_listing

    rows = inbox_listing(root)
    if not rows:
        typer.echo("inbox empty")
    for r in rows:
        typer.echo(f"{r['night']} {r['candidate']} badge={r['badge']} signed={r['signed']} {r['pack']}")


@app.command("init")
def init_cmd(root: Path = ROOT_OPT, model: str | None = typer.Option(None, "--model")) -> None:
    """Make this project ready for a night: kernel state dir, config, pre-registrations, prompts, genesis ledger."""
    from pravrudhi.application.init import init_project

    out = init_project(root, model=model)
    typer.echo(f"initialised {out['root']} (isolation {out['isolation']}); created {len(out['created'])} files")
    for c in out["created"]:
        typer.echo(f"  {c}")


objective_app = typer.Typer(help="What you are trying to achieve, and whether it is happening.")
app.add_typer(objective_app, name="objective")

FROM_OPT = typer.Option(None, "--from", help="copy a packaged example objective instead of writing one by hand")
INTENT_OPT = typer.Option(None, "--intent", help="what you want, in your own words")
TRACK_OPT = typer.Option(None, "--track", help="ledger track this objective's evidence accumulates under")
METRIC_OPT = typer.Option(None, "--metric", help="benchmark metric, named as the evidence document names it")
TOOL_OPT = typer.Option("lm-eval", "--tool", help="lm-eval | evalplus | prabhasa")
DOMAIN_OPT = typer.Option("", "--domain")
TARGET_OPT = typer.Option(None, "--target-delta", help="how much improvement counts as success; omit for 'better is better'")


def _objective_or_exit(root: Path, oid: str) -> Any:
    from pravrudhi.application.objectives import load_all

    for o in load_all(root):
        if o.id == oid:
            return o
    typer.echo(f"no objective {oid!r} in {root}", err=True)
    raise typer.Exit(1)


@objective_app.command("new")
def objective_new(
    objective_id: str = typer.Argument(..., help="short id, lowercase and hyphens"),
    source: str | None = FROM_OPT,
    intent: str | None = INTENT_OPT,
    track: str | None = TRACK_OPT,
    metric: str | None = METRIC_OPT,
    tool: str = TOOL_OPT,
    domain: str = DOMAIN_OPT,
    target_delta: float | None = TARGET_OPT,
    root: Path = ROOT_OPT,
) -> None:
    """State what you want the loop to achieve. An objective with no benchmark is refused."""
    from pravrudhi.application.objectives import Benchmark, Objective, ObjectiveError, copy_example, examples, write

    try:
        if source:
            path = copy_example(root, source)
            typer.echo(f"wrote {path} from the packaged example {source!r}; edit the intent to your own")
            return
        if not (intent and track and metric):
            typer.echo(
                "give --intent, --track and --metric, or --from one of: " + (", ".join(examples()) or "none"), err=True
            )
            raise typer.Exit(2)
        obj = Objective(
            id=objective_id,
            intent=intent,
            track=track,
            benchmarks=(Benchmark(id=metric.split()[0], tool=tool, metric=metric),),
            domain=domain,
            target_delta=target_delta,
        )
        typer.echo(f"wrote {write(root, obj)}")
    except ObjectiveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e


@objective_app.command("list")
def objective_list(root: Path = ROOT_OPT) -> None:
    """Every objective in this workspace, with how far along it is."""
    from pravrudhi.application.objectives import load_all, problems, progress

    ledger = root / "research" / "ledger.jsonl"
    objs = load_all(root)
    if not objs:
        typer.echo("no objectives yet; try: pravrudhi objective new my-goal --from prabhasa-nyaya")
    for o in objs:
        rows = progress(o, ledger) if ledger.exists() else []
        states = ", ".join(f"{p.benchmark}: {p.state}" for p in rows) or "no ledger yet"
        typer.echo(f"{o.id}  [{o.track}]  {states}")
        typer.echo(f"    {o.intent.strip().splitlines()[0][:100]}")
    for name, why in problems(root):
        typer.echo(f"! {name} will not load: {why}", err=True)


@objective_app.command("show")
def objective_show(objective_id: str, root: Path = ROOT_OPT) -> None:
    """The objective and its standing, as JSON. Every number comes from the ledger."""
    from pravrudhi.application.objectives import summary

    typer.echo(json.dumps(summary(root, _objective_or_exit(root, objective_id)), indent=2, sort_keys=True))


@objective_app.command("progress")
def objective_progress(objective_id: str, root: Path = ROOT_OPT) -> None:
    """Baseline, current and the difference, per benchmark."""
    from pravrudhi.application.objectives import progress

    obj = _objective_or_exit(root, objective_id)
    typer.echo(obj.intent.strip())
    typer.echo("")
    for p in progress(obj, root / "research" / "ledger.jsonl"):
        typer.echo(f"{p.benchmark}  [{p.state}]")
        if p.state == "unmeasured":
            typer.echo(f"    {p.reason}")
            continue
        assert p.baseline is not None
        typer.echo(f"    baseline {p.baseline.value:.4f} +/-{p.baseline.stderr:.4f}  n={p.baseline.n}  {p.baseline.model}")
        if p.latest is None:
            typer.echo(f"    {p.reason}")
            continue
        typer.echo(f"    current  {p.latest.value:.4f} +/-{p.latest.stderr:.4f}  n={p.latest.n}")
        assert p.delta is not None and p.delta_lo is not None and p.delta_hi is not None
        verdict = "improvement" if p.significant and p.delta > 0 else (
            "regression" if p.significant else "not distinguishable from no change"
        )
        typer.echo(f"    change   {p.delta:+.4f}  [{p.delta_lo:+.4f}, {p.delta_hi:+.4f}]  {verdict}")
        if p.target_delta is not None:
            typer.echo(f"    target   {p.target_delta:+.4f}  met={p.met}")


@objective_app.command("loom")
def objective_loom(objective_id: str, root: Path = ROOT_OPT) -> None:
    """The objective's plan as Loom source: readable, editable, and not yet run."""
    from pravrudhi.application.intent import compile_intent
    from pravrudhi.application.loom import lower
    from pravrudhi.application.recipes import installed, library

    obj = _objective_or_exit(root, objective_id)
    typer.echo(lower(compile_intent(obj, tuple(library()), installed_skills=frozenset(installed()))))


@objective_app.command("subagents")
def objective_subagents(
    objective_id: str, run: bool = typer.Option(False, "--run", help="dispatch; otherwise preview"), root: Path = ROOT_OPT
) -> None:
    """Fan the objective's plan out to subagents, one scoped task per step. Their output is a proposal."""
    from pravrudhi.agents.registry import build_agent
    from pravrudhi.application.intent import compile_intent
    from pravrudhi.application.recipes import installed, library
    from pravrudhi.application.subagents import dispatch_plan, preview

    obj = _objective_or_exit(root, objective_id)
    plan = compile_intent(obj, tuple(library()), installed_skills=frozenset(installed()))
    if not run:
        for t in preview(obj, plan, root):
            typer.echo(f"{t['step']:22s} {t['tier']:10s} {t['agent']}/{t['model'] or 'default'}")
        typer.echo("(preview; add --run to dispatch)")
        return
    for r in dispatch_plan(obj, plan, root=root, build_agent=lambda n, m: build_agent(root, n, m), log=typer.echo):
        typer.echo(f"{'ACCEPT' if r.accepted else 'REJECT'} {r.step} [{r.route}] {r.wall_s:.0f}s")


@app.command("build")
def build_cmd(plan: Path = BUILD_PLAN_ARG, run: bool = BUILD_RUN_OPT, root: Path = ROOT_OPT) -> None:
    """Preview or dispatch a self-build plan against the engine's own tree, one task per line."""
    from pravrudhi.agents.registry import build_agent as make_agent
    from pravrudhi.application.selfbuild import SelfBuildError, load_plan, preview, run_plan

    try:
        tasks = load_plan(plan)
    except SelfBuildError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e

    if not run:
        for t in preview(tasks, root):
            typer.echo(f"{t['task_id']:32s} {t['tier']:10s} {t['agent']}/{t['model'] or 'default'}")
        typer.echo("(preview; add --run to dispatch)")
        return

    for r in run_plan(root, tasks, build_agent=lambda n, m: make_agent(root, n, m), log=typer.echo):
        typer.echo(f"{'ACCEPT' if r.accepted else 'REJECT'} {r.task_id} [{r.route}] {r.wall_s:.0f}s")


@app.command("update")
def update_cmd(
    root: Path = ROOT_OPT,
    apply_now: bool = typer.Option(False, "--apply", help="apply the update if every safeguard clears"),
    channel: str | None = typer.Option(None, "--channel", help="dev (git checkout) or release (GitHub Release)"),
    rollback_now: bool = typer.Option(False, "--rollback", help="switch back to the previous release install"),
    if_due: bool = typer.Option(False, "--if-due", help="do nothing unless check_interval_min has elapsed"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Is a newer release available; and with --apply, catch this install up under the safeguards."""
    from dataclasses import asdict

    from pravrudhi.application.updates import status

    if rollback_now or apply_now:
        from pravrudhi.application import update_apply

        if channel not in (None, "dev", "release"):
            raise typer.BadParameter("channel must be dev or release")
        if if_due and not rollback_now and not update_apply.should_check(root):
            # A scheduler that fires more often than the configured interval must not spend a check on every tick:
            # the GitHub releases API allows 60 unauthenticated calls an hour per address, and two installs behind
            # one address exhausted it, after which every install refused with a rate-limit reason.
            if as_json:
                typer.echo(json.dumps({"applied": False, "reason": "not due", "rolled_back": False, "version": None}))
            else:
                typer.echo("not due: the configured check interval has not elapsed")
            raise typer.Exit(0)
        result = (
            update_apply.rollback(root)
            if rollback_now
            else update_apply.apply(root, channel=channel)  # type: ignore[arg-type]
        )
        if as_json:
            typer.echo(json.dumps(asdict(result), sort_keys=True))
        else:
            typer.echo(("applied " if result.applied else "not applied: ") + result.reason)
        raise typer.Exit(0)

    st = status()
    if as_json:
        typer.echo(json.dumps(st, indent=2, sort_keys=True))
        return
    cur = st["current"]
    typer.echo(f"running {cur['version']} (kernel {cur['kernel_version']})" + (
        f", git {cur['git_describe']}" if "git_describe" in cur else ""
    ))
    if st["latest"] is None:
        typer.echo("could not reach GitHub to check for a newer release")
    elif st["update_available"]:
        typer.echo(f"update available: {st['latest']['tag']} ({st['latest']['url']})")
        typer.echo(f"  {st['how']}")
    else:
        typer.echo(f"up to date with the latest release ({st['latest']['tag']})")


DEMO_DEST_OPT = typer.Option(Path("app/frontend/public/demo.json"), "--dest", help="where the snapshot is written")


@app.command("heartbeat")
def heartbeat_cmd(
    root: Path = ROOT_OPT,
    loop: bool = typer.Option(False, "--loop", help="keep beating at the configured interval"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """One heartbeat: find the most neglected undone objective step and dispatch it; with --loop, keep going."""
    import time as _time

    from pravrudhi.agents.registry import build_agent
    from pravrudhi.application.heartbeat import beat, load_config

    while True:
        rec = beat(root, dispatch=lambda n, m: build_agent(root, n, m))
        if as_json:
            typer.echo(json.dumps(rec.to_dict(), sort_keys=True))
        else:
            # A beat chooses an objective step, a request criterion, or a request awaiting the gate, and each
            # carries different keys. Assuming the first shape crashed the command on the other two.
            chose = rec.chose or {}
            if "objective" in chose:
                chosen = f"{chose['objective']}/{chose.get('step', '?')}"
            elif "criterion" in chose:
                chosen = f"{chose.get('request', '?')} criterion {chose['criterion']}"
            elif "request" in chose:
                chosen = str(chose["request"])
            else:
                chosen = "nothing"
            # `looked_at` holds objective ids, and a beat that went to the request backlog looked at none of
            # them; saying "looked at 0 objectives" made a beat that did real work read as a beat that did none.
            drive = getattr(rec, "drive", None)
            surveyed = (
                f"looked at {len(rec.looked_at)} objectives"
                if rec.looked_at else f"followed the {drive} drive" if drive else "surveyed the backlog"
            )
            typer.echo(f"{rec.at} {surveyed}, chose {chosen}: {rec.reason}")
        if not loop:
            return
        _time.sleep(load_config(root).interval_min * 60)


PUBLISH_MSG_OPT = typer.Option(
    "Refresh the recorded snapshot so the published pages show what the engine has done",
    "--message", "-m", help="commit message for the snapshot refresh",
)


@app.command("appetite")
def appetite_cmd(root: Path = ROOT_OPT, as_json: bool = typer.Option(False, "--json")) -> None:
    """What the engine wants right now, and why, from measured drives rather than introspection."""
    from dataclasses import asdict

    from pravrudhi.application import kshudha

    drives = kshudha.measure(root)
    app_state = kshudha.select(drives, state=kshudha.load_state(root))
    if as_json:
        typer.echo(json.dumps(asdict(app_state), default=str, sort_keys=True))
        return
    for d in drives:
        value = "unknown" if d.unknown else f"deficit {d.deficit:.2f}"
        blocked = "" if d.eligible else f"   blocked: {d.blocked_reason}"
        typer.echo(f"{d.wire_name:<20} {value:<16} weight {d.weight:.2f}{blocked}")
    typer.echo("")
    typer.echo(kshudha.sentence(app_state))


@app.command("integrate")
def integrate_cmd(
    root: Path = ROOT_OPT,
    dry_run: bool = typer.Option(False, "--dry-run", help="report what would happen and change nothing"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Merge every agent worktree's accepted work back, three-way per file rather than copy per worktree.

    Copying whole trees in sequence is last-writer wins: when two agents touched one shared file the second copy
    reverted the first, and the loss only showed up when a test failed.
    """
    from pravrudhi.application.integrate import integrate as run_integrate

    worktrees = {
        d.name.removeprefix("agent-"): d
        for d in sorted((root / ".worktrees").glob("agent-*")) if d.is_dir()
    }
    if not worktrees:
        typer.echo("no agent worktrees to integrate")
        return
    result = run_integrate(root, worktrees, dry_run=dry_run)
    if as_json:
        typer.echo(json.dumps(result.to_dict(), sort_keys=True))
    else:
        for c in result.contested:
            typer.echo(f"contested  {c.path}  ({', '.join(c.tasks)})")
        for t in result.applied:
            typer.echo(f"applied    {t}")
        for s_ in result.skipped:
            typer.echo(f"skipped    {s_}")
        for c_ in result.conflicts:
            typer.echo(f"CONFLICT   {c_}", err=True)
    if not result.ok:
        raise typer.Exit(1)


@app.command("publish")
def publish_cmd(
    root: Path = ROOT_OPT,
    message: str = PUBLISH_MSG_OPT,
    no_push: bool = typer.Option(False, "--no-push", help="build and commit, but do not push"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Export the snapshot, build the interface, check the pages carry content, commit and push.

    Refuses at the first step that fails rather than publishing a page that answers 200 and shows an error.
    """
    from pravrudhi.application.publish import publish

    result = publish(root, message=message, do_push=not no_push)
    if as_json:
        typer.echo(json.dumps(result.to_dict(), sort_keys=True))
    else:
        for step in result.steps:
            typer.echo(f"{'ok  ' if step.ok else 'FAIL'} {step.name:<8} {step.detail}")
        typer.echo(result.reason)
    if not result.published:
        raise typer.Exit(1)


@app.command("demo-export")
def demo_export_cmd(
    root: Path = ROOT_OPT,
    dest: Path = DEMO_DEST_OPT,
) -> None:
    """Record this engine's results and capabilities as the snapshot the public site and dashboard render."""
    from pravrudhi.application.demo_export import write_demo

    out = write_demo(root, dest)
    typer.echo(f"wrote {out}")


@app.command("routing")
def routing_cmd(root: Path = ROOT_OPT, as_json: bool = typer.Option(False, "--json")) -> None:
    """Which agent and model each difficulty tier would use now, and why, from measured outcomes."""
    from pravrudhi.application.routing import report

    rows = report(root)
    if as_json:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    for r in rows:
        if "error" in r:
            typer.echo(f"{r['tier']:11s} ! {r['error']}")
            continue
        typer.echo(f"{r['tier']:11s} -> {r['agent']}/{r['model']}  (relative cost {r['relative_cost']:g})")
        typer.echo(f"    {r['reason']}")
        for rec in r["records"]:
            if rec["trials"]:
                typer.echo(f"      {rec['route_id']:12s} {rec['successes']}/{rec['trials']} accepted "
                           f"[{rec['lo']:.2f}, {rec['hi']:.2f}]  mean {rec['mean_wall_s']:.0f}s")


@app.command("tools")
def tools_cmd(category: str | None = typer.Option(None, "--category"), root: Path = ROOT_OPT) -> None:
    """Tools, connectors and plugins this engine can draw on, and which are installed here."""
    from pravrudhi.application.tools import availability

    rows = availability()
    if category:
        rows = [r for r in rows if r["category"] == category]
    for r in rows:
        mark = "available" if r["available"] else "not present"
        typer.echo(f"{r['id']:22s} {r['category']:13s} {mark:12s} {r['title']}")


@app.command("recipes")
def recipes_cmd(
    capability: str | None = typer.Option(None, "--capability"),
    root: Path = ROOT_OPT,
) -> None:
    """Published training and evaluation recipes an objective may draw on, and which are installed here."""
    from pravrudhi.application.recipes import availability

    rows = availability()
    if capability:
        rows = [r for r in rows if r["capability"] == capability]
    for r in rows:
        mark = "available" if r["available"] else "not installed"
        typer.echo(f"{r['id']:22s} {r['capability']:12s} {mark:14s} {r['title']}")
        typer.echo(f"    skill {r['skill']} - {r['source']}")


@app.command("status")
def status_cmd(root: Path = ROOT_OPT) -> None:
    """What the ledger says: chain, candidates, badges, nights, inbox."""
    from pravrudhi.application.status import status

    typer.echo(json.dumps(status(root), indent=2, sort_keys=True))


@app.command("export")
def export_cmd(dest: Path, candidate: str | None = typer.Option(None, "--candidate"), root: Path = ROOT_OPT) -> None:
    """Copy the promoted (green) adapter and its provenance manifest to DEST. Never merges into base weights."""
    from pravrudhi.application.export import export_adapter

    m = export_adapter(root, dest, candidate_id=candidate)
    typer.echo(f"exported {m['candidate_id']} (night {m['night']}, adapter {m['adapter_sha256'][:12]}) to {dest}")


@app.command("serve")
def serve_cmd(
    root: Path = ROOT_OPT, host: str = typer.Option("127.0.0.1", "--host"), port: int = typer.Option(8765, "--port")
) -> None:
    """Serve the ledger, candidates, observations, evidence and inbox over HTTP (sign-off needs an operator header)."""
    from pravrudhi.api.server import serve

    serve(root, host=host, port=port)


@study_app.command("paired-confirm")
def study_paired_confirm(
    night: int = typer.Option(1, "--night"),
    candidate: str | None = typer.Option(None, "--candidate"),
    seed: int = typer.Option(7, "--seed"),
    k: int = typer.Option(100, "--k"),
    root: Path = ROOT_OPT,
) -> None:
    """Incumbent adapter vs baseline on a fresh rotation, per-item paired; Hedges g with BCa CI and a permutation p."""
    from pravrudhi.application.confirm_eval import paired_confirm

    paired_confirm(root, night=night, candidate_id=candidate, seed=seed, k=k, log=typer.echo)


@study_app.command("harness-noise-floor")
def study_harness_nf(
    rotations: int = typer.Option(3, "--rotations"),
    seeds: int = typer.Option(3, "--seeds"),
    k: int = typer.Option(100, "--k"),
    night: int = typer.Option(0, "--night"),
    root: Path = ROOT_OPT,
    config: Path | None = HARNESS_CONFIG_OPT,
) -> None:
    """A/A of the baseline harness on the configured pool; writes the floor that config names."""
    from pravrudhi.application.harness_track import harness_noise_floor

    out = harness_noise_floor(
        root, rotations=rotations, seeds=seeds, k=k, night=night, log=typer.echo, config=config
    )
    typer.echo(json.dumps({k2: out[k2] for k2 in ("n_runs", "mean_plus_pass", "sigma_seed", "sigma_rot")}))


@app.command("harness-night")
def harness_night_cmd(
    night: int = NIGHT_OPT,
    budget: float | None = BUDGET_OPT,
    k: int | None = K_OPT,
    policy: str | None = POLICY_OPT,
    proposer_endpoint: str = PROPOSER_ENDPOINT_OPT,
    root: Path = ROOT_OPT,
    gguf: Path | None = GGUF_OPT,
    seed_recipe: list[Path] = SEED_RECIPE_OPT,
    config: Path | None = HARNESS_CONFIG_OPT,
) -> None:
    """Track H night: fixed model, mutable harness, paired on the configured pool's rotations.

    A code pool's hidden tests run in the sandbox; a pool with no container scorer is scored in process by the
    scorer its manifest declares (`harness_track.kernel_scored`)."""
    from pravrudhi.application.harness_track import load_seed_recipes, run_harness_night
    from pravrudhi.application.spine import resolve_model_snapshot

    if gguf is None:
        import yaml

        cfg = yaml.safe_load((config or root / "research" / "prereg" / "harness_night.yaml").read_text())
        gguf = resolve_model_snapshot("Qwen/Qwen3-30B-A3B-GGUF") / str(cfg["proposer"]["gguf"])
    typer.echo(
        json.dumps(
            run_harness_night(
                root, night=night, k=k, budget_gpu_h=budget, gguf=gguf, log=typer.echo, selection_policy=policy,
                proposer_endpoint=proposer_endpoint,
                seed_recipes=load_seed_recipes(seed_recipe),
                config=config,
            ),
            indent=2,
        )
    )


workflow_app = typer.Typer(help="Declared, repeatable multi-step work: build it once, run it by name.")
app.add_typer(workflow_app, name="workflow")

WORKFLOW_ID_ARG = typer.Argument(..., help="workflow id, e.g. add-a-page")
WORKFLOW_INPUT_OPT = typer.Option([], "--input", help="k=v, repeatable")


def _workflow_or_exit(root: Path, workflow_id: str) -> Any:
    from pravrudhi.application.workflows import get

    wf = get(root, workflow_id)
    if wf is None:
        typer.echo(f"no workflow {workflow_id!r} in {root}", err=True)
        raise typer.Exit(1)
    return wf


def _parse_input_opts(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            typer.echo(f"--input must be k=v, got {pair!r}", err=True)
            raise typer.Exit(2)
        low = value.lower()
        if low in ("true", "false"):
            out[key] = low == "true"
            continue
        try:
            out[key] = int(value)
            continue
        except ValueError:
            pass
        try:
            out[key] = float(value)
            continue
        except ValueError:
            pass
        out[key] = value
    return out


@workflow_app.command("list")
def workflow_list_cmd(root: Path = ROOT_OPT) -> None:
    """Every workflow this workspace knows: packaged examples plus anything under workflows/."""
    from pravrudhi.application.workflows import load

    wfs = load(root)
    if not wfs:
        typer.echo("no workflows found")
    for wf in wfs:
        typer.echo(f"{wf.id:24} {wf.title}")


@workflow_app.command("show")
def workflow_show_cmd(workflow_id: str = WORKFLOW_ID_ARG, root: Path = ROOT_OPT) -> None:
    """A workflow's inputs and steps."""
    wf = _workflow_or_exit(root, workflow_id)
    typer.echo(f"{wf.id}: {wf.title}")
    if wf.description:
        typer.echo(wf.description.strip())
    typer.echo("")
    typer.echo("inputs:")
    for i in wf.inputs:
        detail = "required" if i.required else f"default={i.default!r}"
        typer.echo(f"  {i.name:20} {detail}")
    typer.echo("steps:")
    for s in wf.steps:
        needs = f" needs=[{', '.join(s.needs)}]" if s.needs else ""
        when = f" when={s.when!r}" if s.when else ""
        typer.echo(f"  {s.id:20} [{s.tier}]{needs}{when}")


@workflow_app.command("run")
def workflow_run_cmd(
    workflow_id: str = WORKFLOW_ID_ARG,
    input_opts: list[str] = WORKFLOW_INPUT_OPT,
    root: Path = ROOT_OPT,
) -> None:
    """Run a workflow wave by wave, dispatching each step through the swarm."""
    from pravrudhi.agents.registry import build_agent as make_agent
    from pravrudhi.application import swarm
    from pravrudhi.application.delegate import Verdict
    from pravrudhi.application.workflows import WorkflowError
    from pravrudhi.application.workflows import run as run_workflow

    wf = _workflow_or_exit(root, workflow_id)
    inputs = _parse_input_opts(input_opts)

    def dispatch(tasks: list[swarm.SwarmTask]) -> list[Verdict]:
        return swarm.run_wave(lambda n, m: make_agent(root, n, m), tasks, log=typer.echo, root=root)

    try:
        record = run_workflow(root, wf, inputs, dispatch)
    except WorkflowError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    for s in record.steps:
        typer.echo(f"{s.state.upper():8} {s.id:20} {s.reason}")
    typer.echo(f"run {record.id} recorded")


def main() -> None:
    app()


@app.command("ext-record")
def ext_record_cmd(
    path: Path = EXT_RESULT_ARG,
    tool: str = typer.Option(..., "--tool", help="lm-eval | evalplus | prabhasa"),
    track: str = typer.Option(..., "--track", help="M (model) | H (harness)"),
    condition: str = typer.Option(..., "--condition", help="base | adapter:c-0045 | harness:c-0012"),
    model: str = typer.Option(..., "--model"),
    night: int = typer.Option(0, "--night"),
    dataset: str = typer.Option("", "--dataset", help="EvalPlus dataset: humaneval | mbpp"),
    seed: int | None = typer.Option(None, "--seed"),
    root: Path = ROOT_OPT,
) -> None:
    """Admit an external scorer's result file into the ledger by hash (tier: external)."""
    from pravrudhi.application.external import record_external

    row = record_external(root, path.resolve(), tool=tool, track=track, condition=condition, model=model,
                          night=night, dataset=dataset, seed=seed)
    typer.echo(json.dumps({k: row[k] for k in ("seq", "track", "condition", "metrics", "sha256")}))


@app.command("agents")
def agents_cmd(
    root: Path = ROOT_OPT,
    json_out: bool = typer.Option(False, "--json", help="machine-readable output"),
) -> None:
    """Which coding agents can run right now, and the reason for any that cannot."""
    from pravrudhi.agents.registry import survey

    rows = survey(root)
    if json_out:
        typer.echo(json.dumps([{"name": r.name, "available": r.available, "reason": r.reason} for r in rows], indent=2))
        return
    for r in rows:
        typer.echo(f"{r.name:16} {'ready' if r.available else 'unavailable':12} {r.reason}")


hosts_app = typer.Typer(help="Machines this engine can place work on. A single machine needs no configuration.")
app.add_typer(hosts_app, name="hosts")


@hosts_app.command("list")
def hosts_list_cmd(
    root: Path = ROOT_OPT,
    json_out: bool = typer.Option(False, "--json", help="machine-readable output"),
) -> None:
    """Probe every enrolled machine and report what it can actually do."""
    from pravrudhi.hosts.fleet import fleet_report, render_fleet

    typer.echo(json.dumps(fleet_report(root), indent=2) if json_out else render_fleet(root))


@hosts_app.command("add")
def hosts_add_cmd(
    name: str = typer.Argument(..., help="short name for the machine, e.g. mac-mini"),
    address: str = typer.Option("", "--address", help="hostname or IP for the ssh transport"),
    user: str = typer.Option("", "--user", help="ssh user"),
    transport: str = typer.Option("ssh", "--transport", help="local | ssh | orca"),
    orca_host_id: str = typer.Option("", "--orca-host-id", help="host id when transport is orca"),
    workdir: str = typer.Option("~/pravrudhi", "--workdir"),
    root: Path = ROOT_OPT,
) -> None:
    """Enrol a machine, probe it immediately, and refuse to record one that does not answer."""
    from pravrudhi.hosts.base import HostSpec
    from pravrudhi.hosts.fleet import probe_host, save_host

    spec = HostSpec(name=name, transport=transport, address=address, user=user, workdir=workdir, orca_host_id=orca_host_id)
    cap = probe_host(spec)
    if not cap.reachable:
        typer.echo(f"not enrolled: {name} did not answer the probe ({cap.error})", err=True)
        raise typer.Exit(code=1)
    save_host(root, spec)
    typer.echo(json.dumps({"enrolled": name, "capabilities": cap.to_dict()}, indent=2))


@hosts_app.command("place")
def hosts_place_cmd(
    job: str = PLACE_JOB_ARG,
    min_vram_gb: float = typer.Option(0.0, "--min-vram-gb"),
    needs_agent: str = typer.Option("", "--needs-agent"),
    root: Path = ROOT_OPT,
) -> None:
    """Say which machine would take a job, and why each other machine would not."""
    from pravrudhi.hosts.base import Requirement
    from pravrudhi.hosts.fleet import place, survey_fleet

    presets = {
        "train": Requirement(needs_cuda=True, needs_docker=True, min_vram_gb=max(min_vram_gb, 8.0)),
        "serve": Requirement(needs_accelerator=True, min_vram_gb=min_vram_gb),
        "agent": Requirement(needs_agent=needs_agent or "claude"),
        "any": Requirement(min_vram_gb=min_vram_gb),
    }
    req = presets.get(job)
    if req is None:
        typer.echo(f"unknown job {job!r}; expected one of {', '.join(presets)}", err=True)
        raise typer.Exit(code=2)
    chosen, why = place(survey_fleet(root), req)
    typer.echo(json.dumps({"job": job, "chosen": chosen.name if chosen else None, "rejected": why}, indent=2))


@app.command("doctor")
def doctor_cmd(
    root: Path = ROOT_OPT,
    json_out: bool = typer.Option(False, "--json", help="machine-readable output"),
) -> None:
    """Is this installation ready to run? Reports every check and exits non-zero if any failed."""
    from pravrudhi.application.doctor import run_doctor

    report = run_doctor(root)
    if json_out:
        typer.echo(json.dumps(report, indent=2))
    else:
        for c in report["checks"]:
            typer.echo(f"{'ok ' if c['ok'] else 'FAIL'}  {c['name']:12} {c['detail']}")
    raise typer.Exit(code=0 if report["ok"] else 1)


@app.command("app")
def app_cmd(
    root: Path = ROOT_OPT,
    port: int = typer.Option(8008, "--port"),
    host: str = typer.Option("127.0.0.1", "--host", help="bind address; keep it local unless you know why"),
    no_browser: bool = typer.Option(False, "--no-browser"),
) -> None:
    """The Pravrudhi app: the engine's API and the web interface on one local port."""
    from pravrudhi.application.app_serve import frontend_dir, serve

    if frontend_dir(root) is None:
        typer.echo("no frontend build at app/frontend/out; serving the API only (build with: cd app/frontend && npm run build)")
    serve(root, host=host, port=port, open_browser=not no_browser)


@app.command("requests")
def requests_cmd(root: Path = ROOT_OPT, as_json: bool = REQUESTS_JSON_OPT) -> None:
    """The operator's asks: id, state, criteria met, days waiting, and the first 60 characters of the ask."""
    from pravrudhi.application.requests import backlog

    data = backlog(root)
    if as_json:
        typer.echo(json.dumps(data, indent=2, sort_keys=True))
        return
    if not data["requests"]:
        typer.echo("no requests captured yet")
        return
    typer.echo(f"{'id':10} {'state':12} {'met':6} {'days':>6}  ask")
    for r in data["requests"]:
        met, total = r["progress"]
        ask = r["text"].strip().replace("\n", " ")[:60]
        typer.echo(f"{r['id']:10} {r['state']:12} {f'{met}/{total}':6} {r['staleness_days']:>6}  {ask}")


@app.command("requests-show")
def requests_show_cmd(request_id: str = REQUEST_ID_ARG, root: Path = ROOT_OPT) -> None:
    """The full ask, each criterion with who wrote it and whether it is met, and its evidence."""
    from pravrudhi.application.requests import get

    req = get(root, request_id)
    if req is None:
        typer.echo(f"no request {request_id}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{req.id}  [{req.state}]  asked {req.asked_at}")
    typer.echo(req.text)
    typer.echo("")
    if not req.criteria:
        typer.echo("no acceptance criteria yet")
    for i, c in enumerate(req.criteria):
        typer.echo(f"[{i}] ({c.source}) {'met' if c.met else 'unmet'}  {c.text}")
        for e in c.evidence:
            typer.echo(f"      {e.kind}: {e.ref}" + (f"  {e.note}" if e.note else ""))


@app.command("requests-capture")
def requests_capture_cmd(text: str = REQUEST_TEXT_ARG, root: Path = ROOT_OPT) -> None:
    """Record an ask verbatim. Idempotent: re-running the same text returns the existing request."""
    from pravrudhi.application.requests import capture

    req = capture(root, text)
    typer.echo(f"{req.id}  [{req.state}]  {req.text}")


@app.command("requests-met")
def requests_met_cmd(
    request_id: str = REQUEST_ID_ARG,
    index: int = REQUEST_INDEX_ARG,
    kind: str = REQUEST_KIND_OPT,
    ref: str = REQUEST_REF_OPT,
    note: str = NOTE_OPT,
    root: Path = ROOT_OPT,
) -> None:
    """Mark one acceptance criterion met, with the evidence that makes it so."""
    from pravrudhi.application.requests import Evidence, RequestError, meet

    try:
        req = meet(root, request_id, index, [Evidence(kind=kind, ref=ref, note=note)])
    except RequestError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"{req.id}[{index}] met: {req.criteria[index].text}")


@app.command("requests-advance")
def requests_advance_cmd(
    request_id: str = REQUEST_ID_ARG,
    state: str = REQUEST_STATE_ARG,
    note: str = NOTE_OPT,
    root: Path = ROOT_OPT,
) -> None:
    """Move a request along the state machine, refusing an illegal move, a close without evidence, or — for
    `verified` — a completion gate that has not actually passed: re-verified evidence, an adversarial review with
    no unanswered finding, and a passing end-to-end check."""
    from pravrudhi.application.requests import STATES, RequestError, advance

    if state not in STATES:
        typer.echo(f"unknown state {state!r}; expected one of {', '.join(STATES)}", err=True)
        raise typer.Exit(code=2)
    if state == "verified":
        from pravrudhi.agents.registry import build_agent as make_agent
        from pravrudhi.application.completion import gate

        def dispatch(task: Any) -> str:
            agent = make_agent(root, "claude-code", "sonnet")
            if agent is None:
                return ""
            ws = agent.create_workspace(task.task_id)
            run = agent.run(task.prompt, ws, timeout_s=task.timeout_s)
            agent.stop(ws)
            return str(run.text)

        try:
            result = gate(root, request_id, dispatch=dispatch, e2e="uv run pytest -q")
        except RequestError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1) from e
        if not result.passed:
            typer.echo(f"gate refused: {result.reason}", err=True)
            # The reviewer's own words, not just the label saying it objected. Without these a blocking review
            # is an opaque refusal: the heartbeat re-chooses the same request every beat, the gate says only
            # "the review found a reason", and there is nothing to act on. The engine's loop sat idle for a day
            # on exactly this — a refusal nobody could read.
            if result.review is not None and result.review.blocking and result.review.findings.strip():
                typer.echo("\nwhat the reviewer found:\n", err=True)
                typer.echo(result.review.findings.strip(), err=True)
            raise typer.Exit(code=1)
    try:
        req = advance(root, request_id, state, note=note)
    except RequestError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"{req.id} -> {req.state}")


@app.command("telegram-poll")
def telegram_poll_cmd(root: Path = ROOT_OPT, json_out: bool = REQUESTS_JSON_OPT) -> None:
    """Answer whatever the operator has sent the bot since the last poll.

    Only the configured chat is obeyed and every command is a name from a fixed set, so a message arriving from
    the network cannot ask this engine to do anything it does not already do (application/telegram_inbox.py)."""
    from pravrudhi.application.telegram_inbox import live_poll

    answered = live_poll(root)
    typer.echo(json.dumps({"answered": answered}) if json_out else f"answered {answered}")


@app.command("parity")
def parity_cmd(
    action: str | None = typer.Argument(None, help="gaps: show rival advantages only"),
    root: Path = ROOT_OPT,
    as_json: bool = REQUESTS_JSON_OPT,
) -> None:
    """Inspect the checked-in capability matrix and re-run its evidence."""
    from pravrudhi.application.parity import report

    if action not in (None, "gaps"):
        raise typer.BadParameter("expected gaps or no argument")
    data = report(root)
    if as_json:
        typer.echo(json.dumps(
            [row.model_dump() for row in data.gaps] if action == "gaps" else data.model_dump(), indent=2,
        ))
        return
    typer.echo(f"Verified full coverage: {data.coverage.numerator}/{data.coverage.denominator}")
    rows = data.gaps if action == "gaps" else data.rows
    proofs = {proof.id: proof for proof in data.verification}
    if not rows:
        typer.echo("No verified rival advantages recorded." if action == "gaps" else "No capabilities recorded.")
    for row in rows:
        proof = proofs[row.id]
        state = "verified" if proof.verified else "unverified"
        typer.echo(f"{row.id}: {row.capability} — {row.ours}, {state}")
        for failure in proof.failures:
            typer.echo(f"  failed: {failure}")
    if data.next_gap is not None:
        typer.echo(f"Next: {data.next_gap.id} — {data.next_gap.why_it_matters}")
