"use client";

import { useEffect, useState, type FormEvent } from "react";
import RunTerminal from "../../components/terminal/RunTerminal";

export default function TerminalPage() {
  const [runId, setRunId] = useState("");
  const [draft, setDraft] = useState("");
  useEffect(() => {
    // The prerendered HTML has no window and no query string, so the real run_id is read after mount, same
    // reasoning as the tour page's ?step=N.
    const initial = new URLSearchParams(window.location.search).get("run_id")?.trim() ?? "";
    // eslint-disable-next-line react-hooks/set-state-in-effect -- window.location is unavailable during SSR
    setRunId(initial);
    setDraft(initial);
  }, []);

  function watch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = draft.trim();
    if (!id) return;
    setRunId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("run_id", id);
    window.history.replaceState(null, "", url);
  }

  return (
    <main className="mx-auto w-full max-w-6xl space-y-6 p-6">
      <h1 className="text-2xl font-semibold">Terminal</h1>
      <p>Watch a run’s output here. Read-only; commands cannot be entered.</p>
      <form onSubmit={watch} className="flex flex-wrap items-center gap-3">
        <label htmlFor="terminal-run">Run ID</label>
        <input id="terminal-run" value={draft} onChange={(event) => setDraft(event.target.value)} required
          className="rounded border bg-transparent px-3 py-2" placeholder="Enter a run ID" />
        <button type="submit" disabled={!draft.trim()} className="rounded border px-3 py-2 disabled:opacity-50">Watch run</button>
      </form>
      {runId ? <RunTerminal key={runId} runId={runId} /> : <p>Enter a run ID to open its output.</p>}
    </main>
  );
}
