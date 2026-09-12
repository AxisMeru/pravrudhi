// Typed fetch client for the engine's memory: durable notes, preferences and chat threads that belong to the
// user rather than the ledger (see application/memory.py's module docstring on why the two must never be
// confused). A new file rather than additions to api.ts, so pages built in parallel never contend for that one.

import { ApiError, IS_DEMO, apiBase, engineFetch, localToken } from "./api";

export interface MemoryNote {
  id: string;
  text: string;
  source: string;
  created: string;
  /** When the note was last edited; empty for one nobody has edited. */
  revised?: string;
}

export interface Preference {
  key: string;
  value: unknown;
  source: string;
  set_at: string;
}

export interface MemorySnapshot {
  preferences: Preference[];
  notes: MemoryNote[];
  threads: string[];
}

const EMPTY: MemorySnapshot = { preferences: [], notes: [], threads: [] };

async function getJSON<T>(path: string): Promise<T> {
  const res = await engineFetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

// `EMPTY` in demo mode means the recorded snapshot carries no memory section, not that nothing was ever stored.
export async function memory(): Promise<MemorySnapshot> {
  if (IS_DEMO) {
    const { demo } = await import("./demo");
    const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { memory?: MemorySnapshot };
    return bundle.memory ?? EMPTY;
  }
  return getJSON<MemorySnapshot>("/api/memory");
}

// Mirrors application/memory.py::recall's own ranking: notes arrive already most-recent-first (the server calls
// `store.recall("", limit=50)`, whose base order is append order, newest last item first), and a query only
// ever reorders by a case-insensitive substring match, matched notes first, via a stable sort — so recency still
// decides ties within each group. There is no server-side search endpoint to call per keystroke; this is the
// same ranker, run client-side over the same notes the page already has.
export function recall(notes: MemoryNote[], query: string): MemoryNote[] {
  const q = query.trim().toLowerCase();
  if (!q) return notes;
  return [...notes].sort((a, b) => {
    const am = a.text.toLowerCase().includes(q) ? 0 : 1;
    const bm = b.text.toLowerCase().includes(q) ? 0 : 1;
    return am - bm;
  });
}

// Carries the store's own refusal text (a 422 body's `detail`) verbatim — see memory.py's `remember`, which
// refuses a note that reads as a bare numeric claim about a result rather than a durable fact.
export class RememberError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "RememberError";
  }
}

// State-changing, like every other POST the engine answers: the local token is required (see api.ts's
// localToken and the engine's LocalGuard).
export async function remember(text: string, source = ""): Promise<MemoryNote> {
  if (IS_DEMO) throw new RememberError(501, "this is a recorded run: writing a note needs a local engine");
  const token = await localToken();
  const res = await engineFetch(`${apiBase()}/api/memory/notes`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { "x-pravrudhi-token": token } : {}),
    },
    body: JSON.stringify({ text, source }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body && typeof body === "object" && typeof (body as { detail?: unknown }).detail === "string";
    throw new RememberError(res.status, detail ? (body as { detail: string }).detail : `HTTP ${res.status}`);
  }
  return (await res.json()) as MemoryNote;
}

// Correcting a note keeps its id, so the list can replace it in place rather than reloading the page. Refused on
// the same terms `remember` is refused — a note editable into a ledger number would be a way around that guard.
export async function revise(id: string, text: string, source = ""): Promise<MemoryNote> {
  if (IS_DEMO) throw new RememberError(501, "this is a recorded run: editing a note needs a local engine");
  return writeNote(`/api/memory/notes/${encodeURIComponent(id)}`, "PATCH", { text, source });
}

// Deleting is permanent: the store rewrites its file without the row, and nothing else holds a copy.
export async function forget(id: string): Promise<void> {
  if (IS_DEMO) throw new RememberError(501, "this is a recorded run: deleting a note needs a local engine");
  await writeNote(`/api/memory/notes/${encodeURIComponent(id)}`, "DELETE");
}

async function writeNote(path: string, method: string, body?: unknown): Promise<MemoryNote> {
  const token = await localToken();
  const res = await engineFetch(`${apiBase()}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "content-type": "application/json" }),
      ...(token ? { "x-pravrudhi-token": token } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    const detail = payload && typeof payload === "object" && typeof (payload as { detail?: unknown }).detail === "string";
    throw new RememberError(res.status, detail ? (payload as { detail: string }).detail : `HTTP ${res.status}`);
  }
  return (await res.json()) as MemoryNote;
}
