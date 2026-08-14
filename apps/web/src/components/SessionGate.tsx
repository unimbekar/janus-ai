"use client";

import { type ReactNode, useEffect, useState } from "react";
import { SignIn } from "@/components/SignIn";
import { api, type SessionInfo } from "@/lib/api";

export function SessionGate({
  children,
}: {
  children: (session: SessionInfo, onSignOut: () => Promise<void>) => ReactNode;
}) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    api
      .session()
      .then(setSession)
      .catch(() => setSession(null))
      .finally(() => setChecking(false));
  }, []);

  if (checking) {
    return <div className="loading">Loading…</div>;
  }

  if (!session) {
    return <SignIn onSignedIn={setSession} />;
  }

  return (
    <>
      {children(session, async () => {
        await api.logout().catch(() => undefined);
        setSession(null);
      })}
    </>
  );
}
