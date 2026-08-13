"use client";

import { useEffect, useState } from "react";
import { Chat } from "@/components/Chat";
import { SignIn } from "@/components/SignIn";
import { api, type SessionInfo } from "@/lib/api";

export default function HomePage() {
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
    <Chat
      session={session}
      onSignOut={async () => {
        await api.logout().catch(() => undefined);
        setSession(null);
      }}
    />
  );
}
