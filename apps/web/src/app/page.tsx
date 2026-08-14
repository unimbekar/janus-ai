"use client";

import { Suspense } from "react";
import { Chat } from "@/components/Chat";
import { SessionGate } from "@/components/SessionGate";

export default function HomePage() {
  return (
    <SessionGate>
      {(session, onSignOut) => (
        <Suspense fallback={<div className="loading">Loading…</div>}>
          <Chat session={session} onSignOut={onSignOut} />
        </Suspense>
      )}
    </SessionGate>
  );
}
