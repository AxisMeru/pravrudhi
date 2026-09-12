"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { formatAnnotations, readAnnotations, saveAnnotations, type Annotation } from "@/lib/annotations";

const Context = createContext<{
  notes: Annotation[];
  revision: string;
  update: (file: string, notes: Annotation[]) => void;
} | null>(null);

export function AnnotationProvider({ task, revision, children }: { task: string; revision: string; children: ReactNode }) {
  const [state, setState] = useState<{ notes: Annotation[]; sessionOnly: boolean } | null>(null);
  useEffect(() => {
    // readAnnotations reads localStorage, unavailable during SSR — read after mount (and again if the task
    // changes), same reasoning as every other browser-only read in this codebase (window, navigator).
    // eslint-disable-next-line react-hooks/set-state-in-effect -- localStorage is unavailable during SSR
    setState(readAnnotations(task));
  }, [task]);
  function update(file: string, notes: Annotation[]) {
    const persisted = saveAnnotations(task, file, notes);
    setState(previous => ({
      notes: [...(previous?.notes ?? []).filter(n => n.file !== file), ...notes],
      sessionOnly: !persisted || !!previous?.sessionOnly,
    }));
  }
  return <Context.Provider value={state ? { notes: state.notes, revision, update } : null}>
    <section className="mb-3 space-y-2 rounded border border-[var(--color-border)] p-3 text-sm">
      <h2>Review notes ({state?.notes.length ?? 0})</h2>
      <p className="text-xs text-[var(--color-text-dim)]">Personal notes stored in this browser. Clearing site data removes saved notes.</p>
      {state?.sessionOnly && <p role="status">Browser storage is unavailable. Notes remain available for this session; copy them before leaving.</p>}
      {!state && <p role="status">Loading notes…</p>}
      {state && state.notes.length === 0 && <p>Expand a file and choose Add note beside a line.</p>}
      {!!state?.notes.length && <details>
        <summary>Read or copy all task notes</summary>
        <p className="my-2 text-xs">Includes notes from earlier diff revisions and files no longer shown. Select the text to copy and hand off.</p>
        <textarea aria-label="All task notes" readOnly value={formatAnnotations(task, state.notes)} rows={10} className="w-full border p-2 font-mono text-xs" />
        {state.notes.map(note => <Note key={note.id} note={note} label={`${note.file}:${note.line} (${note.side}${note.revision !== revision ? ", earlier revision" : ""})`} />)}
      </details>}
    </section>
    {children}
  </Context.Provider>;
}

function Editor({ initial = "", onSave, onCancel }: { initial?: string; onSave: (text: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(initial);
  return <form className="space-y-1" onSubmit={event => { event.preventDefault(); if (text.trim()) onSave(text.trim()); }}>
    <textarea autoFocus aria-label="Note text" value={text} onChange={event => setText(event.target.value)} rows={3} className="w-full border p-2" />
    <button type="submit" disabled={!text.trim()} className="mr-3 disabled:opacity-40">Save note</button>
    <button type="button" onClick={onCancel}>Cancel</button>
  </form>;
}
function Note({ note, label }: { note: Annotation; label?: string }) {
  const context = useContext(Context)!;
  const [editing, setEditing] = useState(false);
  const fileNotes = context.notes.filter(n => n.file === note.file);
  return <div className="my-2 border-l-2 border-[var(--color-border)] pl-2">
    {label && <p className="break-all font-mono text-xs">{label}</p>}
    {editing ? <Editor initial={note.text} onCancel={() => setEditing(false)} onSave={text => {
      context.update(note.file, fileNotes.map(n => n.id === note.id ? { ...n, text } : n)); setEditing(false);
    }} /> : <>
      <p className="whitespace-pre-wrap break-words">{note.text}</p>
      <button type="button" className="mr-3" onClick={() => setEditing(true)}>Edit note</button>
      <button type="button" onClick={() => context.update(note.file, fileNotes.filter(n => n.id !== note.id))}>Remove note</button>
    </>}
  </div>;
}
export function LineNotes({ file, side, line }: { file: string; side: "old" | "new"; line: number }) {
  const context = useContext(Context);
  const [adding, setAdding] = useState(false);
  if (!context) return null;
  const notes = context.notes.filter(n => n.file === file && n.side === side && n.line === line && n.revision === context.revision);
  return <aside aria-label={`Notes for ${file} ${side} line ${line}`} className="w-64 shrink-0 whitespace-normal px-2 font-sans text-xs">
    {notes.map(note => <Note key={note.id} note={note} />)}
    {adding ? <Editor onCancel={() => setAdding(false)} onSave={text => {
      context.update(file, [...context.notes.filter(n => n.file === file), {
        id: crypto.randomUUID(), file, side, line, revision: context.revision, text,
      }]); setAdding(false);
    }} /> : <button type="button" onClick={() => setAdding(true)} aria-label={`Add note to ${file} ${side} line ${line}`}>Add note</button>}
  </aside>;
}
