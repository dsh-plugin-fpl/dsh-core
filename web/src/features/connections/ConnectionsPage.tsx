import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchApiJson } from "@platform/api/client";
import { IntegrationModal } from "@features/integrations/components";
import { fetchBridges } from "./api";
export function useDshConnections() {
  const cache = useQueryClient();
  const query = useQuery({
    queryKey: ["bridges"],
    queryFn: fetchBridges,
    refetchInterval: 2000,
  });
  const [name, setName] = useState("DSH"),
    [token, setToken] = useState(""),
    [error, setError] = useState(""),
    [pending, setPending] = useState(false),
    [adding, setAdding] = useState(false);
  const create = async () => {
    setPending(true);
    setError("");
    try {
      const value = await fetchApiJson<{ token: string }>("/v1/bridges", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      setToken(value.token);
      await cache.invalidateQueries({ queryKey: ["bridges"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  };
  const revoke = async (id: string) => {
    await fetchApiJson("/v1/bridges/" + encodeURIComponent(id), {
      method: "DELETE",
    });
    await cache.invalidateQueries({ queryKey: ["bridges"] });
  };
  const close = () => {
    setAdding(false);
    setToken("");
    setError("");
  };
  return {
    query,
    name,
    setName,
    token,
    error,
    setError,
    pending,
    adding,
    open: () => {
      setToken("");
      setError("");
      setAdding(true);
    },
    close,
    create,
    revoke,
  };
}
export function DshPairingModal({
  connection,
}: {
  connection: ReturnType<typeof useDshConnections>;
}) {
  const c = connection;
  return (
    <IntegrationModal
      title="连接 DSH"
      onClose={c.close}
      protect
      pending={c.pending}
    >
      <form
        className="integration-form"
        onSubmit={(e) => {
          e.preventDefault();
          void c.create();
        }}
      >
        <p className="integration-field full external-connection-intro">
          连接后，DSH 可使用 Core 公共能力，并向看板提供已安装应用和集成配置。
        </p>
        <label className="integration-field full">
          <span>服务地址</span>
          <input value={location.origin} readOnly />
        </label>
        {!c.token ? (
          <>
            <label className="integration-field full">
              <span>连接名称</span>
              <input
                required
                maxLength={120}
                value={c.name}
                onChange={(e) => c.setName(e.target.value)}
              />
            </label>
            <div className="integration-form-actions">
              <button
                type="button"
                className="btn"
                disabled={c.pending}
                onClick={c.close}
              >
                取消
              </button>
              <button
                className="btn primary"
                disabled={c.pending || !c.name.trim()}
              >
                {c.pending ? "创建中…" : "创建配对凭据"}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="integration-field full">
              在 DSH → 设置 → FPL Core 连接中保存以下凭据，关闭后不再显示明文。
            </p>
            <label className="integration-field full">
              <span>配对凭据</span>
              <input
                type="password"
                value={c.token}
                readOnly
                autoComplete="off"
              />
            </label>
            <div className="integration-form-actions">
              <button
                type="button"
                className="btn"
                onClick={() =>
                  void navigator.clipboard
                    .writeText(c.token)
                    .catch(() => c.setError("请手动选择并复制凭据"))
                }
              >
                复制凭据
              </button>
              <button type="button" className="btn primary" onClick={c.close}>
                完成
              </button>
            </div>
          </>
        )}
        {c.error && (
          <p className="field-error" role="alert">
            {c.error}
          </p>
        )}
      </form>
    </IntegrationModal>
  );
}
