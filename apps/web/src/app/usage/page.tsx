"use client";

import { Usage } from "@/components/Usage";
import { SessionGate } from "@/components/SessionGate";

export default function UsagePage() {
  return (
    <SessionGate>
      {(session, onSignOut) => <Usage session={session} onSignOut={onSignOut} />}
    </SessionGate>
  );
}
