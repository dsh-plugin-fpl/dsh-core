# API 与运行契约

Core 独立拥有配置、任务、会话、资源、凭据和本地执行。HTTP 入口为 `/v1`；根级 `/health`、`/ready` 仅返回服务就绪信息及实例标识。运行中的 `/v1/openapi.json` 是看板客户端的契约来源。`pnpm contracts:generate` 更新 `web/src/generated/`。

## 本机访问

Core 默认监听 `127.0.0.1:9310`。启动器通过 120 秒、单次使用的票据换取 HttpOnly、SameSite=Strict 的本机会话；票据使用 URL fragment 传递，页面立即清除。写请求同时检查 Origin、Host 和 `X-Core-CSRF`。会话不在服务重启后恢复，可通过 `pnpm dashboard` 重新打开。

DSH 使用独立配对凭据连接 `/v1/bridge`，凭据保存于 DSH Credentials，不进入 URL 或日志。配对可在看板撤销，撤销立即断开连接。供应商密钥由 Core 加密，密文与模型目录在同一 SQLite 事务中提交；解密密钥保存于 `.runtime/core/access/secret.key`。

## 看板页面协议

迁入 FPL 页面使用 Core 拥有的目录、默认模型、智能体、本地模型、测试会话、生成、转录、语音、视频增强和日志接口。采用的页面协议定义保存在 `python/dsh_core_worker/fpl_page_contract.json`，新服务在此基础上发布自己的 OpenAPI；不依赖 FPL 网站、账号服务和私有存储。

目录统一使用 catalog revision CAS。冲突返回 409、`revision_conflict` 和当前目录，页面保留输入。密钥响应仅返回是否配置，不返回明文。会话保存消息、参数和附件，执行使用结构化历史，失败或取消的 assistant 消息不进入下一轮上下文。

`/v1/tasks` 提交与查询持久任务，`/v1/resources` 管理资源；资源通过内容入口鉴权读取。图片、视频、音频可预览，其他类型以附件下载。文件内容保存 SHA-256，分块读写沿用现有校验与恢复协议。

## DSH 公共调用接口

`@dsh-plugin-fpl/dsh-core` 继续提供 `ctx.dshCore`；独立服务包和 DSH 插件分别发布。连接层不启动 Python，不保存业务数据库，不接管 Core 的生命周期。

- `registerApplication({id,name,tools,dashboard?})`：登记应用和带 JSON Schema 的工具；可选 dashboard 发布看板入口，返回注销函数。
- `submit(request, signal?)`：返回持久任务回执。请求使用 `request_id`、`application_id`、`session_id`、能力、模型、输入、参数和资源 ID。
- `task(id)`、`events(id,after)`、`cancel(id)`：查询、按游标读取及显式取消。
- `putResource`、`importResource`、`resource`、`resourceChunk`：小文件或最多 256 MB 分块交接。

连接层将应用 ID 绑定到配对身份，不能通过请求参数指定其他连接的应用或任务。Core 可读取该 DSH 连接公布的原生模型目录，通过认证的双向 WS 回调模型与应用工具。连接层的设置远程端点只开放连接状态和配对保存；模型配置、密钥管理和通用内部回调不向 DSH 工具开放。

## 任务与恢复

状态为 `queued → running / processing → completed / failed / cancelled`，另外保留 `recoverable` 和 `interrupted`。同一应用和会话内，相同 request_id、相同输入返回原回执；变更输入返回 409 `idempotency_conflict`。默认模型与连接在入队时冻结。

任务事件按递增序号保存，含状态转换、文本、进度和用量。`/v1/tasks/{id}/stream` 支持 `after` 游标；终态另返回当前持久快照。关闭页面不重提任务，停止按钮发送显式取消命令。

有供应商任务编号或结果 URL 的任务从原回执恢复查询；未知提交结果显示中断，不自动重提。显式恢复本地视频任务可以重新处理原件，不进行付费供应商重提。DSH 断开时，独立供应商/本地任务继续执行，依赖 DSH 的调用保留已有输出并显示中断。

## 持久路径

- `.runtime/core/state/`：Core SQLite（含供应商凭据密文）、资源、本地模型与任务工作目录。
- `.runtime/core/access/`：加密密钥、配对记录及单次启动票据。
- `.runtime/worker-venv/`：独立 Python 运行环境。
- `.runtime/releases/`、`.runtime/packages/`：独立服务运行包和 DSH 连接层安装包。
- `.local/`：可清理的隔离测试、截图与 trace，不参与正式运行。

## 插件应用目录

`GET /v1/applications` 向本机已认证看板返回启用的应用入口。应用通过 DSH 插件生命周期登记：

```ts
ctx.effect(() => ctx.dshCore.registerApplication({
  id: "my-notes", name: "我的笔记",
  dashboard: {
    category: "knowledge", description: "笔记与知识整理",
    package_name: "@dsh-plugin-fpl/my-notes", version: "1.0.0",
    capabilities: ["笔记"], icon: "notebook",
    url: "http://127.0.0.1:19320/",
  },
}));
```

分类为 visual / knowledge / media / development，对应首页的四个文件夹。url 和 icon_url 限本机 HTTP(S)，不得包含凭据。可只先登记元信息，此时状态为 no_entry；可打开时为 ready，连接丢失后为 offline。应用的页面、接口、鉴权和业务状态由应用插件或其独立服务提供；Core 提供目录及公共任务/资源能力。看板通过独立窗口打开应用入口，不执行插件上传的任意脚本。

连接层重连通过 applications.sync 原子发布完整目录，移除断线期间卸载的条目；动态启用使用 application.register，停用/卸载使用 application.unregister。配对身份限定应用 ID 命名空间，撤销连接后不再展示其条目。未声明 dashboard 的内部调用方仍可提交任务，不出现在应用列表。

可直接打包安装的示例见 [dashboard-app](../examples/dashboard-app/README.md)。

## 集成中心（0.3.0）

四类配置由 `/v1/integrations/{tool-apis|mcp|skills|external-connections}` 管理，明确调用返回 Core 任务回执。`ctx.dshCore.registerIntegrationAdapter` 发布应用拥有的集成适配器；看板通过 dsh-adapters、transfer-candidates、transfers/preview、transfers/{id}/apply 和 transfers/{id} 完成两端独立配置复制。正式 HTTP schema 由 OpenAPI 生成；插件类型、导入回执与安全/恢复边界见 [集成中心](integrations.md)。

DSH 配对入口已合并到「集成中心 → 外部连接」，与飞书连接逐项展示；旧 `/settings` 保留重定向。导入副本停用，不执行命令、HTTP 调用或 Skill 注册。只有所有者明确启用后，DSH 连接层才将所选副本加入宿主工具/Skill 注册表；这部分 profile 配置与密钥由 DSH 自有存储管理，不承载独立 Core 的业务库。

## 工坊模型参数目录（0.3.1）

配对 WS 的 `capabilities` 响应新增只读 `generation_options` 与 `temporary_upload_configured`。目录包含 Core 模型 ID、供应商种类、slug、图片尺寸、参考数量、随机种子支持、提示词上限、视频能力与工作流参数；不包含密钥、凭据引用或供应商地址。看板的 `/v1/model-test/generation-options` 使用相同目录。应用按该目录构建请求，Core 在执行入队时再次校验。
