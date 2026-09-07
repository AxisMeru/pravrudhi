"use client";

import { useMemo, useState } from "react";
import { renderMarkdown } from "@/lib/markdown";

export function MarkdownPreview({ text }: { text: string }) {
  const html = useMemo(() => renderMarkdown(text), [text]);
  return <div className="break-words text-sm leading-6 [&_h1]:text-2xl [&_h2]:text-xl [&_h3]:text-lg [&_h1]:font-bold [&_h2]:font-bold [&_h3]:font-bold [&_h4]:font-bold [&_h5]:font-bold [&_h6]:font-bold [&_p]:my-3 [&_ul]:list-disc [&_ol]:list-decimal [&_ul]:pl-6 [&_ol]:pl-6 [&_pre]:overflow-auto [&_pre]:rounded [&_pre]:bg-[var(--color-bg)] [&_pre]:p-3 [&_code]:font-mono [&_a]:text-[var(--color-accent)] [&_a]:underline" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function MarkdownEditor({ text, onChange, disabled = false }: {
  text: string; onChange: (text: string) => void; disabled?: boolean;
}) {
  // A toggle keeps both the source and rendered document readable at full width on phones.
  // The preview is derived from the current draft, never the last saved text.
  const [preview, setPreview] = useState(false);
  return <div>
    <div className="mb-2 flex gap-3">
      <button type="button" aria-pressed={!preview} onClick={() => setPreview(false)}>Write</button>
      <button type="button" aria-pressed={preview} onClick={() => setPreview(true)}>Preview</button>
    </div>
    {preview ? <div aria-label="Markdown preview" className="min-h-64 rounded border border-[var(--color-border)] p-3"><MarkdownPreview text={text} />{!text && <p>Nothing to preview yet.</p>}</div> :
      <label className="block text-sm">Document text (Markdown)
        <textarea value={text} disabled={disabled} onChange={(event) => onChange(event.target.value)} className="mt-2 min-h-64 w-full resize-y rounded border border-[var(--color-border)] bg-[var(--color-bg)] p-3 font-mono text-sm leading-6" placeholder="# My note" />
      </label>}
  </div>;
}
