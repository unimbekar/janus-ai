"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Topbar } from "@/components/Topbar";
import {
  ApiError,
  api,
  type KnowledgeBase,
  type SearchHit,
  type SessionInfo,
} from "@/lib/api";

const ACCEPT =
  ".txt,.text,.log,.md,.markdown,.csv,.json,.html,.htm,.pdf,.docx,text/plain,text/markdown,text/csv,text/html,application/json,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function uploadErrorMessage(caught: unknown): string {
  if (caught instanceof ApiError) {
    const items = caught.error.details?.errors;
    if (Array.isArray(items) && items.length > 0) {
      return items
        .map((item) => {
          if (item && typeof item === "object" && "filename" in item && "message" in item) {
            return `${String(item.filename)}: ${String(item.message)}`;
          }
          return null;
        })
        .filter((line): line is string => Boolean(line))
        .join(" ");
    }
    return caught.error.message;
  }
  return "Ingest failed. Duplicate content is rejected.";
}

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
  const [files, setFiles] = useState<File[]>([]);
  const [query, setQuery] = useState("gateway");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
    setNotice(null);
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
    setNotice(null);
    try {
      await api.ingestDocument(selectedId, { title, content });
      setNotice("Pasted text was ingested.");
      await refresh();
    } catch {
      setError("Ingest failed. Duplicate content is rejected.");
    } finally {
      setBusy(false);
    }
  }

  async function onUpload(event: FormEvent) {
    event.preventDefault();
    if (!selectedId || files.length === 0) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.ingestDocumentsFromFiles(selectedId, files);
      const ingested = result.data.length;
      const skipped = result.errors.length;
      const skipNote =
        skipped === 0
          ? ""
          : ` ${skipped} skipped: ${result.errors.map((item) => `${item.filename} (${item.message})`).join("; ")}`;
      setNotice(
        `Ingested ${ingested} file${ingested === 1 ? "" : "s"}.${skipNote}`,
      );
      setFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await refresh();
    } catch (caught) {
      setError(uploadErrorMessage(caught));
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
            Paste text or upload files. Janus chunks them, embeds through the
            gateway, and retrieves with pgvector. Chat and agents both use this
            store — ask a question in Chat and the answer is grounded in your
            documents.
          </p>
          {error && <div className="error">{error}</div>}
          {notice && <div className="notice">{notice}</div>}

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
              <form onSubmit={onUpload}>
                <h2>Upload files</h2>
                <p className="field-hint">
                  One or more .txt, .md, .csv, .json, .html, .pdf, or .docx files.
                  PDFs need a text layer (not a scan). Max 10 files, 8 MB each.
                </p>
                <div className="field">
                  <label htmlFor="kb-files">Files</label>
                  <input
                    id="kb-files"
                    ref={fileInputRef}
                    type="file"
                    accept={ACCEPT}
                    multiple
                    onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
                  />
                </div>
                {files.length > 0 && (
                  <ul className="file-list">
                    {files.map((file) => (
                      <li key={`${file.name}-${file.size}-${file.lastModified}`}>
                        {file.name}
                        <span className="muted">
                          {" "}
                          · {(file.size / 1024).toFixed(1)} KB
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                <button
                  className="primary"
                  type="submit"
                  disabled={!selectedId || busy || files.length === 0}
                >
                  Upload
                </button>
              </form>

              <form onSubmit={onIngest}>
                <h2>Or paste text</h2>
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
