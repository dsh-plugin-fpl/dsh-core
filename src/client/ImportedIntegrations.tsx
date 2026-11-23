import { useEffect, useState } from "react";
type Entry = {
  id: string;
  name: string;
  kind: string;
  revision: string;
  enabled: boolean;
  error?: string;
  command?: string;
  arguments?: string[];
};
export function ImportedIntegrations({
  api,
}: {
  api: (method: any, params?: Record<string, any>) => Promise<any>;
}) {
  const [items, setItems] = useState<Entry[]>([]),
    [error, setError] = useState(""),
    [pending, setPending] = useState("");
  const refresh = () => api("integrations.copies").then(setItems);
  useEffect(() => {
    let active = true;
    const read = () =>
      api("integrations.copies")
        .then((value) => {
          if (active) setItems(value);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    void read();
    const timer = setInterval(read, 3000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [api]);
  const toggle = async (row: Entry) => {
    setPending(row.id);
    setError("");
    try {
      await api("integrations.enable", { id: row.id, enabled: !row.enabled });
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending("");
    }
  };
  return (
    <section
      style={{
        marginTop: 30,
        borderTop: "1px solid currentColor",
        paddingTop: 16,
      }}
    >
      <h3>导入的集成</h3>
      <p>在独立看板中选择「导入到 DSH」，导入后在这里检查并启用。</p>
      {error && <p role="alert">{error}</p>}
      {!items.length ? (
        <p>还没有导入配置。</p>
      ) : (
        items.map((row) => (
          <article
            key={row.id}
            style={{
              padding: "14px 0",
              borderBottom: "1px solid currentColor",
            }}
          >
            <strong>{row.name}</strong>
            <span>
              {" "}
              · {row.kind} · {row.enabled ? "已启用" : "已停用"}
            </span>
            {row.command && (
              <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                启用后运行：{row.command} {(row.arguments || []).join(" ")}
              </pre>
            )}
            {row.error && <p role="alert">{row.error}</p>}
            <button disabled={!!pending} onClick={() => void toggle(row)}>
              {pending === row.id ? "处理中…" : row.enabled ? "停用" : "启用"}{" "}
              {row.name}
            </button>
          </article>
        ))
      )}
    </section>
  );
}
