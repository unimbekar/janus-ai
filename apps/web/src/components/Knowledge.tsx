"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Topbar } from "@/components/Topbar";
import {
  api,
  type KnowledgeBase,
  type SearchHit,
  type SessionInfo,
} from "@/lib/api";

export function Knowledge({
  session,
  onSignOut,
}: {
  session: SessionInfo;
  onSignOut: () => void;
}) {
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState("Company handbook");
  const [title, setTitle] = useState("Overview");
  const [content, setContent] = useState(
    "Janus routes every model call through the gateway.\n\nAgents never talk to providers directly.",
  );
  const [query, setQuery] = useState("gateway");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const next = await api.knowledgeBases();
    setBases(next);
    if (!selectedId && next[0]) setSelectedId(next[0].id);
  }

  useEffect(() => {
    api
      .knowledgeBases()
      .then((next) => {
        setBases(next);
        if (next[0]) setSelectedId(next[0].id);
      })
      .catch(() => setError("Knowledge bases could not be loaded."));
  }, []);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createKnowledgeBase({ name });
      setSelectedId(created.id);
      await refresh();
    } catch {
      setError("Could not create the knowledge base.");
    } finally {
      setBusy(false);
    }
  }

  async function onIngest(event: FormEvent) {
    event.preventDefault();
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    try {
      await api.ingestDocument(selectedId, { title, content });
      await refresh();
    } catch {
      setError("Ingest failed. Duplicate content is rejected.");
    } finally {
      setBusy(false);
    }
  }

  async function onSearch(event: FormEvent) {
    event.preventDefault();
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    try {
      const body = await api.searchKnowledge(selectedId, query);
      setHits(body.data);
    } catch {
      setError("Search failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="shell">
      <Topbar session={session} onSignOut={onSignOut} />
      <div className="page">
        <div className="page-inner">
          <h1>Knowledge</h1>
          <p className="lede">
            Upload text, embed through the gateway, retrieve with pgvector. Answers
            that use this store can cite the chunks they relied on.
          </p>
          {error && <div className="error">{error}</div>}

          <div className="stack-grid">
            <form className="panel" onSubmit={onCreate}>
              <h2>New knowledge base</h2>
              <div className="field">
                <label htmlFor="kb-name">Name</label>
                <input
                  id="kb-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                />
              </div>
              <button className="primary" type="submit" disabled={busy}>
                Create
              </button>
              <ul className="item-list">
                {bases.map((base) => (
                  <li key={base.id}>
                    <button
                      type="button"
                      className={selectedId === base.id ? "item active" : "item"}
                      onClick={() => setSelectedId(base.id)}
                    >
                      <strong>{base.name}</strong>
                      <span className="badge">{base.document_count} docs</span>
                      <code>{base.embedding_model}</code>
                    </button>
                  </li>
                ))}
              </ul>
            </form>

            <div className="panel">
              <form onSubmit={onIngest}>
                <h2>Ingest text</h2>
                <div className="field">
                  <label htmlFor="doc-title">Title</label>
                  <input
                    id="doc-title"
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    required
                  />
                </div>
                <div className="field">
                  <label htmlFor="doc-content">Content</label>
                  <textarea
                    id="doc-content"
                    rows={8}
                    value={content}
                    onChange={(event) => setContent(event.target.value)}
                    required
                  />
                </div>
                <button className="primary" type="submit" disabled={!selectedId || busy}>
                  Ingest
                </button>
              </form>

              <form onSubmit={onSearch}>
                <h2>Search</h2>
                <div className="field">
                  <label htmlFor="kb-query">Query</label>
                  <input
                    id="kb-query"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    required
                  />
                </div>
                <button className="primary" type="submit" disabled={!selectedId || busy}>
                  Search
                </button>
              </form>

              <ul className="cite-list">
                {hits.map((hit) => (
                  <li key={hit.chunk_id}>
                    <span className="badge">{hit.score.toFixed(3)}</span> {hit.content}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
