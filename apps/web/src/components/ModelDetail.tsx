"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Topbar } from "@/components/Topbar";
import { ApiError, api, type ModelSummary, type SessionInfo } from "@/lib/api";

export function ModelDetail({
  modelId,
  session,
  onSignOut,
}: {
  modelId: string;
  session: SessionInfo;
  onSignOut: () => void;
}) {
  const [model, setModel] = useState<ModelSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .model(modelId)
      .then(setModel)
      .catch((caught) => {
        setError(
          caught instanceof ApiError ? caught.error.message : "This model is not available.",
        );
      });
  }, [modelId]);

  return (
    <div className="shell">
      <Topbar session={session} onSignOut={onSignOut} />
      <div className="page">
        <div className="page-inner narrow">
          <Link href="/models" className="back">
            ← All models
          </Link>

          {error && <div className="error">{error}</div>}

          {model && (
            <>
              <h1>{model.displayName}</h1>
              <code className="slug">{model.id}</code>
              <div className="attribution">
                <span className="badge">{model.tier}</span>
                <span className="badge">{model.privacy}</span>
                <span className="badge">{model.provider}</span>
                {model.verified ? (
                  <span className="badge private">verified</span>
                ) : (
                  <span className="badge">unverified metadata</span>
                )}
              </div>

              {model.notes && <p className="lede">{model.notes}</p>}

              <dl className="kv">
                <dt>Context</dt>
                <dd>{model.contextWindow.toLocaleString()} tokens</dd>
                <dt>Max output</dt>
                <dd>
                  {model.maxOutputTokens
                    ? `${model.maxOutputTokens.toLocaleString()} tokens`
                    : "—"}
                </dd>
                <dt>Cost class</dt>
                <dd>{model.costClass}</dd>
                <dt>Latency class</dt>
                <dd>{model.latencyClass}</dd>
                <dt>Languages</dt>
                <dd>{model.languages.join(", ") || "not declared"}</dd>
                <dt>Capabilities</dt>
                <dd>{model.capabilities.join(", ") || "—"}</dd>
              </dl>

              <h2>Deployments</h2>
              <ul className="deploy-list">
                {model.deployments.map((deployment) => (
                  <li key={deployment.key}>
                    <strong>{deployment.key}</strong>
                    <span className="badge">{deployment.privacy}</span>
                    <span className="badge">{deployment.availability}</span>
                    {deployment.region && <span className="badge">{deployment.region}</span>}
                  </li>
                ))}
              </ul>

              <Link href={`/?model=${encodeURIComponent(model.id)}`} className="primary chat-link">
                Chat with this model
              </Link>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
