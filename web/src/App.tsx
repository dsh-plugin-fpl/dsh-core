import { StoragePage } from "@features/storage/StoragePage";
import { AccountsPage } from "@features/accounts/AccountsPage";
import { useCoreAccount } from "@platform/auth/AccountAccess";
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
import { Tasks } from "@features/tasks/TasksPage";
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
  applications: "/apps",
  files: "/files",
  tasks: "/tasks",
  settings: "/integrations?kind=external-connections",
  integrations: "/integrations",
  "model-service": "/model-service",
  "code-agents": "/code-agents",
  "model-test": "/model-test",
  "model-call-logs": "/model-call-logs",
};
export function App() {
  const account = useCoreAccount();
  const [path, setPath] = useState(location.pathname + location.search);
  useEffect(() => {
    const sync = () => setPath(location.pathname + location.search);
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
      history.pushState(null, "", target);
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
      <Suspense fallback={<p className="page-loading">正在打开页面…</p>}>
        {path.split("?")[0] === "/storage" ? (
          <StoragePage />
        ) : path.split("?")[0] === "/account" ? (
          <AccountsPage />
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
              history.replaceState(null, "", "/model-test")
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
        ) : page === "tasks" ? (
          <Tasks />
        ) : (
          <Home navigate={navigate} />
        )}
      </Suspense>
    </StudioShell>
  );
}
