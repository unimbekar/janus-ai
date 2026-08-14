"use client";

import { Knowledge } from "@/components/Knowledge";
import { SessionGate } from "@/components/SessionGate";

export default function KnowledgePage() {
  return (
    <SessionGate>
      {(session, onSignOut) => <Knowledge session={session} onSignOut={onSignOut} />}
    </SessionGate>
  );
}
