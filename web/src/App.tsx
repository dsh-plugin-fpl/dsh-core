import { corePath, coreUrl, coreProduct } from "@platform/embedded";
import { ConnectedWorkshop } from "@features/creative-workshop/ConnectedWorkshop";
import { StoragePage } from "@features/storage/StoragePage";
import {ProductStorage} from '@features/storage/ProductStorage';
import { AccountsPage } from "@features/accounts/AccountsPage";
import { SetupAuthorizationPage } from "@features/accounts/SetupAuthorizationPage";
import { useCoreAccount } from "@platform/auth/AccountAccess";
import { LoadingState } from "@shared/ui/LoadingState";
import {
  useEffect,
  useState,
  useRef,
  useCallback,
  lazy,
  Suspense,
} from "react";
import { StudioShell } from "@app/layout/StudioSidebar";
import { ModelInfrastructureHeader } from "@app/layout/ModelInfrastructureHeader";
const ModelServicePage = lazy(() =>
  import("@features/models/catalog/ModelServicePage").then((m) => ({
    default: m.ModelServicePage,
  })),
);
const ModelTestPage = lazy(() =>
  import("@features/models/conversation/ModelTestPage").then((m) => ({
    default: m.ModelTestPage,
  })),
);
const ModelCallLogsPage = lazy(() =>
  import("@features/models/logs/ModelCallLogsPage").then((m) => ({
    default: m.ModelCallLogsPage,
  })),
);
const CodeAgentsPage = lazy(() =>
  import("@features/code-agents/CodeAgentsPage").then((m) => ({
    default: m.CodeAgentsPage,
  })),
);
import type { StudioPage } from "@app/routes";
import { Home } from "@features/home/CoreHome";
import { Files } from "@features/resources/ResourcesPage";
import { ApplicationsPage } from "@features/applications/ApplicationsPage";
import type { IntegrationKind } from "@features/integrations/IntegrationCenterPage";
const IntegrationCenterPage = lazy(() =>
  import("@features/integrations/IntegrationCenterPage").then((m) => ({
    default: m.IntegrationCenterPage,
  })),
);
import type { NavigationGuard } from "@features/integrations/interaction";

import "@app/layout/studio-shell.css";

const paths: Record<StudioPage, string> = {
  home: "/",
  "creative-workshop": "/creative-workshop",
  applications: "/apps",
  files: "/files",
  settings: "/integrations?kind=external-connections",
  integrations: "/integrations",
  "model-service": "/model-service",
  "code-agents": "/code-agents",
  "model-test": "/model-test",
  "model-call-logs": "/model-call-logs",
};
export function App() {
  const account = useCoreAccount();
  const [path, setPath] = useState(corePath());
  useEffect(() => {
    const sync = () => setPath(corePath());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  const integrationGuard = useRef<NavigationGuard | null>(null);
  const registerIntegrationGuard = useCallback(
    (guard: NavigationGuard | null) => {
      integrationGuard.current = guard;
    },
    [],
  );
  const navigate = (target: string) => {
    if (target === "/settings")
      target = "/integrations?kind=external-connections";
    const proceed = () => {
      const product = coreProduct();
      if (product && (target === '/apps' || target.startsWith('/apps/'))) {
        window.location.assign(product.projectsUrl);
        return;
      }
      history.pushState(null, "", coreUrl(target));
      setPath(target);
    };
    if (integrationGuard.current?.(proceed)) return;
    proceed();
  };
  const page = (
    path.split("?")[0].startsWith("/apps/")
      ? "applications"
      : (Object.entries(paths).find(([, p]) => p === path.split("?")[0])?.[0] ??
        "home")
  ) as StudioPage;
  const header = (actions: React.ReactNode) => (
    <ModelInfrastructureHeader
      page={page === "code-agents" ? "code-agents" : "model-service"}
      navigate={(p) => navigate(paths[p])}
      focusSelected={false}
      actions={actions}
    />
  );
  return (
    <StudioShell
      page={page}
      navigate={(p) => navigate(paths[p])}
      assistant={null}
    >
      <Suspense
        fallback={<LoadingState label="正在打开页面…" variant="region" />}
      >
        {path.split("?")[0] === "/setup/authorize" ? (
          <SetupAuthorizationPage />
        ) : path.split("?")[0] === "/storage" ? (
          coreProduct() ? <ProductStorage /> : <StoragePage />
        ) : path.split("?")[0] === "/account" ? (
          <AccountsPage />
        ) : ["/creative-workshop", "/fpl-account"].includes(
            path.split("?")[0],
          ) ? (
          <ConnectedWorkshop
            path={path}
            navigate={navigate}
            registerNavigationGuard={registerIntegrationGuard}
          />
        ) : page === "home" ? (
          <Home navigate={navigate} />
        ) : page === "model-service" ? (
          <ModelServicePage
            canWrite
            canTest
            renderHeader={header}
            onTestModel={(m) =>
              navigate("/model-test?model=" + encodeURIComponent(m.id))
            }
          />
        ) : page === "code-agents" ? (
          <CodeAgentsPage
            canManage={account.role === "admin"}
            scope={account.id}
            renderHeader={header}
          />
        ) : page === "model-test" ? (
          <ModelTestPage
            key={path}
            onInitialModelHandled={() =>
              history.replaceState(null, "", coreUrl("/model-test"))
            }
            initialConversationId={new URLSearchParams(path.split("?")[1]).get(
              "conversation",
            )}
            initialModelId={new URLSearchParams(path.split("?")[1]).get(
              "model",
            )}
            initialMode={
              new URLSearchParams(path.split("?")[1]).get("mode") || undefined
            }
          />
        ) : page === "model-call-logs" ? (
          <ModelCallLogsPage
            currentAccount={{ id: account.id, name: account.display_name }}
          />
        ) : page === "applications" ? (
          <ApplicationsPage
            navigate={navigate}
            selected={
              path.split("?")[0].startsWith("/apps/")
                ? decodeURIComponent(path.split("?")[0].slice(6))
                : undefined
            }
          />
        ) : page === "integrations" ? (
          <IntegrationCenterPage
            kind={
              (["tool-apis", "mcp", "skills", "external-connections"].includes(
                new URLSearchParams(path.split("?")[1]).get("kind") || "",
              )
                ? new URLSearchParams(path.split("?")[1]).get("kind")
                : "tool-apis") as IntegrationKind
            }
            onKindChange={(kind) => navigate("/integrations?kind=" + kind)}
            canManage
            registerNavigationGuard={registerIntegrationGuard}
          />
        ) : page === "files" ? (
          <Files />
        ) : (
          <Home navigate={navigate} />
        )}
      </Suspense>
    </StudioShell>
  );
}
