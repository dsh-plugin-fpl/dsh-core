import { useAccountPermission } from "@platform/auth/AccountAccess";
import { coreProduct } from "@platform/embedded";
import { SharedFileBrowser } from "./FileBrowser";

export function ProductLibrary() {
  const writable = useAccountPermission("content.write");
  const adapter = coreProduct()?.fileBrowser;
  const project =
    new URLSearchParams(location.search).get("project") || undefined;
  if (!adapter)
    return (
      <section role="alert">
        本工坊尚未配置文件管理接口，请更新应用后重试。
      </section>
    );
  return (
    <SharedFileBrowser
      adapter={adapter}
      writable={writable}
      projectId={project}
      onProjectChange={(id) => {
        const url = new URL(location.href);
        if (id) url.searchParams.set("project", id);
        else url.searchParams.delete("project");
        history.replaceState(null, "", url);
      }}
    />
  );
}
