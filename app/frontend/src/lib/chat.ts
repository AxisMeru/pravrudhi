// The streaming half of the chat surface: parses `POST /api/chat/stream`'s server-sent events as they arrive so
// a long tool-calling turn shows real progress - "reading the ledger", then its result - instead of a blank
// wait ending in one lump reply. `streamChat` always resolves with the same `ChatResponse` shape `chat()` does,
// because whatever happens mid-stream, the caller ends up holding one finished turn either way.
//
// A turn that fails partway - the endpoint drops, a chunk cannot be parsed, the connection never opens - falls
// back to the existing blocking route with the same message and thread id. The backend defers persisting the
// user's turn until it has an answer for it (see `application/chat.py`'s `_finish_turn`), so a stream that dies
// before its `done` event leaves nothing in the thread for this fallback call to duplicate.

import { apiBase, chat as chatBlocking, engineFetch, localToken, type ChatCitation, type ChatResponse } from "./api";

export interface ChatToolEvent {
  phase: "called" | "returned";
  tool: string;
  args: Record<string, unknown>;
  result_summary?: string;
}

export interface ChatStreamHandlers {
  onTool?: (event: ChatToolEvent) => void;
  onToken?: (text: string) => void;
  onCitation?: (citation: ChatCitation) => void;
}

// What each tool is doing, in the plain terms a person watching the wait would want, not its API name. Every
// tool the assistant can call (`application/chat.py::TOOL_SCHEMA`) has an entry so a call in flight is never
// left describing itself by its raw identifier.
const TOOL_LABELS: Record<string, string> = {
  objectives: "reading the objectives ledger",
  objective_progress: "checking the objective's standing",
  objective_plan: "reading the objective's plan",
  recipes: "checking the recipe catalogue",
  tools: "checking the tool catalogue",
  memory_recall: "recalling notes",
  memory_remember: "writing a note",
  routing_report: "checking the routing report",
  evidence: "reading the evidence",
  appetite: "checking what the engine wants",
};

export function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? `calling ${tool}`;
}

async function readSseEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: Record<string, unknown>) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      onEvent(JSON.parse(line.slice("data: ".length)));
    }
  }
}

export async function streamChat(
  message: string,
  threadId: string | null,
  handlers: ChatStreamHandlers,
): Promise<ChatResponse> {
  let response: Response | null = null;
  try {
    const token = await localToken();
    response = await engineFetch(`${apiBase()}/api/chat/stream`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(token ? { "x-pravrudhi-token": token } : {}),
      },
      body: JSON.stringify({ message, thread_id: threadId }),
    });
  } catch {
    // The engine never answered the request at all - the same condition `chat()` would have hit.
    return chatBlocking(message, threadId);
  }

  if (!response.ok || !response.body) {
    return chatBlocking(message, threadId);
  }

  let finished: ChatResponse | null = null;
  try {
    await readSseEvents(response.body, (event) => {
      if (finished) return; // a stream that kept sending after `done` is not trusted past the first one
      switch (event.type) {
        case "tool":
          handlers.onTool?.(event as unknown as ChatToolEvent);
          break;
        case "token":
          handlers.onToken?.(String(event.text ?? ""));
          break;
        case "citation":
          handlers.onCitation?.({ seq: Number(event.seq), what: String(event.what ?? "") });
          break;
        case "done":
          finished = event as unknown as ChatResponse;
          break;
        // an "error" event, or anything unrecognised, falls through to the post-stream fallback below
      }
    });
  } catch {
    // a malformed chunk or a connection dropped mid-body; the fallback below still has the message
  }

  if (finished) return finished;
  return chatBlocking(message, threadId);
}
