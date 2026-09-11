"use client";

// prabhasa-nyaya in the product: a question of Indian law, answered from statute sources by every vendor the
// user picks, side by side, with every citation checked against the corpus. The check is mechanical and the
// page says what a verdict means: the cited section exists and was among the sources shown, or it was not.
// Nothing on this page is ledger evidence, and a handful of asks is an anecdote, not a comparison.

import { useCallback, useEffect, useState } from "react";
import { Scale, ShieldCheck, ShieldAlert, ShieldQuestion, Ban, Loader2 } from "lucide-react";
import {
  nyayaAsk,
  nyayaAsks,
  nyayaAudit,
  nyayaCorpus,
  nyayaVendors,
  ApiError,
  IS_DEMO,
  type NyayaAnswer,
  type NyayaAsk,
  type NyayaAudit,
  type NyayaCorpusHit,
  type NyayaVendor,
} from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";

const VERDICT: Record<NyayaAnswer["verdict"], { label: string; tone: string; Icon: typeof ShieldCheck; meaning: string }> = {
  licensed: {
    label: "licensed",
    tone: "text-emerald-400 border-emerald-500/40 bg-emerald-500/10",
    Icon: ShieldCheck,
    meaning: "every section it cites exists in the corpus and was among the sources it was shown",
  },
  unlicensed: {
    label: "unlicensed",
    tone: "text-amber-400 border-amber-500/40 bg-amber-500/10",
    Icon: ShieldQuestion,
    meaning: "it relies on nothing it was shown: no citation, or only sections outside its sources",
  },
  invented_citation: {
    label: "invented citation",
    tone: "text-red-400 border-red-500/40 bg-red-500/10",
    Icon: ShieldAlert,
    meaning: "it cites a section that does not exist in the corpus at all",
  },
  abstained: {
    label: "abstained",
    tone: "text-sky-400 border-sky-500/40 bg-sky-500/10",
    Icon: Ban,
    meaning: "it said the sources do not cover the question rather than inventing an answer",
  },
  error: {
    label: "no answer",
    tone: "text-[var(--color-text-dim)] border-[var(--color-border)]",
    Icon: Ban,
    meaning: "the vendor could not be reached or refused",
  },
};

function VerdictBadge({ verdict }: { verdict: NyayaAnswer["verdict"] }) {
  const v = VERDICT[verdict];
  return (
    <span title={v.meaning} className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${v.tone}`}>
      <v.Icon size={13} /> {v.label}
    </span>
  );
}

function CitationChip({ id, status }: { id: string; status: string }) {
  const tone =
    status === "licensed" ? "border-emerald-500/40 text-emerald-300" : status === "unshown" ? "border-amber-500/40 text-amber-300" : "border-red-500/40 text-red-300";
  return (
    <span className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${tone}`} title={status}>
      {id} · {status}
    </span>
  );
}

function AnswerCard({ a }: { a: NyayaAnswer }) {
  return (
    <article className="flex flex-col gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-medium text-[var(--color-text)]">
          {a.vendor} <span className="text-[var(--color-text-dim)]">· {a.model}</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-dim)]">
          {a.confidence !== "unstated" && <span>confidence {a.confidence}</span>}
          <span>{a.wall_s}s</span>
          <VerdictBadge verdict={a.verdict} />
        </div>
      </header>
      {a.error ? (
        <p className="text-sm text-[var(--color-text-dim)]">{a.error}</p>
      ) : (
        <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-[var(--color-text)]">{a.text}</pre>
      )}
      {a.citations.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {a.citations.map((c) => (
            <CitationChip key={c.id} id={c.id} status={c.status} />
          ))}
        </div>
      )}
      {a.audit && (
        <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3 text-xs">
          <div className="font-medium text-[var(--color-text)]">
            Audit by {a.audit.checker}: {a.audit.verdict}
            {a.audit.class && a.audit.class !== "none" ? ` · ${a.audit.class}` : ""}
          </div>
          {a.audit.span && a.audit.span !== "NONE" && (
            <div className="mt-1 text-[var(--color-text-dim)]">
              span: <span className="text-[var(--color-text)]">“{a.audit.span}”</span>
            </div>
          )}
          {a.audit.why && <div className="mt-1 text-[var(--color-text-dim)]">{a.audit.why}</div>}
          <div className="mt-1 text-[var(--color-text-dim)]">
            A second model’s opinion in a fixed shape; on the sibling benchmark the best vendor’s false-positive rate was measured in the tens of items, pipeline tier.
          </div>
        </div>
      )}
    </article>
  );
}

