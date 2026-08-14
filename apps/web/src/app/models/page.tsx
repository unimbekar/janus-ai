"use client";

import { Catalog } from "@/components/Catalog";
import { SessionGate } from "@/components/SessionGate";

export default function ModelsPage() {
  return (
    <SessionGate>
      {(session, onSignOut) => <Catalog session={session} onSignOut={onSignOut} />}
    </SessionGate>
  );
}
