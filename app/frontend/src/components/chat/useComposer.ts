"use client";

// What the typed text and the attached files become when the user sends: one message string, ready to hand to
// the same turn the surface already sends. The attachments are passed along beside it so a transcript can show a
// short label for the turn instead of the whole payload, while the message that goes to the engine still carries
// every byte that was accepted.

import { useCallback, useState } from "react";
import type { Attachment } from "@/lib/attachments";
import { useAttachments, type AttachmentsApi } from "./useAttachments";

export type ComposerSubmit = (message: string, attachments: Attachment[]) => void;

export interface ComposerApi {
  input: string;
  setInput: (text: string) => void;
  tray: AttachmentsApi;
  /** True when there is something to send: words, an attachment, or both. */
  canSend: boolean;
  submit: () => void;
}

export function useComposer({
  onSubmit,
  sending = false,
  disabled = false,
}: {
  onSubmit: ComposerSubmit;
  sending?: boolean;
  disabled?: boolean;
}): ComposerApi {
  const [input, setInput] = useState("");
  const tray = useAttachments();

  const submit = useCallback(() => {
    if (sending || disabled) return;

    const message = tray.compose(input);
    if (message.trim() === "") return;

    const attached = tray.attachments;
    // Cleared before the turn is handed over, so the composer is ready for the next message while this one
    // streams. A turn that fails does not give the text back: it is in the transcript already, exactly as the
    // surface behaved before a file could be attached to it.
    setInput("");
    tray.clear();
    onSubmit(message, attached);
  }, [disabled, input, onSubmit, sending, tray]);

  return { input, setInput, tray, canSend: tray.canSend(input), submit };
}
