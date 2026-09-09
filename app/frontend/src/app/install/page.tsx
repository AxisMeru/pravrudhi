"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";

const QUICKSTART = `git clone https://github.com/AxisMeru/pravrudhi.git
cd pravrudhi
uv sync
uv run pravrudhi init --root .
make exec-image
uv run pravrudhi doctor
uv run pravrudhi app`;

function CodeBlock({ label, code }: { label: string; code: string }) {
  const [result, setResult] = useState<"idle" | "copied" | "error">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setResult("copied");
    } catch {
      setResult("error");
    }
  }

  return (
    <div className="overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-bg)]">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] px-4 py-2">
        <span className="text-xs text-[var(--color-text-dim)]">{label}</span>
        <button
          type="button"
          onClick={copy}
          aria-label={`Copy ${label}`}
          className="flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs text-[var(--color-text-dim)] transition-colors hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
        >
          {result === "copied" ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
          {result === "copied" ? "Copied" : "Copy"}
        </button>
      </div>
      <pre tabIndex={0} className="overflow-x-auto p-4 text-sm leading-7 text-[var(--color-text)] focus-visible:outline-2 focus-visible:outline-[var(--color-accent)]">
        <code>{code}</code>
      </pre>
      <p role="status" className="px-4 text-xs text-[var(--color-text-dim)]">
        {result === "error" && <span className="mb-3 block">Clipboard access is unavailable. Select the code above and copy it manually.</span>}
        {result === "copied" && <span className="sr-only">{label} copied to clipboard.</span>}
      </p>
    </div>
  );
}

export default function InstallPage() {
  return (
    <div>
      <PageHeader title="Get it running" subtitle="Run Pravrudhi on your hardware, then pick a target, a benchmark and a budget." />
      <div className="max-w-4xl space-y-6 p-8">
        <section aria-labelledby="requirements" className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <h2 id="requirements" className="text-sm font-medium text-[var(--color-text)]">What it needs</h2>
          <div className="mt-3 space-y-3 text-sm leading-6 text-[var(--color-text-dim)]">
            <p>For training, use an NVIDIA GPU with at least 8 GB of GPU memory. An Apple Silicon Mac can serve open models. The size of the model you can run depends on the memory available.</p>
            <p>Install Docker and keep it running to build the execution image. Use uv to manage Python and install the project dependencies. You will also need Git and make available in your terminal.</p>
          </div>
        </section>

        <section aria-labelledby="quickstart" className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <h2 id="quickstart" className="text-sm font-medium text-[var(--color-text)]">Quickstart</h2>
          <p className="mb-4 mt-3 text-sm leading-6 text-[var(--color-text-dim)]">Run these commands in order in your terminal. They install the project, prepare your workspace, build the execution image and check your machine before opening the console.</p>
          <CodeBlock label="Terminal · quickstart" code={QUICKSTART} />
          <p className="mt-4 text-sm leading-6 text-[var(--color-text-dim)]">Review the doctor check and resolve any reported setup issues before continuing. The final command opens this interface at <a href="http://localhost:8008" className="text-[var(--color-accent)] hover:underline">localhost:8008</a>. Keep the terminal running while you use it.</p>
        </section>

        <section aria-labelledby="connect" className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <h2 id="connect" className="text-sm font-medium text-[var(--color-text)]">Connect this console to your own engine</h2>
          <p className="mb-4 mt-3 text-sm leading-6 text-[var(--color-text-dim)]">The hosted console can point at an engine on your own machine. Set <code className="text-[var(--color-text)]">NEXT_PUBLIC_API_BASE</code> in the frontend deployment environment to your engine&apos;s browser-accessible URL, then rebuild and redeploy the console. This setting is included at build time.</p>
          <CodeBlock label="Frontend deployment environment" code="NEXT_PUBLIC_API_BASE=https://engine.example.com" />
          <p className="my-4 text-sm leading-6 text-[var(--color-text-dim)]">Replace the example with your engine&apos;s address. For an HTTPS hosted console, use an HTTPS engine URL that your browser can reach.</p>
          <p className="mb-4 text-sm leading-6 text-[var(--color-text-dim)]">The engine only answers the origins its operator names in <code className="text-[var(--color-text)]">PRAVRUDHI_ALLOWED_ORIGINS</code>. On the engine machine, allow your console&apos;s exact origin, including its scheme and port if present, before starting the engine. Replace the example below with your hosted console&apos;s origin.</p>
          <CodeBlock label="Engine terminal · from the project root" code={'PRAVRUDHI_ALLOWED_ORIGINS=https://console.example.com uv run pravrudhi app'} />
          <p className="mt-4 text-sm leading-6 text-[var(--color-text-dim)]">Changing the frontend address does not grant permission on the engine. For local use, open localhost:8008 directly after the quickstart.</p>
        </section>
      </div>
    </div>
  );
}
