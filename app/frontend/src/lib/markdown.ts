// A deliberately small Markdown subset. Raw HTML is always text, never markup.
const escape = (text: string): string => text.replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]!);

function inline(text: string, depth = 0): string {
  if (depth > 12) return escape(text);
  const token = /(`+)([^`]*?)\1|\[([^\]\n]+)\]\(([^\s)]+)\)|(\*\*|__)(.+?)\5|(\*|_)([^\n]+?)\7/g;
  let result = "", start = 0;
  for (const match of text.matchAll(token)) {
    result += escape(text.slice(start, match.index));
    if (match[1]) result += `<code>${escape(match[2])}</code>`;
    else if (match[3]) {
      const href = match[4];
      // Only explicit safe schemes and local fragments/paths; no protocol-relative URLs.
      const safe = /^(https?:\/\/|mailto:|#|\/(?!\/))/i.test(href) && !/[\u0000-\u0020\\]/.test(href);
      result += safe ? `<a href="${escape(href)}" rel="noopener noreferrer">${inline(match[3], depth + 1)}</a>` : escape(match[0]);
    } else {
      const tag = match[5] ? "strong" : "em";
      result += `<${tag}>${inline(match[6] ?? match[8], depth + 1)}</${tag}>`;
    }
    start = match.index! + match[0].length;
  }
  return result + escape(text.slice(start));
}

export function renderMarkdown(source: string): string {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const output: string[] = [];
  let i = 0;
  const block = (line: string) => /^(?:\s*$| {0,3}(?:#{1,6}\s|`{3,}|~{3,}|[-+*]\s|\d+[.)]\s))/.test(line);
  while (i < lines.length) {
    const line = lines[i++];
    if (!line.trim()) continue;
    const fence = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (fence) {
      const code: string[] = [];
      const end = new RegExp(`^ {0,3}${fence[1][0]}{${fence[1].length},}\\s*$`);
      while (i < lines.length && !end.test(lines[i])) code.push(lines[i++]);
      if (i < lines.length) i++;
      output.push(`<pre><code>${escape(code.join("\n"))}</code></pre>`);
      continue;
    }
    const heading = line.match(/^ {0,3}(#{1,6})\s+(.+?)(?:\s+#+)?$/);
    if (heading) {
      output.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`);
      continue;
    }
    const item = line.match(/^ {0,3}([-+*]|\d+[.)])\s+(.*)$/);
    if (item) {
      const ordered = /^\d/.test(item[1]);
      const tag = ordered ? "ol" : "ul";
      const items = [inline(item[2])];
      while (i < lines.length) {
        const next = lines[i].match(/^ {0,3}([-+*]|\d+[.)])\s+(.*)$/);
        if (!next || /^\d/.test(next[1]) !== ordered) break;
        items.push(inline(next[2])); i++;
      }
      output.push(`<${tag}${ordered ? ` start="${parseInt(item[1], 10)}"` : ""}><li>${items.join("</li><li>")}</li></${tag}>`);
      continue;
    }
    const paragraph = [line];
    while (i < lines.length && !block(lines[i])) paragraph.push(lines[i++]);
    output.push(`<p>${inline(paragraph.join("\n"))}</p>`);
  }
  return output.join("\n");
}
