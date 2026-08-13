"use client";

import { useState } from "react";
import { ApiError, api, type SessionInfo } from "@/lib/api";

const MIN_PASSWORD_LENGTH = 12;

export function SignIn({ onSignedIn }: { onSignedIn: (session: SessionInfo) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const registering = mode === "register";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = registering
        ? await api.register({
            email,
            password,
            name: name || undefined,
            organization_name: organizationName || undefined,
          })
        : await api.login({ email, password });
      onSignedIn(session);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.error.message : "Could not reach Janus.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <form className="auth-card" onSubmit={submit}>
        <div className="brand">
          <span className="brand-mark">J</span>
          <span>Janus Intelligence</span>
        </div>

        <h1>{registering ? "Create your workspace" : "Sign in"}</h1>
        <p className="subtitle">
          {registering
            ? "One account, every model — frontier cloud or private."
            : "Welcome back."}
        </p>

        {registering && (
          <>
            <div className="field">
              <label htmlFor="name">Your name</label>
              <input
                id="name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                autoComplete="name"
              />
            </div>
            <div className="field">
              <label htmlFor="organization">Workspace name</label>
              <input
                id="organization"
                value={organizationName}
                onChange={(event) => setOrganizationName(event.target.value)}
                placeholder="Acme Corp"
              />
            </div>
          </>
        )}

        <div className="field">
          <label htmlFor="email">Work email</label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="email"
          />
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            required
            minLength={registering ? MIN_PASSWORD_LENGTH : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={registering ? "new-password" : "current-password"}
          />
          {registering && (
            <span className="field-hint">
              At least {MIN_PASSWORD_LENGTH} characters. A passphrase is easier to
              remember and harder to guess.
            </span>
          )}
        </div>

        {error && <div className="error">{error}</div>}

        <div className="auth-actions">
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "Working…" : registering ? "Create workspace" : "Sign in"}
          </button>
          <button
            type="button"
            className="switch"
            onClick={() => {
              setMode(registering ? "login" : "register");
              setError(null);
            }}
          >
            {registering ? "I already have an account" : "Create a new workspace"}
          </button>
        </div>
      </form>
    </div>
  );
}
