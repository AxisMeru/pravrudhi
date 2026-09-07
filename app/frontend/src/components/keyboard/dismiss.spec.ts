// Escape has to close exactly one thing: the topmost. A stack that dismissed the wrong layer would close the
// dialog underneath a confirmation, or leave a modal open while dismissing something the reader could not see.
// The ordering is the whole contract, and it had no test.

import { strict as assert } from "node:assert";
import test from "node:test";

import { dismissTopmost, onDismiss } from "./dismiss";

test("nothing registered means Escape was not handled here", () => {
  assert.equal(dismissTopmost(), false, "claiming to have dismissed nothing would swallow the key");
});

test("the most recently opened layer is the one that closes", () => {
  const closed: string[] = [];
  onDismiss(() => closed.push("dialog"));
  onDismiss(() => closed.push("confirmation"));

  assert.equal(dismissTopmost(), true);
  assert.deepEqual(closed, ["confirmation"], "Escape closed the layer underneath the one on top");
  assert.equal(dismissTopmost(), true);
  assert.deepEqual(closed, ["confirmation", "dialog"]);
  assert.equal(dismissTopmost(), false, "the stack kept a handler it had already run");
});

test("a layer that closes itself is no longer on the stack", () => {
  const closed: string[] = [];
  const release = onDismiss(() => closed.push("dialog"));
  onDismiss(() => closed.push("confirmation"));

  release();  // the dialog unmounted on its own, from under the confirmation

  assert.equal(dismissTopmost(), true);
  assert.equal(dismissTopmost(), false);
  assert.deepEqual(closed, ["confirmation"], "an unmounted layer was still asked to close");
});

test("releasing twice is harmless", () => {
  const release = onDismiss(() => {});
  release();
  release();
  assert.equal(dismissTopmost(), false);
});

test("a dismissed layer's release does not remove the layer that replaced it", () => {
  const closed: string[] = [];
  const release = onDismiss(() => closed.push("first"));
  dismissTopmost();          // Escape closed it, and it is off the stack
  onDismiss(() => closed.push("second"));

  release();                 // its unmount cleanup runs afterwards

  assert.equal(dismissTopmost(), true);
  assert.deepEqual(closed, ["first", "second"], "a stale cleanup removed a live layer");
});
