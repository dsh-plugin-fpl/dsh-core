import { ThemeToggle } from "@shared/ui/ThemeToggle";
import { useQuery } from "@tanstack/react-query";
import { fetchApiJson } from "@platform/api/client";
import { Network, UserRound } from "lucide-react";
import "./studio-global-header.css";
export function StudioGlobalHeader(_: { assistant?: unknown }) {
  const q = useQuery({
    queryKey: ["bridges"],
    queryFn: () =>
      fetchApiJson<{ items: { connected: boolean }[] }>("/v1/bridges"),
    refetchInterval: 3000,
  });
  const connected = q.data?.items.some((b) => b.connected);
  return (
    <header className="studio-panel studio-global-header">
      <div className="studio-header-start">
        <div className="studio-header-theme">
          <span>外观</span>
          <ThemeToggle />
        </div>
      </div>
      <div className="studio-header-actions">
        <a
          href="/integrations?kind=external-connections"
          className="studio-header-chip"
        >
          <Network size={16} />
          <span>{connected ? "DSH 已连接" : "连接 DSH"}</span>
        </a>
        <span className="studio-header-chip">
          <UserRound size={16} /> 本机所有者
        </span>
      </div>
    </header>
  );
}
