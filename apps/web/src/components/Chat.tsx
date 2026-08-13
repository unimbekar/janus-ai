"use client";

import { useEffect, useRef, useState } from "react";
import {
  type JanusError,
  type Message,
  type ModelSummary,
  type RoutingInfo,
  type SessionInfo,
  api,
  streamChat,
} from "@/lib/api";

const SUGGESTIONS = [
  "Summarize this contract and flag unusual terms",
  "Write a Python script to reconcile two CSV exports",
  "Draft a customer follow-up email",
];

interface Turn extends Message {
  routing?: RoutingInfo;
  streaming?: boolean;
}

export function Chat({
  session,
  onSignOut,
}: {
  session: SessionInfo;
  onSignOut: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [selected, setSelected] = useState("auto");
  const [error, setError] = useState<JanusError | null>(null);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    api.models().then(setModels).catch(() => setModels([]));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  async function send(text: string) {
    const content = text.trim();
    if (!content || busy) return;

    const history: Message[] = [
      ...turns.map(({ role, content: turnContent }) => ({ role, content: turnContent })),
      { role: "user" as const, content },
    ];

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
      await streamChat(
        { model: selected, messages: history },
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

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">J</span>
          <span>Janus</span>
        </div>
        <div className="topbar-spacer" />
        <div className="topbar-meta">
          <span>{session.organization.name}</span>
          <span className="badge">{session.organization.default_mode} mode</span>
          <button className="ghost" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </header>

      <div className="conversation">
        <div className="conversation-inner">
          {turns.length === 0 && (
            <div className="empty">
              <h1>What can Janus work on?</h1>
              <p>
                Ask anything. Janus picks the right model and tells you which one
                answered.
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

              <div className="message-body">
                {turn.content}
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
              <button className="ghost" onClick={() => abortRef.current?.abort()}>
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
  );
}
