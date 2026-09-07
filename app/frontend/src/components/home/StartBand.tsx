"use client";

// Band 4: start something. On a live engine this is the working run form, unchanged from what it has always
// done, plus a link to the guided flow for someone who doesn't know what to type. In demo mode there is no
// engine to run against, so the band explains how to get one instead.

import { useEffect, useState } from "react";
import Link from "next/link";
import { Play } from "lucide-react";
import { ApiError, IS_DEMO, startRun } from "@/lib/api";

const BENCHMARKS = ["gsm8k", "mbppplus"] as const;
const PROPOSERS = ["Qwen3-30B-A3B", "GLM-4.7-Flash"] as const;
const POLICIES = ["efe", "greedy", "thompson", "random"] as const;

type RunOutcome = { kind: "idle" } | { kind: "started"; id: string } | { kind: "unsupported" } | { kind: "error"; message: string };

function selectClass() {
  return "w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]";
}

function labelClass() {
  return "mb-1.5 block text-xs font-medium uppercase tracking-wide text-[var(--color-text-dim)]";
}

function GuidedFlowLink() {
  return (
    <Link
      href="/start"
      className="mt-4 block text-center text-xs text-[var(--color-text-dim)] hover:text-[var(--color-text)]"
    >
      Not sure what to type? Try the guided flow →
    </Link>
  );
}

function DemoStart() {
  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
      <h2 className="text-sm font-medium">Run this on your own model</h2>
      <p className="mt-1.5 max-w-2xl text-sm text-[var(--color-muted)]">
        Install the engine and it opens this same interface on your machine, where the form is live and the runs
        are yours. Your model, your GPU, your results — nothing leaves your computer.
      </p>
      <a
        href="/install"
        className="mt-4 inline-flex items-center gap-2 rounded-md bg-emerald-500/90 px-4 py-2 text-sm font-medium text-black hover:bg-emerald-400"
      >
        Get it running
      </a>
      <GuidedFlowLink />
    </section>
  );
}

function LiveStart() {
  const [target, setTarget] = useState<"model" | "harness">("model");
  const [model, setModel] = useState("");
  const [bench, setBench] = useState<(typeof BENCHMARKS)[number]>(BENCHMARKS[0]);
  const [budget, setBudget] = useState(2);
  const [proposer, setProposer] = useState<(typeof PROPOSERS)[number]>(PROPOSERS[0]);
  const [policy, setPolicy] = useState<(typeof POLICIES)[number]>(POLICIES[0]);
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<RunOutcome>({ kind: "idle" });

  async function handleRun() {
    setRunning(true);
    setOutcome({ kind: "idle" });
    try {
      const handle = await startRun({
        target,
        model,
        bench,
        budget_gpu_h: budget,
        proposer,
        policy,
      });
      setOutcome({ kind: "started", id: handle.id });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setOutcome({ kind: "unsupported" });
      } else {
        setOutcome({ kind: "error", message: err instanceof Error ? err.message : "unknown error" });
      }
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
      <h2 className="mb-4 text-sm font-medium text-[var(--color-text)]">Start something</h2>
      <div className="max-w-md space-y-4">
        <div>
          <span className={labelClass()}>Target</span>
          <div className="flex gap-2">
            {(["model", "harness"] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTarget(t)}
                className={`flex-1 rounded-md border px-3 py-2 text-sm capitalize transition-colors ${
                  target === t
                    ? "border-[var(--color-accent)] bg-[var(--color-accent-dim)]/30 text-[var(--color-text)]"
                    : "border-[var(--color-border)] text-[var(--color-text-dim)] hover:text-[var(--color-text)]"
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        <label className="block">
          <span className={labelClass()}>{target === "model" ? "Model name" : "Harness name"}</span>
          <input
            className={selectClass()}
            placeholder={target === "model" ? "e.g. qwen3-4b" : "e.g. claude-code"}
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </label>

        <label className="block">
          <span className={labelClass()}>Benchmark</span>
          <select className={selectClass()} value={bench} onChange={(e) => setBench(e.target.value as (typeof BENCHMARKS)[number])}>
            {BENCHMARKS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className={labelClass()}>Budget (GPU-hours)</span>
          <input
            type="number"
            min={0}
            step={0.5}
            className={selectClass()}
            value={budget}
            onChange={(e) => setBudget(Number(e.target.value))}
          />
        </label>

        <label className="block">
          <span className={labelClass()}>Proposer model</span>
          <select
            className={selectClass()}
            value={proposer}
            onChange={(e) => setProposer(e.target.value as (typeof PROPOSERS)[number])}
          >
            {PROPOSERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className={labelClass()}>Selection policy</span>
          <select className={selectClass()} value={policy} onChange={(e) => setPolicy(e.target.value as (typeof POLICIES)[number])}>
            {POLICIES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          onClick={handleRun}
          disabled={running || !model}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-[var(--color-accent)] px-4 py-2.5 text-sm font-medium text-[#06110c] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Play size={15} />
          {running ? "Starting…" : "Run"}
        </button>

        {outcome.kind === "started" && <p className="text-xs text-[var(--color-accent)]">Run started: {outcome.id}</p>}
        {outcome.kind === "unsupported" && <p className="text-xs text-[var(--color-warn)]">engine does not support runs yet</p>}
        {outcome.kind === "error" && <p className="text-xs text-[var(--color-danger)]">{outcome.message}</p>}
      </div>
      <GuidedFlowLink />
    </section>
  );
}

export function StartBand() {
  // Decided after mount: the prerendered HTML has no window, so choosing here rather than at module load keeps
  // the server output and the first client render identical.
  const [mode, setMode] = useState<"unknown" | "demo" | "live">("unknown");
  useEffect(() => setMode(IS_DEMO ? "demo" : "live"), []);
  if (mode === "unknown") return <div className="h-48 animate-pulse rounded-lg bg-[var(--color-surface)]" />;
  return mode === "demo" ? <DemoStart /> : <LiveStart />;
}
