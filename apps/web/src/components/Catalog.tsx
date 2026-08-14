"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Topbar } from "@/components/Topbar";
import { api, type ModelSummary, type SessionInfo } from "@/lib/api";

export function Catalog({
  session,
  onSignOut,
}: {
  session: SessionInfo;
  onSignOut: () => void;
}) {
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .models()
      .then(setModels)
      .catch(() => setError("The model catalog could not be loaded."));
  }, []);

  return (
    <div className="shell">
      <Topbar session={session} onSignOut={onSignOut} />
      <div className="page">
        <div className="page-inner">
          <h1>Models</h1>
          <p className="lede">
            Everything this workspace is allowed to use. Auto picks from this
            list; pinning a model still goes through the same policy.
          </p>

          {error && <div className="error">{error}</div>}

          <div className="model-grid">
            {models.map((model) => (
              <Link key={model.id} href={`/models/${model.id}`} className="model-card">
                <div className="model-card-head">
                  <strong>{model.displayName}</strong>
                  <span className="badge">{model.tier}</span>
                </div>
                <code>{model.id}</code>
                <div className="attribution">
                  <span className="badge">{model.privacy}</span>
                  {model.verified ? (
                    <span className="badge private">verified</span>
                  ) : (
                    <span className="badge">unverified metadata</span>
                  )}
                  <span className="badge">{model.costClass}</span>
                </div>
                <p className="model-caps">
                  {model.capabilities.slice(0, 6).join(" · ") || "text"}
                </p>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
