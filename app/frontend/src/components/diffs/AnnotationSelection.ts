"use client";

import { useSyncExternalStore } from "react";

// The existing page passes task identity only to TaskList, not to DiffViewer.
// Bridge those siblings without guessing identity from commits or file content.
let selected: string | null = null;
const listeners = new Set<() => void>();
export function selectAnnotationTask(task: string | null) {
  selected = task;
  listeners.forEach(listener => listener());
}
export function useAnnotationTask() {
  return useSyncExternalStore(listener => {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  }, () => selected, () => null);
}
