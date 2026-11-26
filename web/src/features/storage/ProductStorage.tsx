import { useCoreAccount } from "@platform/auth/AccountAccess";
import { coreUrl } from "@platform/embedded";
import { useEffect, useState } from "react";

/** Product data moves together; the standalone Core migration UI is inapplicable. */
export function ProductStorage() {
  const account = useCoreAccount();
  const owner = account.legacy && account.role === "admin";
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!owner) return;
    void fetch("/v1/app/storage", { credentials: "include" })
      .then(async (response) => {
        if (!response.ok) throw new Error("无法读取存储位置");
        setPath((await response.json()).path);
      })
      .catch((error) => setError(String(error)));
  }, [owner]);
  return (
    <section className="page">
      <h1>设置</h1>
      <p>
        <a href={coreUrl("/legacy-files")}>旧文件整理</a>
      </p>
      <p>
        项目、素材和公共设置由本工坊统一管理。迁移数据前请退出工坊，并保留备份。
      </p>
      {owner &&
        (error ? (
          <p role="alert">{error}</p>
        ) : (
          <p>
            数据目录：<code>{path || "正在读取…"}</code>
          </p>
        ))}
    </section>
  );
}
