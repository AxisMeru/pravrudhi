// Data for the Search page: how the candidate graph branches, and how often the budget actually forced the
// controller to leave something out. Both are folded from the ledger, so the recorded demo carries them
// unchanged and a page without a live engine shows the same numbers rather than an error.

import { apiBase, ApiError, IS_DEMO } from "@/lib/api";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

export interface Ancestry {
  nodes: number;
  roots: number;
  max_depth: number;
  distinct_parents: number;
  widest: [string, number] | null;
}

export interface NightPressure {
  night: number;
  live: number;
  selected: number;
  declined: number;
  binding: boolean;
}

export interface SearchSnapshot {
  ancestry: Ancestry;
  pressure: NightPressure[];
  binding_nights: number;
  declined: number;
}

export async function search(): Promise<SearchSnapshot | null> {
  if (IS_DEMO) {
    const { demo } = await import("./demo");
    const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { search?: SearchSnapshot };
    return bundle.search ?? null;
  }
  try {
    return await getJSON<SearchSnapshot>("/api/search");
  } catch {
    return null;
  }
}
