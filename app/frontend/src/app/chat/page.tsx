"use client";

// A conversation with the engine, held to the honesty boundary: a reply may state a number only because a tool
// call this turn returned it, and every such number carries the ledger row that justifies it. Anything the
// model's draft stated that no tool backed comes back in `refusals` instead of quietly vanishing.

import { useCallback, useEffect, useRef, useState } from "react";
import { Send, Plus } from "lucide-react";
import {
  chat,
  chatStream,
  chatThreads,
  chatThread,
  ApiError,
  IS_DEMO,
  type ChatCitation,
  type ChatToolCall,
  type ChatThreadSummary,
} from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
// Dropping a file into the conversation. The components existed and were mounted on nothing, which is the same
// defect the command palette had: real code, reachable by nobody, and correctly recorded as absent on the
// parity board until wired here.
import { AttachmentTray, ChatDropTarget, useAttachments } from "@/components/chat";

interface DisplayTurn {
  role: "user" | "assistant";
  content: string;
  citations?: ChatCitation[];
  toolCalls?: ChatToolCall[];
  refusals?: string[];
}

function DemoChat() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Chat"
        subtitle="Ask about objectives, plans and evidence. Every number in a reply is backed by a tool call made in that turn."
      />
      <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
        <h2 className="text-sm font-medium text-[var(--color-text)]">Run the engine locally to chat.</h2>
        <p className="mt-1.5 max-w-2xl text-sm text-[var(--color-text-dim)]">
          This is a recording of the public site, with no engine behind it to answer. Install the engine and this
          page becomes a live conversation grounded in your own ledger.
        </p>
        <a
          href="/install"
          className="mt-4 inline-flex items-center gap-2 rounded-md bg-emerald-500/90 px-4 py-2 text-sm font-medium text-black hover:bg-emerald-400"
        >
          Get it running
        </a>
      </section>
    </div>
  );
}

