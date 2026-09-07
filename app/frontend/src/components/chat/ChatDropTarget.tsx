"use client";

// The drop half of attaching a file: a surface that knows when a file is being dragged over it, says so, and
// hands the drop to whoever owns the attachment state. It owns no attachment logic of its own, so the same
// surface can wrap a whole conversation panel or just the input row.

import { useCallback, useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Paperclip } from "lucide-react";
import { MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS, MAX_TOTAL_ATTACHMENT_BYTES, formatBytes } from "@/lib/attachments";

// A drag carrying anything other than a file — a selected word dragged inside the textarea, a link dragged to
// the bookmark bar — must leave the surface alone, so every handler asks this first.
function carriesFiles(event: DragEvent | globalThis.DragEvent): boolean {
  const types = event.dataTransfer?.types;
  if (!types) return false;
  return Array.from(types as ArrayLike<string>).includes("Files");
}

export function ChatDropTarget({
  onFiles,
  children,
  className = "relative",
}: {
  onFiles: (files: File[]) => void;
  children: ReactNode;
  className?: string;
}) {
  // dragenter and dragleave fire once per element crossed, so a counter is what distinguishes leaving a child
  // from leaving the surface. Without it the overlay flickers off between the transcript and the input.
  const depth = useRef(0);
  const [dragging, setDragging] = useState(false);

  const settle = useCallback((next: number) => {
    depth.current = Math.max(0, next);
    setDragging(depth.current > 0);
  }, []);

  // The browser's answer to a file dropped outside an input is to navigate to it, which would leave the
  // conversation entirely — thread, transcript and all. While a file is being dragged anywhere in the window
  // that default is suppressed, and this surface is the only thing that accepts one. Drags that are not files
  // are never touched, so selecting and dragging words inside the textarea behaves exactly as it did before.
  useEffect(() => {
    const suppress = (event: globalThis.DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
    };
    // Dragging out of the window ends the drag with no leave event to count, so the overlay has to be dropped
    // here or it stays up over a conversation the user is no longer dragging anything onto.
    const left = (event: globalThis.DragEvent) => {
      if (event.relatedTarget === null) settle(0);
    };
    window.addEventListener("dragover", suppress);
    window.addEventListener("drop", suppress);
    window.addEventListener("dragleave", left);
    return () => {
      window.removeEventListener("dragover", suppress);
      window.removeEventListener("drop", suppress);
      window.removeEventListener("dragleave", left);
    };
  }, [settle]);

  const onDragEnter = (event: DragEvent<HTMLDivElement>) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    settle(depth.current + 1);
  };

  const onDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!carriesFiles(event)) return;
    // Without this the browser reports the surface as not-a-target and the drop never arrives.
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };

  const onDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    settle(depth.current - 1);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    // A surface nested inside another (an input row inside a conversation panel) must not have the same drop
    // counted twice: the outer one would refuse the second copy as a duplicate of the first.
    event.stopPropagation();
    settle(0);
    const files = Array.from(event.dataTransfer?.files ?? []);
    if (files.length > 0) onFiles(files);
  };

  return (
    <div
      className={className}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {children}

      {dragging && (
        <div className="absolute inset-0 z-20 flex items-center justify-center rounded-lg bg-[var(--color-bg)]/85 p-6">
          <div className="flex max-w-md flex-col items-center gap-2 rounded-lg border-2 border-dashed border-[var(--color-accent)] px-6 py-5 text-center">
            <Paperclip size={18} className="text-[var(--color-accent)]" />
            <p className="text-sm font-medium text-[var(--color-text)]">Drop to attach it to your message</p>
            <p className="text-xs leading-5 text-[var(--color-text-dim)]">
              Text, source, markdown, JSON or CSV — up to {formatBytes(MAX_ATTACHMENT_BYTES)} each, {MAX_ATTACHMENTS}{" "}
              files and {formatBytes(MAX_TOTAL_ATTACHMENT_BYTES)} in one message. There is no upload route on this
              engine: the file is read here in the browser and its text goes into the message you send, so a binary
              or anything larger is refused rather than sent as noise.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
