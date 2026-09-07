"use client";

import { useState } from "react";
import type { MemoryNote } from "@/lib/memory";
import { IS_DEMO } from "@/lib/api";
import { MarkdownEditor, MarkdownPreview } from "./MarkdownEditor";

export function NotesList({ notes, query, onSave, onDelete }: {
  notes: MemoryNote[]; query: string;
  onSave?: (note: MemoryNote, text: string) => Promise<void>;
  onDelete?: (note: MemoryNote) => Promise<void>;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  // Drafts belong to the list, not the selected editor: switching notes never discards text.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const act = async (note: MemoryNote, deleting: boolean) => {
    setBusy(note.id);
    setErrors((prev) => ({ ...prev, [note.id]: "" }));
    try {
      if (deleting) await onDelete!(note);
      else await onSave!(note, drafts[note.id] ?? note.text);
      setDrafts((prev) => { const next = { ...prev }; delete next[note.id]; return next; });
      setConfirmDelete(null);
      if (deleting) setSelected((prev) => prev === note.id ? null : prev);
    } catch (error) {
      setErrors((prev) => ({ ...prev, [note.id]: error instanceof Error ? error.message : String(error) }));
    } finally { setBusy(null); }
  };
  if (!notes.length) return <p className="text-sm text-[var(--color-text-dim)]">{query.trim() ? "No stored note matches that search." : "No notes remembered yet."}</p>;
  return <div className="space-y-3">{notes.map((note) => {
    const text = drafts[note.id] ?? note.text;
    const dirty = text !== note.text;
    return <section key={note.id} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <button type="button" aria-expanded={selected === note.id} onClick={() => { setSelected(selected === note.id ? null : note.id); setConfirmDelete(null); }} className="mb-2 w-full text-left text-sm font-medium">
        {note.text.split("\n")[0].slice(0, 100) || "Untitled note"} {dirty && <span className="text-[var(--color-accent)]">· Unsaved changes</span>}
        <span className="block text-xs text-[var(--color-text-dim)]">{note.source} · {note.created}{note.revised && ` · edited ${note.revised}`}</span>
      </button>
      {selected !== note.id ? <MarkdownPreview text={text} /> : <>
        <MarkdownEditor text={text} onChange={(value) => setDrafts((prev) => ({ ...prev, [note.id]: value }))} disabled={IS_DEMO || busy === note.id} />
        <div className="mt-3 flex gap-4">
          <button type="button" disabled={IS_DEMO || !!busy || !dirty || !text.trim() || !onSave} onClick={() => act(note, false)} className="rounded bg-[var(--color-accent)] px-3 py-2 text-[var(--color-bg)] disabled:opacity-50">{busy === note.id ? "Working…" : "Save changes"}</button>
          <button type="button" disabled={IS_DEMO || !!busy || !onDelete} onClick={() => setConfirmDelete(note.id)} className="text-[var(--color-danger)] disabled:opacity-50">Delete note</button>
        </div>
        {confirmDelete === note.id && <div className="mt-3" role="alert"><p>Delete this note permanently{dirty ? " and discard its unsaved changes" : ""}?</p><button disabled={!!busy} onClick={() => act(note, true)} className="mr-4 text-[var(--color-danger)]">Confirm delete</button><button disabled={!!busy} onClick={() => setConfirmDelete(null)}>Cancel</button></div>}
      </>}
      {errors[note.id] && <p role="alert" className="mt-2 text-sm text-[var(--color-danger)]">{errors[note.id]}</p>}
    </section>;
  })}</div>;
}