function ThreadList({
  threads,
  unsupported,
  activeId,
  onSelect,
  onNew,
}: {
  threads: ChatThreadSummary[];
  unsupported: boolean;
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
      <button
        onClick={onNew}
        className="flex w-full items-center justify-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-raised)]"
      >
        <Plus size={14} />
        New chat
      </button>

      {unsupported && (
        <p className="mt-3 text-xs text-[var(--color-text-dim)]">This engine build does not report chat threads yet.</p>
      )}
      {!unsupported && threads.length === 0 && (
        <p className="mt-3 text-xs text-[var(--color-text-dim)]">No conversations yet.</p>
      )}

      <ul className="mt-2 space-y-1">
        {threads.map((t) => (
          <li key={t.id}>
            <button
              onClick={() => onSelect(t.id)}
              className={`flex w-full flex-col items-start gap-0.5 rounded-md px-3 py-2 text-left transition-colors ${
                t.id === activeId
                  ? "bg-[var(--color-surface-raised)] text-[var(--color-text)]"
                  : "text-[var(--color-text-dim)] hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
              }`}
            >
              <span className="w-full truncate font-mono text-xs">{t.id}</span>
              <span className="text-[11px] text-[var(--color-text-dim)]">{t.turns} turns</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TurnBubble({ turn }: { turn: DisplayTurn }) {
  const isUser = turn.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-2.5 text-sm leading-6 ${
          isUser
            ? "bg-[var(--color-accent-dim)]/30 text-[var(--color-text)]"
            : "border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]"
        }`}
      >
        <p className="whitespace-pre-wrap">{turn.content}</p>

        {!isUser && turn.citations && turn.citations.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {turn.citations.map((c, i) => (
              <span
                key={i}
                title={c.what}
                className="rounded-full border border-[var(--color-border)] px-2 py-0.5 font-mono text-[11px] text-[var(--color-text-dim)]"
              >
                row {c.seq}
              </span>
            ))}
          </div>
        )}

        {!isUser && turn.refusals && turn.refusals.length > 0 && (
          <details className="mt-2 text-[11px] text-[var(--color-text-dim)]">
            <summary className="cursor-pointer">
              removed {turn.refusals.length} unsupported number{turn.refusals.length === 1 ? "" : "s"}
            </summary>
            <ul className="mt-1 space-y-1 pl-3">
              {turn.refusals.map((r, i) => (
                <li key={i} className="list-disc leading-4">
                  {r}
                </li>
              ))}
            </ul>
          </details>
        )}

        {!isUser && turn.toolCalls && turn.toolCalls.length > 0 && (
          <details className="mt-2 text-[11px] text-[var(--color-text-dim)]">
            <summary className="cursor-pointer">
              {turn.toolCalls.length} tool call{turn.toolCalls.length === 1 ? "" : "s"}
            </summary>
            <ul className="mt-1 space-y-1.5 pl-3">
              {turn.toolCalls.map((tc, i) => (
                <li key={i} className="font-mono leading-4">
                  {tc.tool}({JSON.stringify(tc.args)}) → {tc.result_summary}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}

function LiveChat() {
  const [threads, setThreads] = useState<ChatThreadSummary[]>([]);
  const [threadsUnsupported, setThreadsUnsupported] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<DisplayTurn[]>([]);
  const [turnsLoading, setTurnsLoading] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const tray = useAttachments();

  const loadThreads = useCallback(() => {
    chatThreads()
      .then((rows) => {
        setThreads(rows);
        setThreadsUnsupported(false);
      })
      .catch(() => setThreadsUnsupported(true));
  }, []);

  useEffect(loadThreads, [loadThreads]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [turns.length]);

  const selectThread = (id: string) => {
    setActiveId(id);
    setSendError(null);
    setTurnsLoading(true);
    chatThread(id)
      .then((rows) =>
        setTurns(
          rows.map((t) => ({
            role: t.role,
            content: t.content,
            citations: t.meta?.citations,
            toolCalls: t.meta?.tool_calls,
            refusals: t.meta?.refusals,
          })),
        ),
      )
      .catch(() => setTurns([]))
      .finally(() => setTurnsLoading(false));
  };

  const startNew = () => {
    setActiveId(null);
    setTurns([]);
    setSendError(null);
  };

  const send = async () => {
    if (sending || !tray.canSend(input)) return;
    // What is sent is the typed text with each attached file's contents folded in after it. A message may be
    // attachments alone, which is why the guard asks the tray rather than testing the text: dropping a file and
    // pressing send with nothing typed is a reasonable thing to do.
    const text = tray.compose(input);
    setInput("");
    tray.clear();
    setSending(true);
    setSendError(null);
    setTurns((t) => [...t, { role: "user", content: text }]);

    // Initialize and add the assistant turn immediately so we can update it as events stream in
    const initialAssistantTurn: DisplayTurn = {
      role: "assistant",
      content: "",
      citations: [],
      toolCalls: [],
      refusals: [],
    };
    setTurns((t) => [...t, initialAssistantTurn]);

    let assistantTurn: DisplayTurn = { ...initialAssistantTurn };
    let threadId: string | null = null;
    let hasError = false;

    try {
      for await (const event of chatStream(text, activeId)) {
        const type = event.type as string;

        if (type === "token") {
          const tokenText = event.text as string | undefined;
          if (tokenText) {
            assistantTurn.content += tokenText;
            // Update the UI with the new token progressively
            setTurns((t) => {
              const copy = [...t];
              if (copy[copy.length - 1]?.role === "assistant") {
                copy[copy.length - 1] = { ...assistantTurn };
              }
              return copy;
            });
          }
        } else if (type === "citation") {
          const citation: ChatCitation = {
            seq: event.seq as number,
            what: event.what as string,
          };
          assistantTurn.citations = [...(assistantTurn.citations || []), citation];
          setTurns((t) => {
            const copy = [...t];
            if (copy[copy.length - 1]?.role === "assistant") {
              copy[copy.length - 1] = { ...assistantTurn };
            }
            return copy;
          });
        } else if (type === "tool") {
          const phase = event.phase as string;
          if (phase === "called") {
            const toolCall: ChatToolCall = {
              tool: event.tool as string,
              args: event.args as Record<string, unknown>,
              result_summary: "",
            };
            assistantTurn.toolCalls = [...(assistantTurn.toolCalls || []), toolCall];
            setTurns((t) => {
              const copy = [...t];
              if (copy[copy.length - 1]?.role === "assistant") {
                copy[copy.length - 1] = { ...assistantTurn };
              }
              return copy;
            });
          } else if (phase === "returned") {
            // Update the result_summary of the last tool call
            if (assistantTurn.toolCalls && assistantTurn.toolCalls.length > 0) {
              const lastIdx = assistantTurn.toolCalls.length - 1;
              assistantTurn.toolCalls[lastIdx].result_summary = event.result_summary as string || "";
            }
            setTurns((t) => {
              const copy = [...t];
              if (copy[copy.length - 1]?.role === "assistant") {
                copy[copy.length - 1] = { ...assistantTurn };
              }
              return copy;
            });
          }
        } else if (type === "done") {
          threadId = event.thread_id as string;
          assistantTurn.content = event.reply as string;
          assistantTurn.citations = event.citations as ChatCitation[];
          assistantTurn.toolCalls = event.tool_calls as ChatToolCall[];
          assistantTurn.refusals = event.refusals as string[];
          setTurns((t) => {
            const copy = [...t];
            if (copy[copy.length - 1]?.role === "assistant") {
              copy[copy.length - 1] = { ...assistantTurn };
            }
            return copy;
          });
        } else if (type === "error") {
          setSendError(event.error as string);
          hasError = true;
          break;
        }
      }

      if (!hasError && threadId) {
        setActiveId(threadId);
        loadThreads();
      }
    } catch (e) {
      setSendError(
        e instanceof ApiError ? `chat is not available on this engine build (HTTP ${e.status})` : e instanceof Error ? e.message : String(e),
      );
    } finally {
      setSending(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Chat"
        subtitle="Every number in a reply came from a tool call this turn; a number the model wrote that no tool returned is removed and listed instead."
      />
      <div className="grid grid-cols-1 gap-6 p-8 lg:grid-cols-[260px_1fr]">
        <ThreadList
          threads={threads}
          unsupported={threadsUnsupported}
          activeId={activeId}
          onSelect={selectThread}
          onNew={startNew}
        />

        <div className="flex min-h-[70vh] flex-col rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
          <div className="flex-1 space-y-3 overflow-y-auto p-5">
            {turnsLoading && <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>}
            {!turnsLoading && turns.length === 0 && (
              <p className="text-sm text-[var(--color-text-dim)]">
                Ask something below. Only tool-backed numbers make it into the reply.
              </p>
            )}
            {!turnsLoading &&
              turns.map((t, i) => <TurnBubble key={i} turn={t} />)}
            <div ref={bottomRef} />
          </div>

          <ChatDropTarget
            onFiles={(files) => void tray.addFiles(files)}
            className="border-t border-[var(--color-border)] p-4"
          >
            {sendError && <p className="mb-2 text-xs text-[var(--color-danger)]">{sendError}</p>}
            <AttachmentTray
              attachments={tray.attachments}
              refusals={tray.refusals}
              reading={tray.reading}
              onRemove={tray.remove}
              onDismissRefusal={tray.dismissRefusal}
            />
            <div className="flex items-end gap-2">
              <textarea
                className="min-h-11 flex-1 resize-y rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]"
                placeholder="Ask about an objective, a plan, or the evidence behind a number…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
              />
              <button
                onClick={send}
                disabled={sending || !tray.canSend(input)}
                className="flex items-center gap-2 rounded-md bg-[var(--color-accent)] px-4 py-2.5 text-sm font-medium text-[#06110c] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Send size={15} />
                {sending ? "Sending…" : "Send"}
              </button>
            </div>
          </ChatDropTarget>
        </div>
      </div>
    </div>
  );
}

export default function ChatPage() {
  // Decided after mount: the prerendered HTML has no window, so choosing here rather than at module load keeps
  // the server output and the first client render identical.
  const [mode, setMode] = useState<"unknown" | "demo" | "live">("unknown");
  useEffect(() => setMode(IS_DEMO ? "demo" : "live"), []);
  if (mode === "unknown") return <div className="h-48 animate-pulse rounded-lg bg-[var(--color-surface)]" />;
  return mode === "demo" ? <DemoChat /> : <LiveChat />;
}
