import type { components } from "@generated/api";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchApiJson } from "@platform/api/client";
import { IntegrationModal, PanelMessage } from "./components";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  History,
  ShieldCheck,
} from "lucide-react";
import { CustomSelect } from "@shared/ui/CustomSelect";
type Direction = "from-dsh" | "to-dsh";
type Adapter = components["schemas"]["AdapterOut"];
type Candidate = components["schemas"]["IntegrationSummary"];
type Row = components["schemas"]["TransferRow"];
type Plan = components["schemas"]["TransferOut"];
const kindNames: Record<string, string> = {
  "tool-apis": "工具 API",
  mcp: "MCP",
  skills: "Skill",
  "external-connections": "外部连接",
};
const post = <T,>(path: string, body: unknown) =>
  fetchApiJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
const LAST = "fpl-core-last-integration-transfer";
const readLast = () => {
  try {
    return localStorage.getItem(LAST);
  } catch {
    return null;
  }
};
const saveLast = (id: string) => {
  try {
    localStorage.setItem(LAST, id);
  } catch {}
};
export function TransferToolbar() {
  const [direction, setDirection] = useState<Direction | null>(null),
    [resume, setResume] = useState<string | null>(null);
  return (
    <div className="integration-transfer-actions">
      <button className="btn" onClick={() => setDirection("from-dsh")}>
        <ArrowDownToLine size={15} />从 DSH 导入
      </button>
      <button className="btn" onClick={() => setDirection("to-dsh")}>
        <ArrowUpFromLine size={15} />
        导入到 DSH
      </button>
      <button
        className="btn"
        aria-label="查看上次导入"
        onClick={() => {
          const id = readLast();
          if (id) setResume(id);
          else setDirection("from-dsh");
        }}
      >
        <History size={15} />
      </button>
      {(direction || resume) && (
        <TransferDialog
          direction={direction || "from-dsh"}
          resume={resume}
          close={() => {
            setDirection(null);
            setResume(null);
          }}
        />
      )}
    </div>
  );
}
function TransferDialog({
  direction,
  resume,
  close,
}: {
  direction: Direction;
  resume: string | null;
  close: () => void;
}) {
  const cache = useQueryClient();
  const adapters = useQuery({
    queryKey: ["integration-dsh-adapters"],
    queryFn: () =>
      fetchApiJson<{ items: Adapter[] }>("/v1/integrations/dsh-adapters"),
    refetchInterval: 3000,
  });
  const [selection, setSelection] = useState(""),
    [selected, setSelected] = useState<Set<string>>(new Set()),
    [plan, setPlan] = useState<Plan | null>(null),
    [choices, setChoices] = useState<Record<string, Row["action"]>>({}),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const available = (adapters.data?.items || []).filter((a) =>
    direction === "to-dsh" ? a.can_import : a.can_export,
  );
  const adapter =
    available.find((a) => a.bridge_id + "/" + a.id === selection) ||
    available[0];
  const candidates = useQuery({
    queryKey: [
      "integration-transfer-candidates",
      direction,
      adapter?.bridge_id,
      adapter?.id,
    ],
    enabled: !!adapter && !plan,
    queryFn: () =>
      fetchApiJson<{ items: Candidate[] }>(
        "/v1/integrations/transfer-candidates?" +
          new URLSearchParams({
            direction,
            bridge_id: adapter!.bridge_id,
            adapter_id: adapter!.id,
          }),
      ),
  });
  const setPreview = (value: Plan) => {
    setPlan(value);
    setChoices(
      Object.fromEntries(
        value.rows.map((row) => [
          row.key,
          (row.applied_action || row.action) as Row["action"],
        ]),
      ),
    );
    saveLast(value.id);
  };
  useEffect(() => {
    if (resume)
      fetchApiJson<Plan>(
        "/v1/integrations/transfers/" + encodeURIComponent(resume),
      )
        .then(setPreview)
        .catch((e) => setError(e.message));
  }, [resume]);
  const preview = async () => {
    if (!adapter) return;
    setBusy(true);
    setError("");
    try {
      const items = (candidates.data?.items || [])
        .filter((row) => selected.has(row.kind + "/" + row.id))
        .map(({ kind, id }) => ({ kind, id }));
      setPreview(
        await post<Plan>("/v1/integrations/transfers/preview", {
          direction,
          bridge_id: adapter.bridge_id,
          adapter_id: adapter.id,
          items,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const apply = async () => {
    if (!plan) return;
    setBusy(true);
    setError("");
    try {
      const result = await post<Plan>(
        "/v1/integrations/transfers/" + plan.id + "/apply",
        {
          choices: plan.rows.map((row) => ({
            key: row.key,
            action: choices[row.key],
          })),
        },
      );
      setPlan(result);
      await cache.invalidateQueries({ queryKey: ["integrations"] });
      await cache.invalidateQueries({ queryKey: ["core-tasks"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <IntegrationModal
      title={direction === "from-dsh" ? "从 DSH 导入配置" : "导入配置到 DSH"}
      onClose={close}
      wide
      protect
      pending={busy}
    >
      <div className="integration-transfer-body">
        <p className="integration-transfer-intro">
          两边分别保存配置副本。后续修改不会自动覆盖另一端，再次导入时可预览差异。
        </p>
        {(error || adapters.error?.message || candidates.error?.message) && (
          <PanelMessage>
            <p role="alert">
              {error || adapters.error?.message || candidates.error?.message}
            </p>
          </PanelMessage>
        )}
        {!plan ? (
          <>
            <label className="integration-field full">
              <span>{direction === "from-dsh" ? "来源插件" : "接收插件"}</span>
              <CustomSelect
                ariaLabel="DSH 集成适配器"
                value={adapter ? adapter.bridge_id + "/" + adapter.id : ""}
                onChange={(value) => {
                  setSelection(value);
                  setSelected(new Set());
                }}
                options={available.map((a) => ({
                  value: a.bridge_id + "/" + a.id,
                  label: a.bridge_name + " · " + a.name,
                }))}
              />
            </label>
            {!available.length ? (
              <div className="integration-empty">
                <strong>没有可用的 DSH 集成适配器</strong>
                <p>
                  连接 DSH 并启用新版 FPL Core
                  连接器。第三方插件需提供对应的集成适配器。
                </p>
              </div>
            ) : candidates.isPending ? (
              <p>正在读取配置目录…</p>
            ) : (
              <div className="integration-transfer-list">
                {(candidates.data?.items || [])
                  .filter((row) => adapter?.kinds.includes(row.kind))
                  .map((row) => (
                    <label
                      key={row.kind + "/" + row.id}
                      className="integration-transfer-candidate"
                    >
                      <input
                        type="checkbox"
                        checked={selected.has(row.kind + "/" + row.id)}
                        onChange={(e) =>
                          setSelected((old) => {
                            const value = new Set(old);
                            e.target.checked
                              ? value.add(row.kind + "/" + row.id)
                              : value.delete(row.kind + "/" + row.id);
                            return value;
                          })
                        }
                      />
                      <span>
                        <strong>{row.name}</strong>
                        <small>
                          {kindNames[row.kind]}
                          {(row.secret_fields || []).length
                            ? " · 包含 " +
                              (row.secret_fields || []).length +
                              " 项鉴权配置"
                            : ""}
                        </small>
                      </span>
                    </label>
                  ))}
                {!candidates.data?.items.length && <p>还没有可导入的配置。</p>}
              </div>
            )}
            <div className="integration-form-actions">
              <button className="btn" onClick={close}>
                取消
              </button>
              <button
                className="btn primary"
                disabled={!selected.size || busy}
                onClick={() => void preview()}
              >
                {busy ? "读取并预览中…" : "预览导入"}
              </button>
            </div>
          </>
        ) : (
          <>
            <p role="status">
              {plan.status === "completed"
                ? "导入已完成，配置保存为停用副本。"
                : plan.status === "partial"
                  ? "部分项目需要处理，可核对回执或重新预览。"
                  : "确认以下变更后导入。"}
            </p>
            <div className="integration-transfer-list">
              {plan.rows.map((row) => (
                <article key={row.key} className="integration-transfer-row">
                  <div>
                    <strong>{row.name}</strong>
                    <small>
                      {kindNames[row.kind]} ·{" "}
                      {row.status === "completed"
                        ? "已导入"
                        : row.status === "skipped"
                          ? "已跳过"
                          : row.status === "unknown"
                            ? "待核对回执"
                            : row.status === "failed"
                              ? "失败"
                              : "待导入"}
                    </small>
                  </div>
                  <CustomSelect
                    ariaLabel={"导入方式 " + row.name}
                    value={choices[row.key] || row.action}
                    onChange={(action) =>
                      setChoices((old) => ({
                        ...old,
                        [row.key]: action as Row["action"],
                      }))
                    }
                    disabled={
                      busy ||
                      !!row.applied_action ||
                      ["completed", "skipped", "unknown", "applying"].includes(
                        row.status,
                      )
                    }
                    options={[
                      { value: "create", label: "新建副本" },
                      ...(row.target_id
                        ? [{ value: "update", label: "更新已有副本" }]
                        : []),
                      { value: "skip", label: "跳过" },
                    ]}
                  />
                  <p className="integration-transfer-fields">
                    变更字段：{row.changes.join("、") || "无参数差异"}
                  </p>
                  {!!(row.secret_fields || []).length && (
                    <p className="integration-transfer-fields">
                      鉴权：{row.secret_fields.join("、")}（将安全复制）
                    </p>
                  )}
                  {(row.warnings || []).map((warning, index) => (
                    <p className="integration-transfer-warning" key={index}>
                      {warning}
                    </p>
                  ))}
                  {row.message && <p>{row.message}</p>}
                </article>
              ))}
            </div>
            <aside className="integration-transfer-security">
              <ShieldCheck size={17} />
              鉴权随选中配置加密保存，预览不显示密钥。导入不会启动命令或调用工具，检查后再启用。
            </aside>
            {plan.direction === "to-dsh" && (
              <p>
                导入完成后，在 DSH「设置 → FPL Core 连接 → 导入的集成」启用。
              </p>
            )}
            <div className="integration-form-actions">
              <button className="btn" onClick={close}>
                {plan.status === "completed" ? "完成" : "关闭"}
              </button>
              {plan.status !== "completed" && (
                <>
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() => {
                      setPlan(null);
                      setSelected(new Set());
                    }}
                  >
                    重新预览
                  </button>
                  <button
                    className="btn primary"
                    disabled={busy}
                    onClick={() => void apply()}
                  >
                    {busy
                      ? "正在处理…"
                      : plan.status === "preview"
                        ? "确认导入"
                        : "重试失败项 / 检查回执"}
                  </button>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </IntegrationModal>
  );
}