function Sources({ hits }: { hits: NyayaAsk["sources"] }) {
  if (hits.length === 0) return <p className="text-sm text-[var(--color-text-dim)]">No source matched the question; an answer here can only abstain.</p>;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {hits.map((s) => (
        <li key={s.id} className="rounded border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-text-dim)]" title={s.act}>
          <span className="font-mono text-[var(--color-text)]">{s.id}</span> — {s.title}
        </li>
      ))}
    </ul>
  );
}

function AskTab() {
  const [vendors, setVendors] = useState<NyayaVendor[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [checker, setChecker] = useState<string>("");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<NyayaAsk | null>(null);
  const [history, setHistory] = useState<NyayaAsk[]>([]);

  const load = useCallback(async () => {
    try {
      const vs = await nyayaVendors();
      setVendors(vs);
      setChosen((prev) => (prev.length ? prev : vs.filter((v) => v.available).slice(0, 2).map((v) => v.id)));
    } catch {
      setVendors([]);
    }
    try {
      setHistory(await nyayaAsks());
    } catch {
      setHistory([]);
    }
  }, []);
  useEffect(() => {
    // Deferred a tick so the effect itself sets no state (react-hooks/set-state-in-effect).
    const t = setTimeout(() => void load(), 0);
    return () => clearTimeout(t);
  }, [load]);

  const submit = async () => {
    if (!question.trim() || chosen.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const rec = await nyayaAsk(question, chosen, checker || null);
      setResult(rec);
      setHistory((h) => [rec, ...h].slice(0, 20));
    } catch (e) {
      setError(e instanceof ApiError ? `the engine answered HTTP ${e.status}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_280px]">
      <div className="space-y-4">
        <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <textarea
            className="min-h-24 w-full resize-y rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]"
            placeholder="e.g. A man strikes another with a stick intending to hurt him; the victim dies of a head wound. Which sections of the IPC apply, and what is the punishment?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {vendors.map((v) => (
              <label
                key={v.id}
                title={v.why || v.note}
                className={`flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-xs ${
                  v.available ? "border-[var(--color-border)] text-[var(--color-text)]" : "border-dashed border-[var(--color-border)] text-[var(--color-text-dim)]"
                }`}
              >
                <input
                  type="checkbox"
                  disabled={!v.available}
                  checked={chosen.includes(v.id)}
                  onChange={(e) => setChosen((c) => (e.target.checked ? [...c, v.id] : c.filter((x) => x !== v.id)))}
                />
                {v.id}
                {!v.available && <span className="opacity-70">· {v.why}</span>}
              </label>
            ))}
            <select
              className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-xs text-[var(--color-text)]"
              value={checker}
              onChange={(e) => setChecker(e.target.value)}
              title="A second vendor that audits each answer for a reasoning error, in the A1.1 shape"
            >
              <option value="">no audit</option>
              {vendors
                .filter((v) => v.available)
                .map((v) => (
                  <option key={v.id} value={v.id}>
                    audit with {v.id}
                  </option>
                ))}
            </select>
            <button
              onClick={submit}
              disabled={busy || !question.trim() || chosen.length === 0}
              className="ml-auto flex items-center gap-2 rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[#06110c] hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? <Loader2 size={15} className="animate-spin" /> : <Scale size={15} />}
              {busy ? "Asking…" : "Ask"}
            </button>
          </div>
          {error && <p className="mt-2 text-xs text-[var(--color-danger)]">{error}</p>}
        </section>

        {result && (
          <section className="space-y-3">
            <div>
              <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-dim)]">Sources shown to every vendor</h2>
              <div className="mt-2">
                <Sources hits={result.sources} />
              </div>
            </div>
            <div className={`grid grid-cols-1 gap-3 ${result.answers.length > 1 ? "xl:grid-cols-2" : ""}`}>
              {result.answers.map((a) => (
                <AnswerCard key={a.vendor} a={a} />
              ))}
            </div>
            <p className="text-xs text-[var(--color-text-dim)]">
              Recorded as <span className="font-mono">{result.id}</span> with provenance <span className="font-mono">{result.provenance}</span>. {result.note}
            </p>
          </section>
        )}
      </div>

      <aside className="space-y-3">
        <h2 className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-dim)]">Recent asks</h2>
        {history.length === 0 && <p className="text-sm text-[var(--color-text-dim)]">Nothing asked yet on this install.</p>}
        {history.map((h) => (
          <button
            key={h.id}
            onClick={() => setResult(h)}
            className="block w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-left text-xs hover:border-[var(--color-accent)]"
          >
            <div className="line-clamp-2 text-[var(--color-text)]">{h.question}</div>
            <div className="mt-1 flex flex-wrap gap-1">
              {h.answers.map((a) => (
                <span key={a.vendor} className="text-[var(--color-text-dim)]">
                  {a.vendor}: {VERDICT[a.verdict].label}
                </span>
              ))}
            </div>
          </button>
        ))}
      </aside>
    </div>
  );
}

function AuditTab() {
  const [vendors, setVendors] = useState<NyayaVendor[]>([]);
  const [checker, setChecker] = useState("");
  const [sources, setSources] = useState("");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<(NyayaAudit & { raw?: string }) | null>(null);

  useEffect(() => {
    nyayaVendors()
      .then((vs) => {
        setVendors(vs);
        setChecker((c) => c || vs.find((v) => v.available)?.id || "");
      })
      .catch(() => setVendors([]));
  }, []);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await nyayaAudit(sources, answer, checker));
    } catch (e) {
      setError(e instanceof ApiError ? `the engine answered HTTP ${e.status}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <section className="grid grid-cols-1 gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 md:grid-cols-2">
        <label className="text-xs text-[var(--color-text-dim)]">
          Sources the answer was allowed to rely on
          <textarea
            className="mt-1 min-h-40 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)]"
            value={sources}
            onChange={(e) => setSources(e.target.value)}
            placeholder="- IPC s.300: ... &#10;- Case name, citation: holding ..."
          />
        </label>
        <label className="text-xs text-[var(--color-text-dim)]">
          The answer to audit
          <textarea
            className="mt-1 min-h-40 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)]"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="Paste a legal answer, a draft opinion, or a model's reply."
          />
        </label>
        <div className="flex items-center gap-2 md:col-span-2">
          <select
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-xs text-[var(--color-text)]"
            value={checker}
            onChange={(e) => setChecker(e.target.value)}
          >
            {vendors
              .filter((v) => v.available)
              .map((v) => (
                <option key={v.id} value={v.id}>
                  {v.id}
                </option>
              ))}
          </select>
          <button
            onClick={submit}
            disabled={busy || !answer.trim() || !checker}
            className="ml-auto flex items-center gap-2 rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[#06110c] hover:opacity-90 disabled:opacity-40"
          >
            {busy ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />}
            {busy ? "Auditing…" : "Audit"}
          </button>
        </div>
        {error && <p className="text-xs text-[var(--color-danger)] md:col-span-2">{error}</p>}
      </section>
      {result && (
        <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm">
          <div className="font-medium text-[var(--color-text)]">
            {result.checker}: {result.verdict}
            {result.class && result.class !== "none" ? ` · ${result.class}` : ""}
          </div>
          {result.span && result.span !== "NONE" && <div className="mt-2 text-[var(--color-text-dim)]">span: “{result.span}”</div>}
          {result.why && <div className="mt-1 text-[var(--color-text-dim)]">{result.why}</div>}
        </section>
      )}
    </div>
  );
}

function CorpusTab() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<NyayaCorpusHit[]>([]);
  const [count, setCount] = useState<number | null>(null);
  const search = async (text: string) => {
    setQ(text);
    try {
      const r = await nyayaCorpus(text);
      setCount(r.documents);
      setHits(r.hits);
    } catch {
      setHits([]);
    }
  };
  useEffect(() => {
    const t = setTimeout(() => void search(""), 0);
    return () => clearTimeout(t);
  }, []);
  return (
    <div className="space-y-3">
      <input
        className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)]"
        placeholder="Search the statute corpus (e.g. culpable homicide, dowry death, section 420)"
        value={q}
        onChange={(e) => void search(e.target.value)}
      />
      <p className="text-xs text-[var(--color-text-dim)]">
        {count === null ? "" : `${count} sections in the corpus, each with its source recorded. Add your own under research/nyaya/corpus/.`}
      </p>
      <ul className="space-y-2">
        {hits.map((h) => (
          <li key={h.id} className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm">
            <div className="font-mono text-xs text-[var(--color-text-dim)]">
              {h.id} · {h.act} · score {h.score}
            </div>
            <div className="mt-1 font-medium text-[var(--color-text)]">{h.title}</div>
            <div className="mt-1 text-[var(--color-text-dim)]">{h.text}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DemoNyaya() {
  return (
    <div className="space-y-6">
      <PageHeader title="Nyaya" subtitle="A question of Indian law, answered from statute sources and checked citation by citation." />
      <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
        <h2 className="text-sm font-medium text-[var(--color-text)]">Run the engine locally to ask.</h2>
        <p className="mt-1.5 max-w-2xl text-sm text-[var(--color-text-dim)]">
          This is a recording of the public site, with no engine behind it and no vendor to answer. Installed, this
          page asks the models you can reach and checks every citation they make against the statute corpus.
        </p>
        <a href="/install" className="mt-4 inline-flex items-center gap-2 rounded-md bg-emerald-500/90 px-4 py-2 text-sm font-medium text-black hover:bg-emerald-400">
          Get it running
        </a>
      </section>
    </div>
  );
}

export default function NyayaPage() {
  const [mode, setMode] = useState<"unknown" | "demo" | "live">("unknown");
  const [tab, setTab] = useState<"ask" | "audit" | "corpus">("ask");
  useEffect(() => {
    // Decided after mount, one frame later, so the prerendered HTML and the first client render agree.
    const f = requestAnimationFrame(() => setMode(IS_DEMO ? "demo" : "live"));
    return () => cancelAnimationFrame(f);
  }, []);
  if (mode === "unknown") return <div className="h-48 animate-pulse rounded-lg bg-[var(--color-surface)]" />;
  if (mode === "demo") return <DemoNyaya />;
  return (
    <div>
      <PageHeader
        title="Nyaya"
        subtitle="A question of Indian law, answered from statute sources by the models you pick, side by side. Every citation is checked against the corpus; an answer that says it does not know is recorded as such."
      />
      <div className="p-8">
        <div className="mb-5 flex gap-1 border-b border-[var(--color-border)]">
          {(
            [
              ["ask", "Ask"],
              ["audit", "Audit an answer"],
              ["corpus", "Corpus"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === id ? "border-[var(--color-accent)] text-[var(--color-text)]" : "border-transparent text-[var(--color-text-dim)] hover:text-[var(--color-text)]"}`}
            >
              {label}
            </button>
          ))}
        </div>
        {tab === "ask" && <AskTab />}
        {tab === "audit" && <AuditTab />}
        {tab === "corpus" && <CorpusTab />}
      </div>
    </div>
  );
}
