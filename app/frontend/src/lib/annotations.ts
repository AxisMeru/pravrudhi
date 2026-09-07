import type { DiffHunk } from "./diffs";

// Deliberately browser-only: an annotation is one reviewer's working note. The engine has no
// annotation route; inventing a server-side store would be a larger product decision.
export interface Annotation {
  id: string;
  file: string;
  revision: string;
  side: "old" | "new";
  line: number;
  text: string;
}
const prefix = "pravrudhi:annotations:v1:";
const session = new Map<string, Annotation[]>();
const unavailable = new Set<string>();
const keyFor = (task: string, file: string) => prefix + JSON.stringify([task, file]);

function valid(value: unknown): value is Annotation {
  if (!value || typeof value !== "object") return false;
  const n = value as Annotation;
  return typeof n.id === "string" && typeof n.file === "string" && typeof n.revision === "string"
    && (n.side === "old" || n.side === "new") && Number.isInteger(n.line) && n.line > 0
    && typeof n.text === "string" && !!n.text.trim();
}
export function readAnnotations(task: string): { notes: Annotation[]; sessionOnly: boolean } {
  let sessionOnly = false;
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key?.startsWith(prefix) || session.has(key)) continue;
      const [storedTask, file] = JSON.parse(key.slice(prefix.length));
      if (storedTask !== task) continue;
      try {
        const value: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
        if (!Array.isArray(value) || !value.every(valid)) throw new Error("Invalid notes");
        session.set(key, value.filter(n => n.file === file));
      } catch { unavailable.add(key); sessionOnly = true; }
    }
  } catch { sessionOnly = true; }
  const notes: Annotation[] = [];
  for (const [key, values] of session) {
    if (JSON.parse(key.slice(prefix.length))[0] === task) {
      notes.push(...values);
      if (unavailable.has(key)) sessionOnly = true;
    }
  }
  return { notes, sessionOnly };
}
export function saveAnnotations(task: string, file: string, notes: Annotation[]): boolean {
  const key = keyFor(task, file);
  session.set(key, notes);
  try {
    localStorage.setItem(key, JSON.stringify(notes));
    unavailable.delete(key);
    return true;
  } catch { unavailable.add(key); return false; }
}

export function lineAddresses(hunk: DiffHunk): ({ side: "old" | "new"; line: number } | null)[] {
  const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(hunk.header);
  if (!match) return hunk.lines.map(() => null);
  let oldLine = Number(match[1]);
  let newLine = Number(match[2]);
  return hunk.lines.map(line => {
    if (line.text.startsWith("\\ No newline at end of file")) return null;
    if (line.kind === "del") return { side: "old", line: oldLine++ };
    if (line.kind === "context") oldLine++;
    return { side: "new", line: newLine++ };
  });
}

export function formatAnnotations(task: string, notes: Annotation[]): string {
  return [`Review notes for task ${task}`, ...notes.map(n =>
    `${n.file}:${n.line} (${n.side}, revision ${n.revision})\n${n.text}`)].join("\n\n");
}
