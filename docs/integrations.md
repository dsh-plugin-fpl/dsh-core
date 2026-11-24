# 集成中心与 DSH 配置副本

`/integrations` 复用 FPL 的工具 API、MCP、Skill 与外部连接页面。外部连接逐项展示 DSH 配对和飞书自建应用，统一名称、类型、状态和操作；连接 DSH、配对凭据与飞书配置使用弹窗。旧 `/settings` 跳转到 `/integrations?kind=external-connections`。应用目录仍由实际安装的 DSH 应用插件发布，不硬编码未安装工坊。

工坊的独立应用桥接属于应用身份，不作为外部连接展示或计数。`GET /v1/bridges` 保留完整列表并返回 `kind: dsh | application` 和可空的 `application_id`，分类来自当前账号持久的应用身份绑定，与名称、在线状态及是否发布应用入口无关。顶部 DSH 状态只统计 `kind=dsh` 的在线配对；工坊入口和运行状态仍由 `/v1/applications` 在「应用」中呈现。

## 本机管理

- 工具 API：JSON Schema、请求模板、Bearer / API-Key、启停、检查和最近检查记录。
- MCP：Streamable HTTP / stdio、参数、环境、工作目录、附加请求头、超时、工具发现和启停。保存及导入均不启动进程；显式检查会建立连接。
- Skill：完整 ZIP 上传、独立版本、默认版本、标签、Markdown 与附件预览/下载。20 MiB 压缩、100 MiB 解压、1000 文件；拒绝越界、符号链接和重复路径。
- 外部连接：DSH 配对、在线状态与撤销；飞书凭据、启停、鉴权、目标权限检查与删除。文章业务仍由应用插件提供。

Core 的专用集成库位于 `.runtime/core/integrations/integrations.sqlite3`；使用显式版本与校验和登记迁移。首次迁移已在隔离库验证。凭据复用 Core 自有加密密钥，和配置在同一事务提交。备份/恢复此能力时同时保留集成库、Skill 目录、Core state、access 密钥和配对记录；不能单独丢弃密钥后保留密文。

工具 API 和 MCP 的显式调用端点为 `POST /v1/integrations/tool-apis/{id}/call`、`POST /v1/integrations/mcp/{id}/call`。请求带 request_id、expected_revision、input；MCP 另带 tool_name。结果是 Core 任务回执，相同输入不重复调用，输入变化返回 409。运行记录保留脱敏结果；取消、超时或重启不会重放未知结果。配置验证亦计入运行记录。

## 双向导入

看板顶栏选择「从 DSH 导入」或「导入到 DSH」，选择在线连接、适配器和条目，再预览并确认。两端各自拥有配置副本，后续修改不会自动覆盖另一端。再次导入可新建、更新上次副本或跳过；目标修改后默认跳过，更新使用目标 revision 校验。相同名称的新 Skill 会同步修改包内 name，附件保持完整。

鉴权随明确选择的配置复制，浏览器仅得到字段名与存在状态。完整快照只通过已配对的本机 WS 传递，Core 预览包加密持久化，完成后移除包内容；预览有效期一小时。一次最多 50 项、64 MiB。每项独立提交并保存回执，部分失败可以核对；响应不确定时只查原回执，不盲目重写，也不能换动作复用原请求。

所有导入副本保持停用。Core 中在相应标签检查、管理；DSH 中在「设置 → FPL Core 连接 → 导入的集成」明确启用。MCP 启用才启动服务器，工具 API 只在实际调用时请求网络，Skill 启用才加入宿主注册表。DSH 自有副本与回执保存在当前 profile 的 `.runtime/fpl-core-integrations/catalog.enc`，使用 DSH Credentials 保存独立 AES 密钥；Skill 附件是独立的私有文件目录，不引用源插件路径。

## 原生与应用适配器

内置 `native-dsh` 从 DSH 公开 Config Editor、Plugin Inventory 与 Skill Registry 读取现有 MCP/Skill，不猜测第三方私有配置，不改 profile YAML。MCP 环境变量、Headers、cwd 和调用超时按共同契约复制；重连/启动策略由目标端独立设置。Skill 必须能完整取得附件；无法导出的远程/opaque 资源明确失败。内置 `core-managed` 接收四类配置的独立 DSH 副本。

第三方插件先登记应用，再使用公开 `ctx.dshCore.registerIntegrationAdapter(adapter)` 登记：

```ts
ctx.effect(() =>
  ctx.dshCore.registerIntegrationAdapter({
    id: "my-app-integrations",
    application_id: "my-app",
    name: "我的应用配置",
    kinds: ["tool-apis", "mcp", "skills", "external-connections"],
    can_import: true,
    can_export: true,
    list: (signal) => publicCatalog(signal),
    export: (kind, id, signal) => exportSelectedSnapshot(kind, id, signal),
    import: (item, options, signal) =>
      importDisabledCopy(item, options, signal),
    receipt: (requestId, signal) => readOriginalReceipt(requestId, signal),
  }),
);
```

`list` 只返回 id、kind、name、revision、enabled 与 secret_fields 元数据。`export` 返回 schema_version=1 的 PortableIntegration，完整配置与可选 Skill archive_base64；不得把全部密钥塞入目录元数据。`import` 必须检查 expected_revision，使用 options.request_id 保存幂等回执；不确定是否提交时继续通过 receipt 查询。应用卸载时注销适配器，重连发布完整目录。接口正式类型见 `src/integrations/types.ts` 和包导出的 CoreApi；不通过通用 DSH 工具暴露配置明文。

外部连接首个具体服务协议为飞书；新的服务类型需扩展 Core 的公开配置/校验契约，不能以任意私有对象绕过校验。真实飞书凭据与付费网络调用不属于自动验收。
