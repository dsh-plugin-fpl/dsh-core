import {coreUrl, coreProduct} from "@platform/embedded";
import { useCoreAccount } from "@platform/auth/AccountAccess";
import { ThemeToggle } from "@shared/ui/ThemeToggle";
import { useQuery } from "@tanstack/react-query";
import { fetchBridges, isDshConnection } from "@features/connections/api";
import { HardDrive, Network, UserRound } from "lucide-react";
import "./studio-global-header.css";
function StandaloneGlobalHeader(_: { assistant?: unknown }) {
  const profile = useCoreAccount();
  const q = useQuery({
    queryKey: ["bridges"],
    queryFn: fetchBridges,
    refetchInterval: 3000,
  });
  const connected = q.data?.items.some(
    (b) => isDshConnection(b) && b.connected,
  );
  return (
    <header className="studio-panel studio-global-header">
      <div className="studio-header-start">
        {coreProduct() && <a className="studio-header-chip" href={coreProduct()!.projectsUrl}>{coreProduct()!.name}</a>}
        <div className="studio-header-theme">
          <span>外观</span>
          <ThemeToggle />
        </div>
      </div>
      <div className="studio-header-actions">
        {(coreProduct() || (profile.legacy && profile.role === "admin")) && (
          <a className="studio-header-chip" href={coreUrl("/storage")}>
            <HardDrive size={16} />
            {coreProduct() ? "设置" : "存储"}
          </a>
        )}
        <a
          href={coreUrl("/integrations?kind=external-connections")}
          className="studio-header-chip"
        >
          <Network size={16} />
          <span>{connected ? "DSH 已连接" : "连接 DSH"}</span>
        </a>
        <a className="studio-header-chip" href={coreUrl("/account")}>
          <UserRound size={16} />
          {profile.display_name}
        </a>
      </div>
    </header>
  );
}

export function StudioGlobalHeader(props: {assistant?: unknown}) {
  return coreProduct() ? <header className="studio-panel studio-global-header"><div className="studio-header-start"><ThemeToggle /></div></header> : <StandaloneGlobalHeader {...props}/>;
}
