/** Minimal installable application: its own web entry, advertised through the public Core API. */
import { createServer } from "node:http";
export const inject = ["dshCore"];
export function apply(ctx) {
  ctx.effect(async () => {
    const server = createServer((request, response) => {
      if (request.method !== "GET" || request.url !== "/") {
        response.writeHead(404).end();
        return;
      }
      response.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Security-Policy":
          "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
      });
      response.end(
        `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>插件应用示例</title><style>body{background:#1b201d;color:#f8f1e7;font:16px/1.8 system-ui;margin:0;padding:48px}main{max-width:680px;margin:auto;border:1px solid #41483d;border-radius:20px;padding:32px}small{color:#f48050}h1{font-size:28px}p{color:#bfb8a8}</style><main><small>Future Props Lab</small><h1>插件应用示例</h1><p>此页面由独立 DSH 应用插件提供。应用通过公开的 ctx.dshCore 登记到 FPL 看板，启用后显示入口，停用或卸载时注销。</p><p>这是接入示例，正式应用在自己的仓库实现页面、业务接口与持久状态。</p></main></html>`,
      );
    });
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });
    let unregister;
    try {
      unregister = ctx.dshCore.registerApplication({
        id: "dashboard-app-example",
        name: "插件应用示例",
        dashboard: {
          category: "development",
          description: "通过安装 DSH 插件接入独立 FPL 看板",
          capabilities: ["插件接入"],
          icon: "code",
          package_name: "@dsh-plugin-fpl/dashboard-app-example",
          version: "0.1.0",
          url: "http://127.0.0.1:" + server.address().port + "/",
        },
      });
    } catch (error) {
      server.close();
      throw error;
    }
    return async () => {
      unregister();
      await new Promise((resolve) => server.close(resolve));
    };
  });
}
