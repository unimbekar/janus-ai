"use client";

import { useParams } from "next/navigation";
import { ModelDetail } from "@/components/ModelDetail";
import { SessionGate } from "@/components/SessionGate";

export default function ModelDetailPage() {
  const params = useParams<{ id: string[] }>();
  const modelId = (params.id ?? []).join("/");

  return (
    <SessionGate>
      {(session, onSignOut) => (
        <ModelDetail modelId={modelId} session={session} onSignOut={onSignOut} />
      )}
    </SessionGate>
  );
}
