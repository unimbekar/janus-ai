"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { MarkdownBody } from "@/components/MarkdownBody";
import { Topbar } from "@/components/Topbar";
import {
  type ConversationSummary,
  type JanusError,
  type ModelSummary,
  type RoutingInfo,
  type SessionInfo,
  api,
  streamConversationMessage,
} from "@/lib/api";

const SUGGESTIONS = [
  "Summarize this contract and flag unusual terms",
  "Write a Python script to reconcile two CSV exports",
  "Draft a customer follow-up email",
];

interface Turn {
  role: "user" | "assistant";
  content: string;
  routing?: RoutingInfo;
  streaming?: boolean;
}

function titleOf(conversation: ConversationSummary): string {
  return conversation.title?.trim() || "New conversation";
}

export function Chat({
  session,
  onSignOut,
}: {
  session: SessionInfo;
  onSignOut: () => void;
}) {
  const router = useRouter();
  const params = useSearchParams();
  const conversationId = params.get("c");
  const presetModel = params.get("model");

  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [threads, setThreads] = useState<ConversationSummary[]>([]);
  const [selected, setSelected] = useState(presetModel || "auto");
  const [error, setError] = useState<JanusError | null>(null);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const skipLoadRef = useRef<string | null>(null);

  useEffect(() => {
    api.models().then(setModels).catch(() => setModels([]));
    api.conversations().then(setThreads).catch(() => setThreads([]));
  }, []);

  useEffect(() => {
    if (!conversationId) return;
    if (skipLoadRef.current === conversationId) {
      skipLoadRef.current = null;
      return;
    }
    let cancelled = false;
    api
      .conversation(conversationId)
      .then((detail) => {
        if (cancelled) return;
        setTurns(
          detail.messages.map((message) => ({
            role: message.role === "assistant" ? "assistant" : "user",
            content: message.content,
            routing:
              message.role === "assistant" && message.model
                ? {
                    model: message.model,
                    deployment: message.deployment ?? "",
                    provider: message.provider ?? "",
                    privacy: message.privacy ?? "unknown",
                    fallback_used: message.fallback_used,
                    routing_explanation: message.routing_explanation,
                  }
                : undefined,
          })),
        );
      })
      .catch(() => {
        if (!cancelled) {
          setTurns([]);
          setError({
            type: "not_found",
            code: "conversation_not_found",
            message: "That conversation is not available.",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  function openThread(id: string | null) {
    if (!id) setTurns([]);
    const next = new URLSearchParams(params.toString());
    if (id) next.set("c", id);
    else next.delete("c");
    next.delete("model");
    const query = next.toString();
    router.replace(query ? `/?${query}` : "/");
  }

  async function refreshThreads() {
    try {
      setThreads(await api.conversations());
    } catch {
      // Listing is best-effort; a failed refresh must not interrupt the turn.
    }
  }

  async function send(text: string) {
    const content = text.trim();
    if (!content || busy) return;

    setTurns((current) => [
      ...current,
      { role: "user", content },
      { role: "assistant", content: "", streaming: true },
    ]);
    setDraft("");
    setError(null);
    setBusy(true);

    const controller = new AbortController();
    abortRef.current = controller;

    const updateLast = (update: (turn: Turn) => Turn) =>
      setTurns((current) =>
        current.map((turn, index) => (index === current.length - 1 ? update(turn) : turn)),
      );

    try {
      let activeId = conversationId;
      if (!activeId) {
        const created = await api.createConversation(
          selected === "auto" ? {} : { pinned_model: selected },
        );
        activeId = created.id;
        skipLoadRef.current = created.id;
        openThread(created.id);
      }

      await streamConversationMessage(
        activeId,
        { content, model: selected === "auto" ? undefined : selected },
        {
          onRouting: (routing) => updateLast((turn) => ({ ...turn, routing })),
          onDelta: (delta) =>
            updateLast((turn) => ({ ...turn, content: turn.content + delta })),
          onError: (streamError) => {
            setError(streamError);
            updateLast((turn) => ({ ...turn, streaming: false }));
          },
        },
        controller.signal,
      );
      await refreshThreads();
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) {
        setError({
          type: "unknown",
          code: "stream_failed",
          message: "The connection was interrupted.",
        });
      }
    } finally {
      updateLast((turn) => ({ ...turn, streaming: false }));
      setBusy(false);
      abortRef.current = null;
    }
  }

  async function stop() {
    abortRef.current?.abort();
    if (conversationId) {
      await api.cancelConversation(conversationId).catch(() => undefined);
    }
  }

  return (
    <div className="shell">
      <Topbar session={session} onSignOut={onSignOut} />

      <div className="workspace">
        <aside className="sidebar">
          <button className="primary sidebar-new" onClick={() => openThread(null)}>
            New chat
          </button>
          <ul className="thread-list">
            {threads.map((thread) => (
              <li key={thread.id}>
                <button
                  className={thread.id === conversationId ? "thread active" : "thread"}
                  onClick={() => openThread(thread.id)}
                >
                  {titleOf(thread)}
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <div className="main">
          <div className="conversation">
            <div className="conversation-inner">
              {turns.length === 0 && (
                <div className="empty">
                  <h1>What can Janus work on?</h1>
                  <p>
                    Ask anything. Janus picks the right model and tells you which one
                    answered. History is saved to this conversation.
                  </p>
                  <div className="empty-hints">
                    {SUGGESTIONS.map((suggestion) => (
                      <button
                        key={suggestion}
                        className="hint"
                        onClick={() => send(suggestion)}
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {turns.map((turn, index) => (
                <div className={`message ${turn.role}`} key={index}>
                  <span className="message-role">
                    {turn.role === "user" ? session.user.name ?? "You" : "Janus"}
                  </span>

                  {turn.routing && (
                    <div className="attribution">
                      <span className="badge">{turn.routing.model}</span>
                      {turn.routing.privacy !== "provider" && (
                        <span className="badge private">
                          {turn.routing.privacy === "local" ? "on this machine" : "private"}
                        </span>
                      )}
                      {turn.routing.fallback_used && (
                        <span className="badge fallback">fallback</span>
                      )}
                    </div>
                  )}

                  <div className={`message-body${turn.role === "assistant" ? " markdown-body" : ""}`}>
                    {turn.role === "assistant" ? (
                      <MarkdownBody content={turn.content} />
                    ) : (
                      turn.content
                    )}
                    {turn.streaming && <span className="cursor" />}
                  </div>

                  {turn.routing?.routing_explanation && !turn.streaming && (
                    <span className="explanation">{turn.routing.routing_explanation}</span>
                  )}
                </div>
              ))}

              {error && (
                <div className="error">
                  {error.message}
                  <span className="error-code">
                    {error.code}
                    {error.details?.hint ? ` — ${String(error.details.hint)}` : ""}
                  </span>
                </div>
              )}

              <div ref={bottomRef} />
            </div>
          </div>

          <div className="composer">
            <div className="composer-inner">
              <div className="composer-row">
                <textarea
                  value={draft}
                  placeholder="Ask Janus…"
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      send(draft);
                    }
                  }}
                  rows={1}
                />
                {busy ? (
                  <button className="ghost" onClick={stop}>
                    Stop
                  </button>
                ) : (
                  <button
                    className="primary"
                    onClick={() => send(draft)}
                    disabled={!draft.trim()}
                  >
                    Send
                  </button>
                )}
              </div>

              <div className="composer-meta">
                <select value={selected} onChange={(event) => setSelected(event.target.value)}>
                  <option value="auto">Auto — let Janus choose</option>
                  {models.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.displayName}
                      {model.verified ? "" : " (unverified metadata)"}
                    </option>
                  ))}
                </select>
                <span>Enter to send · Shift+Enter for a new line</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
