// A renderer that hands its output to `dangerouslySetInnerHTML` is a security boundary, and this one arrived
// without a test. The code is careful — an allowlist of schemes rather than a blocklist, control characters
// rejected, every text span escaped — but "careful on reading" is not the standard for something that renders
// text a user typed into a page other people may open.
//
// These are the cases that break naive markdown renderers, written as the attack rather than as the feature.

import { strict as assert } from "node:assert";
import test from "node:test";

import { renderMarkdown } from "./markdown";

test("raw HTML is shown as text, never as markup", () => {
  const html = renderMarkdown("<script>alert(1)</script>");
  assert.ok(!html.includes("<script>"), "a script tag survived into the output");
  assert.ok(html.includes("&lt;script&gt;"));
});

test("an image tag with an error handler is inert", () => {
  const html = renderMarkdown('<img src=x onerror="alert(1)">');
  assert.ok(!html.includes("<img"), "an img tag survived into the output");
  assert.ok(!html.includes("onerror=\""), "an event handler survived unescaped");
});

test("a javascript link is refused and shown as the text the author wrote", () => {
  const html = renderMarkdown("[click me](javascript:alert(1))");
  assert.ok(!html.includes("href=\"javascript"), "a javascript: URL became a link");
  assert.ok(html.includes("click me"), "the author's text should still be visible");
});

test("a data URL is refused", () => {
  const html = renderMarkdown("[x](data:text/html;base64,PHNjcmlwdD4=)");
  assert.ok(!html.includes("href=\"data:"));
});

test("a protocol-relative URL is refused, since it inherits the page's scheme", () => {
  const html = renderMarkdown("[x](//evil.example/path)");
  assert.ok(!html.includes("href=\"//"));
});

test("a scheme disguised with whitespace or control characters is refused", () => {
  for (const href of ["java\tscript:alert(1)", "java\nscript:alert(1)", " javascript:alert(1)"]) {
    const html = renderMarkdown(`[x](${href})`);
    assert.ok(!/href="[^"]*script/i.test(html), `a disguised scheme got through: ${JSON.stringify(href)}`);
  }
});

test("an ordinary link is rendered, with the attributes that make it safe to open", () => {
  const html = renderMarkdown("[docs](https://example.com/a)");
  assert.ok(html.includes('href="https://example.com/a"'));
  assert.ok(html.includes('rel="noopener noreferrer"'));
});

test("a quote in a URL cannot break out of the attribute", () => {
  const html = renderMarkdown('[x](https://example.com/")');
  assert.ok(!/href="https:\/\/example\.com\/"[^>]/.test(html), "the href attribute was escapable");
});

test("the ordinary formatting still works", () => {
  const html = renderMarkdown("# Title\n\nSome **bold** and `code`.\n\n- one\n- two");
  for (const fragment of ["<h1>", "<strong>bold</strong>", "<code>code</code>", "<ul>", "<li>"]) {
    assert.ok(html.includes(fragment), `missing ${fragment}`);
  }
});

test("deeply nested emphasis terminates rather than running away", () => {
  const html = renderMarkdown("*".repeat(200) + "x" + "*".repeat(200));
  assert.ok(typeof html === "string");
});
