"use client";

// The whole conversation panel as a drop target: the transcript is passed in as children, the tray and the input
// row sit below it, and a file dropped anywhere over the panel — over the transcript, over the input, over the
// space between — is attached to the next message. This is the unit a chat page adopts, and it is the reason the
// drop target is separate from the composer: the surface a person aims at is the conversation, not a text box.

import type { ReactNode } from "react";
import { ChatDropTarget } from "./ChatDropTarget";
import { AttachmentTray } from "./AttachmentTray";
import { ComposerInput } from "./ComposerInput";
import { useComposer, type ComposerSubmit } from "./useComposer";

export function ChatSurface({
  children,
  onSubmit,
  sending = false,
  error = null,
  disabled = false,
  placeholder = "Ask about an objective, a plan, or the evidence behind a number…",
  className = "relative flex min-h-[70vh] flex-col rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]",
}: {
  children: ReactNode;
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
      <div className="flex-1 space-y-3 overflow-y-auto p-5">{children}</div>

      <div className="border-t border-[var(--color-border)] p-4">
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
      </div>
    </ChatDropTarget>
  );
}
