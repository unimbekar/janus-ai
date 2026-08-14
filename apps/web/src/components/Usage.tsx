"use client";

import { useEffect, useState } from "react";
import { Topbar } from "@/components/Topbar";
import {
  api,
  type DeploymentRow,
  type SessionInfo,
  type UsageSummary,
} from "@/lib/api";

export function Usage({
  session,
  onSignOut,
}: {
  session: SessionInfo;
  onSignOut: () => void;
}) {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [deployments, setDeployments] = useState<DeploymentRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.usage(), api.deployments()])
      .then(([nextUsage, nextDeployments]) => {
        setUsage(nextUsage);
        setDeployments(nextDeployments);
      })
      .catch(() => setError("Usage and deployment health could not be loaded."));
  }, []);

  return (
    <div className="shell">
      <Topbar session={session} onSignOut={onSignOut} />
      <div className="page">
        <div className="page-inner">
          <h1>Usage & deployments</h1>
          <p className="lede">
            Organization totals from telemetry, and the deployments this workspace
            can see — without endpoints or credentials.
          </p>
          {error && <div className="error">{error}</div>}

          {usage && (
            <div className="stat-grid">
              <div className="stat">
                <span className="muted">Requests</span>
                <strong>{usage.requests}</strong>
              </div>
              <div className="stat">
                <span className="muted">Input tokens</span>
                <strong>{usage.input_tokens}</strong>
              </div>
              <div className="stat">
                <span className="muted">Output tokens</span>
                <strong>{usage.output_tokens}</strong>
              </div>
              <div className="stat">
                <span className="muted">Cost (USD)</span>
                <strong>{usage.cost_usd}</strong>
              </div>
            </div>
          )}

          <h2>Deployments</h2>
          <table className="table">
            <thead>
              <tr>
                <th>Model</th>
                <th>Key</th>
                <th>Privacy</th>
                <th>Availability</th>
                <th>Accelerator</th>
                <th>Region</th>
              </tr>
            </thead>
            <tbody>
              {deployments.map((row) => (
                <tr key={`${row.model}-${row.key}`}>
                  <td>
                    <code>{row.model}</code>
                  </td>
                  <td>{row.key}</td>
                  <td>
                    <span className="badge">{row.privacy}</span>
                  </td>
                  <td>{row.availability}</td>
                  <td>{row.accelerator || "—"}</td>
                  <td>{row.region || "—"}</td>
                </tr>
              ))}
              {deployments.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    No deployments visible under the current policy.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
