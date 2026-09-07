"use client";

// The input row with a drop target over it, for a surface that keeps its own transcript: the tray sits above the
// textarea, files land anywhere in the row, and what is sent is the typed text with every accepted file's text
// folded in after it. Someone who never drops a file sees the composer they have always had — the tray renders
// nothing at all until there is something to show.

import { ChatDropTarget } from "./ChatDropTarget";
import { AttachmentTray } from "./AttachmentTray";
import { ComposerInput } from "./ComposerInput";
import { useComposer, type ComposerSubmit } from "./useComposer";

export function ChatComposer({
  onSubmit,
  sending = false,
  error = null,
  disabled = false,
  placeholder = "Ask about an objective, a plan, or the evidence behind a number…",
  className = "relative border-t border-[var(--color-border)] p-4",
}: {
  onSubmit: ComposerSubmit;
  sending?: boolean;
  error?: string | null;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
}) {
  const composer = useComposer({ onSubmit, sending, disabled });
  const { tray } = composer;

  return (
    <ChatDropTarget onFiles={(files) => void tray.addFiles(files)} className={className}>
      <AttachmentTray
        attachments={tray.attachments}
        refusals={tray.refusals}
        reading={tray.reading}
        onRemove={tray.remove}
        onDismissRefusal={tray.dismissRefusal}
      />

      {error && <p className="mb-2 text-xs text-[var(--color-danger)]">{error}</p>}

      <ComposerInput
        value={composer.input}
        onChange={composer.setInput}
        onSubmit={composer.submit}
        canSend={composer.canSend}
        sending={sending}
        disabled={disabled}
        placeholder={placeholder}
      />
    </ChatDropTarget>
  );
}
