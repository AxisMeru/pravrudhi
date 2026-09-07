// Annotations are the one part of the diff view that holds something the reader wrote, so the storage path has
// to survive a browser that refuses to store anything at all — a private window, or site data blocked. The
// notes stay in memory for the session in that case rather than vanishing as they are typed.
//
// Written against `node:assert` like every other spec here: the runner is `node --test`, and importing
// Playwright's `test` gives a runner that never executes and a suite that silently proves nothing.

import { strict as assert } from "node:assert";
import test from "node:test";

import { lineAddresses, readAnnotations, saveAnnotations, type Annotation } from "../../lib/annotations";

test("addresses distinguish deleted and added lines and skip metadata", () => {
  assert.deepEqual(
    lineAddresses({ header: "@@ -5,2 +8,2 @@", lines: [
      { kind: "context", text: "same" }, { kind: "del", text: "before" },
      { kind: "add", text: "after" }, { kind: "context", text: "\\ No newline at end of file" },
    ] }),
    [{ side: "new", line: 8 }, { side: "old", line: 6 }, { side: "new", line: 9 }, null],
  );
});

test("multiple notes, edits, removal and task isolation survive storage failure", () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("Blocked"); } });
  const note: Annotation = { id: "1", file: "a.ts", revision: "a → b", side: "new", line: 1, text: "Fix this" };
  assert.equal(saveAnnotations("one", "a.ts", [note, { ...note, id: "2" }]), false);
  assert.equal(readAnnotations("one").notes.length, 2);
  assert.deepEqual(readAnnotations("two").notes, []);
  saveAnnotations("one", "a.ts", [{ ...note, text: "Edited" }]);
  assert.deepEqual(readAnnotations("one"), { notes: [{ ...note, text: "Edited" }], sessionOnly: true });
  saveAnnotations("one", "a.ts", []);
  assert.deepEqual(readAnnotations("one").notes, []);
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
  assert.deepEqual(readAnnotations("persisted"), { notes: [note], sessionOnly: true });
  assert.equal(saveAnnotations("persisted", "old.ts", [{ ...note, text: "Updated" }]), true);
  assert.equal(JSON.parse(entries.get('pravrudhi:annotations:v1:["persisted","old.ts"]')!)[0].text, "Updated");
});
