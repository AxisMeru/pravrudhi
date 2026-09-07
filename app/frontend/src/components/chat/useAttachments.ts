"use client";

// The state a drop target needs: what is attached, what was refused and why, and the message both compose into.
// Kept here rather than in the components so the tray, the drop surface and the composer all read one list, and
// so a second drop landing while the first is still being read is queued behind it instead of racing it — two
// concurrent readers starting from the same snapshot would lose one of the files without saying so.

import { useCallback, useRef, useState } from "react";
import {
  admissionRefusal,
  composeMessage,
  describe,
  readAttachment,
  type Attachment,
  type AttachmentRefusal,
} from "@/lib/attachments";

export interface AttachmentsApi {
  attachments: Attachment[];
  refusals: AttachmentRefusal[];
  /** True while a dropped file is being read and checked. */
  reading: boolean;
  addFiles: (files: File[]) => Promise<void>;
  remove: (id: string) => void;
  dismissRefusal: (id: string) => void;
  /** Drops everything attached and abandons any read still in flight, so nothing arrives after the clear. */
  clear: () => void;
  /** What will be sent: the typed text with every attached file's text folded in after it. */
  compose: (typed: string) => string;
  canSend: (typed: string) => boolean;
}

export function useAttachments(): AttachmentsApi {
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [refusals, setRefusals] = useState<AttachmentRefusal[]>([]);
  const [reading, setReading] = useState(false);

  // The list as it stands, for a queued drop to check its budget against: reading the state variable inside a
  // deferred callback would see the snapshot from when that callback was created, not the one now on screen.
  const listRef = useRef<Attachment[]>([]);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const inFlight = useRef(0);
  // Bumped by `clear`, so a file still being read when the tray was emptied does not reappear into it.
  const generation = useRef(0);

  const replace = useCallback((next: Attachment[]) => {
    listRef.current = next;
    setAttachments(next);
  }, []);

  const refuse = useCallback((refusal: AttachmentRefusal) => {
    setRefusals((current) => [...current, refusal]);
  }, []);

  const process = useCallback(
    async (files: File[], startedAt: number) => {
      for (const file of files) {
        if (startedAt !== generation.current) return;

        const described = describe(file);

        // Budget, count and duplicates are decided before the file is read: a refusal here costs nothing.
        const blocked = admissionRefusal(described, listRef.current);
        if (blocked) {
          refuse(blocked);
          continue;
        }

        const outcome = await readAttachment(file);
        if (startedAt !== generation.current) return;
        if (outcome.ok) {
          replace([...listRef.current, outcome.attachment]);
        } else {
          refuse(outcome.refusal);
        }
      }
    },
    [refuse, replace],
  );

  const addFiles = useCallback(
    (files: File[]) => {
      if (files.length === 0) return Promise.resolve();

      const startedAt = generation.current;
      inFlight.current += 1;
      setReading(true);
      queue.current = queue.current
        .then(() => process(files, startedAt))
        .catch(() => {
          // `process` reports refusals rather than throwing, so this only catches a read the browser aborted
          // underneath us. The queue still has to move on, or every later drop waits behind a file never coming.
        })
        .finally(() => {
          inFlight.current -= 1;
          if (inFlight.current <= 0) {
            inFlight.current = 0;
            setReading(false);
          }
        });
      return queue.current;
    },
    [process],
  );

  const remove = useCallback(
    (id: string) => {
      replace(listRef.current.filter((attachment) => attachment.id !== id));
    },
    [replace],
  );

  const dismissRefusal = useCallback((id: string) => {
    setRefusals((current) => current.filter((refusal) => refusal.id !== id));
  }, []);

  const clear = useCallback(() => {
    generation.current += 1;
    inFlight.current = 0;
    replace([]);
    setRefusals([]);
    setReading(false);
  }, [replace]);

  const compose = useCallback((typed: string) => composeMessage(typed, attachments), [attachments]);

  // An attachment on its own is a message: the user dropped something to ask about it and owes no words with it.
  const canSend = useCallback((typed: string) => typed.trim() !== "" || attachments.length > 0, [attachments]);

  return { attachments, refusals, reading, addFiles, remove, dismissRefusal, clear, compose, canSend };
}
