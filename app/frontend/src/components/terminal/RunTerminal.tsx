"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { formatRunEvent, terminalStream } from "../../lib/terminal";

/**
 * Read-only run output. Deliberately accepts no shell input: a shell in a web
 * page is a remote execution surface, which this engine's threat model excludes.
 */
export default function RunTerminal({ runId }: { runId: string }) {
  const [output, setOutput] = useState("");
  const [status, setStatus] = useState("Connecting…");
  const [error, setError] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const [following, setFollowing] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const viewport = useRef<HTMLPreElement>(null);
  const follow = useRef(true);

  useEffect(() => {
    const controller = new AbortController();
    let pending = "";
    let frame: number | undefined;
    const flush = () => {
      frame = undefined;
      if (pending && !controller.signal.aborted) {
        const text = pending;
        pending = "";
        setOutput((previous) => previous + text);
      }
    };
    setOutput("");
    setError("");
    setCopyStatus("");
    setStatus("Connecting…");
    follow.current = true;
    setFollowing(true);
    void (async () => {
      let ended = false;
      try {
        for await (const event of terminalStream(runId, controller.signal)) {
          if (controller.signal.aborted) return;
          pending += formatRunEvent(event) + "\n";
          if (frame === undefined) frame = requestAnimationFrame(flush);
          ended = event.type === "end";
          setStatus(ended ? `Run ${event.status ?? "ended"}${event.exit_code == null ? "" : ` (exit ${event.exit_code})`}` : "Receiving output");
        }
        if (!controller.signal.aborted && !ended) {
          setStatus("Disconnected");
          setError("The stream closed before a run-end event. Reload output to reconnect.");
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          setStatus("Connection failed");
          setError(cause instanceof Error ? cause.message : "Unable to read run output.");
        }
      } finally {
        if (frame !== undefined) cancelAnimationFrame(frame);
        flush();
      }
    })();
    return () => {
      controller.abort();
      if (frame !== undefined) cancelAnimationFrame(frame);
    };
  }, [runId, attempt]);

  useLayoutEffect(() => {
    if (follow.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [output]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(output);
      setCopyStatus("Copied");
    } catch {
      setCopyStatus("Copy unavailable. Select output and use your browser’s Copy command.");
    }
  }

  return (
    <section className="space-y-3" aria-label={`Terminal for run ${runId}`}>
      <div className="flex flex-wrap items-center gap-3">
        <span role="status">{status}</span>
        <button className="rounded border px-3 py-1 disabled:opacity-50" onClick={copy} disabled={!output}>Copy output</button>
        <button className="rounded border px-3 py-1" onClick={() => {
          follow.current = true;
          setFollowing(true);
          if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
        }}>{following ? "Following tail" : "Jump to latest"}</button>
        {error && <button className="rounded border px-3 py-1" onClick={() => setAttempt((value) => value + 1)}>Reload output</button>}
      </div>
      {error && <p role="alert">{error} Reloading replaces scrollback with the server’s retained output.</p>}
      <p role="status" className="text-sm">{copyStatus}</p>
      <pre ref={viewport} tabIndex={0} aria-label="Run output (read-only)"
        className="h-[65vh] min-h-64 overflow-auto rounded border bg-zinc-950 p-4 font-mono text-sm text-zinc-100"
        style={{ whiteSpace: "pre", userSelect: "text", overflowAnchor: "none" }}
        onScroll={() => {
          const element = viewport.current;
          if (!element) return;
          follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
          setFollowing(follow.current);
        }}>{output || (error ? "No output received." : "Waiting for output…")}</pre>
    </section>
  );
}
