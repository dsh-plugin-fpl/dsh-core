import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchApiJson } from "@platform/api/client";
import { useCoreAccount } from "@platform/auth/AccountAccess";
import { Feedback } from "@shared/ui/RequestFeedback";
import { FolderOpen, HardDrive } from "lucide-react";
import "./storage.css";

import type { components } from "@generated/api";
type Status = components["schemas"]["StorageStatus"];
type Plan = components["schemas"]["StoragePlan"];
type Job = components["schemas"]["StorageJob"];
const size = (bytes: number) =>
  new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(
    bytes / 1024 ** 3,
  ) + " GB";
export function StoragePage() {
  const account = useCoreAccount(),
    cache = useQueryClient();
  const allowed = account.role === "admin" && account.legacy;
  const [target, setTarget] = useState<string | null>(null),
    [plan, setPlan] = useState<Plan | null>(null);
  const [includeLegacy, setIncludeLegacy] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const query = useQuery({
    queryKey: ["storage"],
    queryFn: () => fetchApiJson<Status>("/v1/storage"),
    enabled: allowed,
    refetchInterval: 3000,
    retry: false,
  });
  const status = query.data;
  const migrating = status?.jobs.some(
    (j) =>
      !["completed", "failed", "cancelled", "rolled_back"].includes(j.status),
  );
  async function action(start: boolean) {
    setBusy(true);
    setError("");
    try {
      if (start && plan) {
        await fetchApiJson<Job>("/v1/storage/migrations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ plan_id: plan.id }),
        });
        setPlan(null);
      } else if (status) {
        setPlan(
          await fetchApiJson<Plan>("/v1/storage/plans", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              target_root: target ?? status.root,
              expected_revision: status.revision,
              include_legacy:
                includeLegacy ??
                (status.legacy_source_configured && !status.legacy_imported),
            }),
          }),
        );
      }
      await cache.invalidateQueries({ queryKey: ["storage"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function jobAction(id: string, verb: "resume" | "cancel") {
    setBusy(true);
    setError("");
    try {
      await fetchApiJson<Job>(`/v1/storage/migrations/${id}/${verb}`, {
        method: "POST",
      });
      await cache.invalidateQueries({ queryKey: ["storage"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="page core-page storage-page">
      <header className="page-header">
        <div>
          <h1>存储与迁移</h1>
          <p>统一管理 Core 与工坊的数据目录。</p>
        </div>
        <HardDrive aria-hidden />
      </header>
      {!allowed ? (
        <p>只有本机所有者可以管理机器存储。你的文件仍按账号隔离保存。</p>
      ) : (
        <>
          <Feedback
            error={error || (!migrating ? query.error?.message : undefined)}
          />
          {status && (
            <>
              <article className="home-panel storage-panel">
                <h2>
                  <FolderOpen size={20} /> 数据位置
                </h2>
                <p>
                  {status.configured
                    ? "已启用统一存储"
                    : "当前数据仍在各服务的原运行目录"}
                </p>
                <label>
                  统一存储根目录
                  <input
                    value={target ?? status.root}
                    onChange={(e) => {
                      setTarget(e.target.value);
                      setPlan(null);
                    }}
                    disabled={busy || migrating}
                    spellCheck={false}
                  />
                </label>
                {status.legacy_source_configured && !status.legacy_imported && (
                  <label>
                    <input
                      type="checkbox"
                      checked={includeLegacy ?? !status.legacy_imported}
                      onChange={(e) => {
                        setIncludeLegacy(e.target.checked);
                        setPlan(null);
                      }}
                      disabled={busy || migrating}
                    />{" "}
                    同时导入已登记的旧 FPL 数据
                  </label>
                )}
                <p className="storage-hint">
                  Core
                  与每个工坊使用各自的子目录。迁移会保留原数据，并校验文件完整性。代码与开发环境仍保存在安装目录。
                </p>
                <button
                  className="btn primary"
                  disabled={
                    busy ||
                    migrating ||
                    (status.configured &&
                      (target ?? status.root) === status.root)
                  }
                  onClick={() => void action(false)}
                >
                  {busy ? "正在检查…" : "检查目录与迁移范围"}
                </button>
              </article>
              <article className="home-panel storage-panel">
                <h2>已接入的工坊</h2>
                <div className="storage-entries">
                  {status.participants.map((row) => (
                    <div key={row.id}>
                      <strong>{row.id}</strong>
                      <span>
                        {row.files} 个文件 · {size(row.bytes)}
                      </span>
                      <code>{row.root}</code>
                    </div>
                  ))}
                </div>
                {!status.participants.length && (
                  <p>尚未登记工坊存储。安装工坊后通过其维护入口登记。</p>
                )}
              </article>
              {plan && (
                <article className="home-panel storage-panel">
                  <h2>迁移预检</h2>
                  <p>
                    目标：<code>{plan.root}</code>
                  </p>
                  <p>
                    预计数据 {size(plan.required_bytes)}，可用空间{" "}
                    {size(plan.available_bytes)}。
                  </p>
                  <p>
                    {plan.legacy
                      ? "包含已登记旧 FPL 来源的导入。"
                      : "包含当前 Core 和已接入工坊的全部持久数据。"}
                  </p>
                  <p>
                    开始后会正常停止相关服务；完成后恢复连接。请先保存正在编辑的内容。
                  </p>
                  <button
                    className="btn primary"
                    disabled={busy || migrating}
                    onClick={() => void action(true)}
                  >
                    迁移数据并使用此目录
                  </button>
                  <button className="btn" onClick={() => setPlan(null)}>
                    返回修改
                  </button>
                </article>
              )}
              {status.jobs
                .filter((job) => job.status !== "completed")
                .map((job) => (
                  <article className="home-panel storage-panel" key={job.id}>
                    <h2>
                      {job.status === "rolled_back"
                        ? "已恢复原目录"
                        : "迁移进度"}
                    </h2>
                    <p role="status" aria-live="polite">
                      {job.message}
                    </p>
                    {job.status === "needs_recovery" && (
                      <button
                        className="btn primary"
                        disabled={busy}
                        onClick={() => void jobAction(job.id, "resume")}
                      >
                        恢复迁移
                      </button>
                    )}
                    {["queued", "running"].includes(job.status) && (
                      <button
                        className="btn"
                        disabled={busy}
                        onClick={() => void jobAction(job.id, "cancel")}
                      >
                        取消迁移并恢复原目录
                      </button>
                    )}
                  </article>
                ))}
              {migrating && (
                <p role="status">
                  迁移期间 Core
                  会重新启动，页面会自动恢复查询。请勿关闭或移走目标磁盘。
                </p>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
