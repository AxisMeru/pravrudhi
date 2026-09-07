import { apiBase, ApiError, IS_DEMO, type RunEvent } from "./api";

function parseFrame(frame: string): RunEvent | null {
  const data = frame.split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""))
    .join("\n");
  if (!data) return null;
  try {
    const event: unknown = JSON.parse(data);
    return event !== null && typeof event === "object" &&
      "type" in event && typeof event.type === "string" ? event as RunEvent : null;
  } catch {
    return null;
  }
}

// Like chatStream, consume SSE with a streaming decoder and retain partial frames.
// No automatic reconnect: the server replays history and supplies no resume IDs.
export async function* terminalStream(runId: string, signal: AbortSignal): AsyncGenerator<RunEvent> {
  const path = `/api/runs/${encodeURIComponent(runId)}/events`;
  if (IS_DEMO) throw new Error("Live terminal output requires a connected engine.");
  const response = await fetch(`${apiBase()}${path}`, {
    signal, cache: "no-store", headers: { Accept: "text/event-stream" },
  });
  if (!response.ok) throw new ApiError(response.status, path);
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Response has no readable body");
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let boundary: RegExpExecArray | null;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const event = parseFrame(buffer.slice(0, boundary.index));
        buffer = buffer.slice(boundary.index + boundary[0].length);
        if (event) {
          yield event;
          if (event.type === "end") return;
        }
      }
      if (done) {
        const event = parseFrame(buffer);
        if (event) yield event;
        return;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function formatRunEvent(event: RunEvent): string {
  if (event.type === "log" && typeof event.text === "string") return event.text;
  // Preserve all structured fields, including fields introduced by newer engines.
  const fields = Object.entries(event)
    .filter(([key]) => key !== "type")
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`);
  return `[${event.type}] ${fields.join(" ")}`;
}
