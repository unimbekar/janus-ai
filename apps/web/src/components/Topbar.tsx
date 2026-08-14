"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { SessionInfo } from "@/lib/api";

export function Topbar({
  session,
  onSignOut,
}: {
  session: SessionInfo;
  onSignOut: () => void;
}) {
  const pathname = usePathname();
  const onChat = pathname === "/";
  const onModels = pathname.startsWith("/models");
  const onAgents = pathname.startsWith("/agents");
  const onKnowledge = pathname.startsWith("/knowledge");
  const onUsage = pathname.startsWith("/usage") || pathname.startsWith("/deployments");

  return (
    <header className="topbar">
      <Link href="/" className="brand">
        <span className="brand-mark">J</span>
        <span>Janus</span>
      </Link>
      <nav className="nav">
        <Link href="/" className={onChat ? "active" : undefined}>
          Chat
        </Link>
        <Link href="/models" className={onModels ? "active" : undefined}>
          Models
        </Link>
        <Link href="/agents" className={onAgents ? "active" : undefined}>
          Agents
        </Link>
        <Link href="/knowledge" className={onKnowledge ? "active" : undefined}>
          Knowledge
        </Link>
        <Link href="/usage" className={onUsage ? "active" : undefined}>
          Usage
        </Link>
      </nav>
      <div className="topbar-spacer" />
      <div className="topbar-meta">
        <span>{session.organization.name}</span>
        <span className="badge">{session.organization.default_mode} mode</span>
        <button className="ghost" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </header>
  );
}
