import { useCoreAccount } from "@platform/auth/AccountAccess";
import { requestApi } from "@platform/api/client";
import { ThemeToggle } from "@shared/ui/ThemeToggle";
import { useQuery } from "@tanstack/react-query";
import { fetchApiJson } from "@platform/api/client";
import { HardDrive, Network, UserRound } from "lucide-react";
import "./studio-global-header.css";
export function StudioGlobalHeader(_: { assistant?: unknown }) {
  const profile = useCoreAccount();
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
        {profile.legacy && profile.role === "admin" && (
          <a className="studio-header-chip" href="/storage">
            <HardDrive size={16} />
            存储
          </a>
        )}
        <a
          href="/integrations?kind=external-connections"
          className="studio-header-chip"
        >
          <Network size={16} />
          <span>{connected ? "DSH 已连接" : "连接 DSH"}</span>
        </a>
        <a className="studio-header-chip" href="/account">
          <UserRound size={16} />
          {profile.display_name}
        </a>
        <button
          className="studio-header-chip"
          onClick={() =>
            void requestApi("/v1/identity/logout", { method: "POST" }).then(
              () => location.assign("/login"),
            )
          }
        >
          退出
        </button>
      </div>
    </header>
  );
}
