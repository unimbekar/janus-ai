"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Topbar } from "@/components/Topbar";
import {
  api,
  type AgentRun,
  type AgentSummary,
  type KnowledgeBase,
  type SessionInfo,
} from "@/lib/api";

export function Agents({
  session,
  onSignOut,
}: {
  session: SessionInfo;
  onSignOut: () => void;
}) {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [name, setName] = useState("Research assistant");
  const [slug, setSlug] = useState("research-assistant");
  const [instructions, setInstructions] = useState(
    "Use retrieved context when provided. Never invent citations.",
  );
  const [knowledgeBaseId, setKnowledgeBaseId] = useState("");
  const [prompt, setPrompt] = useState("What does our knowledge say?");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const [nextAgents, nextBases] = await Promise.all([api.agents(), api.knowledgeBases()]);
    setAgents(nextAgents);
    setBases(nextBases);
    if (!knowledgeBaseId && nextBases[0]) setKnowledgeBaseId(nextBases[0].id);
    if (!selectedId && nextAgents[0]) setSelectedId(nextAgents[0].id);
  }

  useEffect(() => {
    Promise.all([api.agents(), api.knowledgeBases()])
      .then(([nextAgents, nextBases]) => {
        setAgents(nextAgents);
        setBases(nextBases);
        if (nextBases[0]) setKnowledgeBaseId(nextBases[0].id);
        if (nextAgents[0]) setSelectedId(nextAgents[0].id);
      })
      .catch(() => setError("Agents could not be loaded."));
  }, []);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createAgent({
        name,
        slug,
        instructions,
        knowledge_base_ids: knowledgeBaseId ? [knowledgeBaseId] : [],
        tools: knowledgeBaseId ? ["knowledge_search", "clock"] : ["clock"],
      });
      setSelectedId(created.id);
      await refresh();
    } catch {
      setError("Could not create the agent. Check the slug is unique and lowercase.");
    } finally {
      setBusy(false);
    }
  }

  async function onPublish() {
    if (!selectedId) return;
    setBusy(true);
    try {
      await api.publishAgent(selectedId);
      await refresh();
    } catch {
      setError("Publish failed.");
    } finally {
      setBusy(false);
    }
  }

  async function onRun(event: FormEvent) {
    event.preventDefault();
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    try {
      setRun(await api.runAgent(selectedId, prompt));
    } catch {
      setError("The agent run failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="shell">
      <Topbar session={session} onSignOut={onSignOut} />
      <div className="page">
        <div className="page-inner">
          <h1>Agents</h1>
          <p className="lede">
            Versioned agents with tools and knowledge. Model calls always go through
            the gateway — never a provider SDK.
          </p>
          {error && <div className="error">{error}</div>}

          <div className="stack-grid">
            <form className="panel" onSubmit={onCreate}>
              <h2>Create</h2>
              <div className="field">
                <label htmlFor="agent-name">Name</label>
                <input
                  id="agent-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="agent-slug">Slug</label>
                <input
                  id="agent-slug"
                  value={slug}
                  onChange={(event) => setSlug(event.target.value)}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="agent-instructions">Instructions</label>
                <textarea
                  id="agent-instructions"
                  rows={4}
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="agent-kb">Knowledge base</label>
                <select
                  id="agent-kb"
                  value={knowledgeBaseId}
                  onChange={(event) => setKnowledgeBaseId(event.target.value)}
                >
                  <option value="">None</option>
                  {bases.map((base) => (
                    <option key={base.id} value={base.id}>
                      {base.name}
                    </option>
                  ))}
                </select>
              </div>
              <button className="primary" type="submit" disabled={busy}>
                Create draft
              </button>
            </form>

            <div className="panel">
              <h2>Your agents</h2>
              <ul className="item-list">
                {agents.map((agent) => (
                  <li key={agent.id}>
                    <button
                      type="button"
                      className={selectedId === agent.id ? "item active" : "item"}
                      onClick={() => setSelectedId(agent.id)}
                    >
                      <strong>{agent.name}</strong>
                      <span className="badge">{agent.status}</span>
                      <code>{agent.slug}</code>
                    </button>
                  </li>
                ))}
                {agents.length === 0 && <li className="muted">No agents yet.</li>}
              </ul>
              <div className="row-actions">
                <button className="primary" type="button" disabled={!selectedId || busy} onClick={onPublish}>
                  Publish
                </button>
              </div>
              <form onSubmit={onRun}>
                <div className="field">
                  <label htmlFor="agent-prompt">Run input</label>
                  <textarea
                    id="agent-prompt"
                    rows={3}
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                    required
                  />
                </div>
                <button className="primary" type="submit" disabled={!selectedId || busy}>
                  Run
                </button>
              </form>
              {run && (
                <div className="result">
                  <div className="attribution">
                    <span className="badge">{run.status}</span>
                    <span className="badge">{run.step_count} steps</span>
                  </div>
                  <pre>{run.output || "(no output)"}</pre>
                  {run.citations.length > 0 && (
                    <ul className="cite-list">
                      {run.citations.map((citation, index) => (
                        <li key={`${citation.quote}-${index}`}>
                          {citation.quote}
                          {citation.score != null && (
                            <span className="muted"> · {citation.score.toFixed(3)}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
