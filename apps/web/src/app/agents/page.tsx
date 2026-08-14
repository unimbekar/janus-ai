"use client";

import { Agents } from "@/components/Agents";
import { SessionGate } from "@/components/SessionGate";

export default function AgentsPage() {
  return (
    <SessionGate>
      {(session, onSignOut) => <Agents session={session} onSignOut={onSignOut} />}
    </SessionGate>
  );
}
