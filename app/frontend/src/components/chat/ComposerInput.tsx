"use client";

// The input row on its own: what the conversation panel has always had, unchanged, with the send button's
// readiness handed to it rather than decided here. Split out so a whole conversation panel and a bare input row
// can share one textarea instead of the two drifting apart.

import { Send } from "lucide-react";

export function ComposerInput({
  value,
  onChange,
  onSubmit,
  canSend,
  sending = false,
  disabled = false,
  placeholder,
}: {
  value: string;
  onChange: (text: string) => void;
  onSubmit: () => void;
  canSend: boolean;
  sending?: boolean;
  disabled?: boolean;
  placeholder?: string;
}) {
  return (
    <div className="flex items-end gap-2">
      <textarea
        className="min-h-11 flex-1 resize-y rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] disabled:cursor-not-allowed disabled:opacity-50"
        placeholder={placeholder}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          // Enter sends, Shift+Enter is a new line: the same two keys doing the same two things as before.
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            onSubmit();
          }
        }}
      />
      <button
        onClick={onSubmit}
        disabled={disabled || sending || !canSend}
        className="flex items-center gap-2 rounded-md bg-[var(--color-accent)] px-4 py-2.5 text-sm font-medium text-[#06110c] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
      >
        <Send size={15} />
        {sending ? "Sending…" : "Send"}
      </button>
    </div>
  );
}
