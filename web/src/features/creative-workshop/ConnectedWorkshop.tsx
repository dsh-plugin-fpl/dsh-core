import { useEffect, useState } from "react";
import { CreativeWorkshopPage } from "./CreativeWorkshopPage";
import { WebsiteAccount, useWebsite } from "./WebsiteAccount";
import type { Mode } from "./shared";
import type { Guard } from "@shared/ui/navigationGuards";

export function ConnectedWorkshop({
  path,
  navigate,
  registerNavigationGuard,
}: {
  path: string;
  navigate: (path: string) => void;
  registerNavigationGuard: (guard: Guard | null) => void;
}) {
  const status = useWebsite();
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    const update = () => setGeneration((x) => x + 1);
    window.addEventListener("fpl:account-changed", update);
    return () => window.removeEventListener("fpl:account-changed", update);
  }, []);
  if (
    path.split("?")[0] === "/fpl-account" ||
    !status.data?.account ||
    status.data.must_change_password
  )
    return <WebsiteAccount />;
  const params = new URLSearchParams(path.split("?")[1]);
  const mode = params.get("mode") || "creative-workshop";
  const modes = [
    "creative-workshop",
    "creative-workshop-mine",
    "creative-workshop-new",
    "creative-workshop-editor",
    "creative-workshop-detail",
  ];
  return (
    <CreativeWorkshopPage
      key={`${status.data.site}:${status.data.account.id}:${generation}`}
      mode={(modes.includes(mode) ? mode : "creative-workshop") as Mode}
      itemId={params.get("id") || undefined}
      canWrite={status.data.permissions.includes("content.write")}
      canInstall={status.data.permissions.includes("configuration.manage")}
      navigate={(next, id) =>
        navigate(
          "/creative-workshop?" +
            new URLSearchParams({ mode: next, ...(id ? { id } : {}) }),
        )
      }
      registerNavigationGuard={registerNavigationGuard}
    />
  );
}
