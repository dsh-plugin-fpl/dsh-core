import { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  CoreOwnerAccess,
  type CoreProfile,
} from "@platform/auth/AccountAccess";
import { configureCorePlatform, type CorePlatform } from "@platform/embedded";
import { fetchApiJson } from "@platform/api/client";
import { App } from "./App";
import {
  HomeExtension,
  type CoreWorkspaceProps,
} from "./features/home/homeExtension";
export type {
  CoreHomeProps,
  CoreWorkspaceProps,
} from "./features/home/homeExtension";
import "@shared/ui/theme.css";
import "@app/styles.css";
import "./core.css";

/** Configure once before mounting in a product window. */
export { configureCorePlatform };
export type { CorePlatform };
export function CoreWorkspace({ home }: CoreWorkspaceProps = {}) {
  const [client] = useState(() => new QueryClient());
  const [identity, setIdentity] = useState<{
    account: CoreProfile;
    permissions: string[];
  } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    void fetchApiJson<{ account: CoreProfile; permissions: string[] }>(
      "/v1/identity/me",
    )
      .then(setIdentity)
      .catch((e) => setError(String(e)));
  }, []);
  if (error) return <p role="alert">{error}</p>;
  if (!identity) return <p role="status">正在打开公共能力设置…</p>;
  return (
    <QueryClientProvider client={client}>
      <CoreOwnerAccess
        profile={identity.account}
        permissions={identity.permissions}
      >
        <HomeExtension.Provider
          value={
            home
              ? {
                  home,
                  account: {
                    id: identity.account.id,
                    permissions: identity.permissions,
                  },
                }
              : null
          }
        >
          <App />
        </HomeExtension.Provider>
      </CoreOwnerAccess>
    </QueryClientProvider>
  );
}

export { WorkshopHomeFrame } from "./features/home/WorkshopHomeFrame";
export type { WorkshopHomeFrameProps } from "./features/home/WorkshopHomeFrame";
