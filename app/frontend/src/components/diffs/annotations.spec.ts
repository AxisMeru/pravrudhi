import { test, expect } from "@playwright/test";
import { lineAddresses, readAnnotations, saveAnnotations, type Annotation } from "../../lib/annotations";

test("addresses distinguish deleted and added lines and skip metadata", () => {
  expect(lineAddresses({ header: "@@ -5,2 +8,2 @@", lines: [
    { kind: "context", text: "same" }, { kind: "del", text: "before" },
    { kind: "add", text: "after" }, { kind: "context", text: "\\ No newline at end of file" },
  ] })).toEqual([{ side: "new", line: 8 }, { side: "old", line: 6 }, { side: "new", line: 9 }, null]);
});

test("multiple notes, edits, removal and task isolation survive storage failure", () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("Blocked"); } });
  const note: Annotation = { id: "1", file: "a.ts", revision: "a → b", side: "new", line: 1, text: "Fix this" };
  expect(saveAnnotations("one", "a.ts", [note, { ...note, id: "2" }])).toBe(false);
  expect(readAnnotations("one").notes).toHaveLength(2);
  expect(readAnnotations("two").notes).toEqual([]);
  saveAnnotations("one", "a.ts", [{ ...note, text: "Edited" }]);
  expect(readAnnotations("one")).toEqual({ notes: [{ ...note, text: "Edited" }], sessionOnly: true });
  saveAnnotations("one", "a.ts", []);
  expect(readAnnotations("one").notes).toEqual([]);
});

test("loads persisted files for the whole task and tolerates corrupt data", () => {
  const note: Annotation = { id: "saved", file: "old.ts", revision: "old revision", side: "old", line: 4, text: "Still relevant" };
  const entries = new Map([
    ['pravrudhi:annotations:v1:["persisted","old.ts"]', JSON.stringify([note])],
    ['pravrudhi:annotations:v1:["persisted","broken.ts"]', '{broken'],
  ]);
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    get length() { return entries.size; },
    key: (index: number) => [...entries.keys()][index] ?? null,
    getItem: (key: string) => entries.get(key) ?? null,
    setItem: (key: string, value: string) => { entries.set(key, value); },
  } });
  expect(readAnnotations("persisted")).toEqual({ notes: [note], sessionOnly: true });
  expect(saveAnnotations("persisted", "old.ts", [{ ...note, text: "Updated" }])).toBe(true);
  expect(JSON.parse(entries.get('pravrudhi:annotations:v1:["persisted","old.ts"]')!)[0].text).toBe("Updated");
});
