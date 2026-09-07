"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { ComposeNote } from "@/components/memory/ComposeNote";
import { NotesList } from "@/components/memory/NotesList";
import { SearchBox } from "@/components/memory/SearchBox";
import { forget, memory, recall, revise, type MemoryNote, type MemorySnapshot } from "@/lib/memory";

export default function MemoryPage() {
  const [data, setData] = useState<MemorySnapshot | undefined>(undefined);
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    memory()
      .then((snapshot) => {
        if (!cancelled) setData(snapshot);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const addNote = (note: MemoryNote) => {
    setData((prev) => (prev ? { ...prev, notes: [note, ...prev.notes] } : prev));
  };

  // A revision keeps the note's id, so the list is patched in place: the note stays where the reader left it
  // rather than jumping to the top as a new one would. Errors are raised for `NotesList` to show against the
  // note they belong to, so the local state is only touched once the engine has accepted the change.
  const saveNote = async (note: MemoryNote, text: string) => {
    const revised = await revise(note.id, text, "user");
    setData((prev) => (prev ? { ...prev, notes: prev.notes.map((n) => (n.id === note.id ? revised : n)) } : prev));
  };

  const deleteNote = async (note: MemoryNote) => {
    await forget(note.id);
    setData((prev) => (prev ? { ...prev, notes: prev.notes.filter((n) => n.id !== note.id) } : prev));
  };

  const notes = data ? recall(data.notes, query) : [];

  return (
    <div>
      <PageHeader
        title="Memory"
        subtitle="What the user asked to be remembered, kept apart from the ledger's evidence."
      />
      <div className="space-y-8 p-8">
        {failed && <p className="text-sm text-[var(--color-text-dim)]">Could not reach the engine&apos;s memory API.</p>}
        {!failed && data === undefined && <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>}

        {!failed && data && (
          <>
            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Remember</h2>
              <ComposeNote onRemembered={addNote} />
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Notes</h2>
              <div className="mb-3">
                <SearchBox value={query} onChange={setQuery} />
              </div>
              <NotesList notes={notes} query={query} onSave={saveNote} onDelete={deleteNote} />
            </section>

            {data.preferences.length > 0 && (
              <section>
                <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Preferences</h2>
                <div className="space-y-2">
                  {data.preferences.map((pref) => (
                    <div
                      key={pref.key}
                      className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm"
                    >
                      <span className="font-mono text-[13px] text-[var(--color-text)]">{pref.key}</span>
                      <span className="text-[var(--color-text-dim)]">{JSON.stringify(pref.value)}</span>
                      <span className="ml-auto text-[11px] text-[var(--color-text-dim)]">
                        {pref.source} · {pref.set_at}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {data.threads.length > 0 && (
              <section>
                <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Chat threads</h2>
                <p className="text-[11px] leading-4 text-[var(--color-text-dim)]">
                  {data.threads.length} thread{data.threads.length === 1 ? "" : "s"} stored: {data.threads.join(", ")}
                </p>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
