# FPL Core

独立运行的模型、编程智能体、本地模型、任务和资源服务。看板复用现有 FPL 的页面、主题与组件；DeepSeek Harness 通过可选连接层接入。

## 启动看板

需要 Node.js 22+、pnpm、uv。Python 依赖安装到本仓库 `.runtime/worker-venv/`。

```sh
pnpm install --frozen-lockfile
pnpm setup:service
pnpm build
pnpm start
```

默认地址为 `http://127.0.0.1:9310`。启动入口打开带一次性票据的本机页面，票据交换后从地址栏移除；刷新页面沿用会话。

```sh
pnpm start:with-dsh   # 启动 Core、打开看板并唤起 DSH
pnpm dashboard        # 为现有 Core 打开新的已认证看板
pnpm status
pnpm stop
```

`start` 和 `start:with-dsh` 重复运行会复用现有实例。关闭浏览器窗口或 DSH 不停止 Core；`stop` 显式停止 Core。没有开机自启动登记。端口可通过 `--port 9312` 指定，后续命令使用相同参数。

服务、配置、加密密钥、资源与状态位于 `.runtime/core/`。首次使用从空目录开始，不导入已有 FPL 或 Harness profile 数据。日常操作通过看板与 `/v1` API 完成。

## 看板

首页保留 AI 应用的四个文件夹，侧栏「应用」可搜索已安装应用。应用通过 DSH 插件安装并启用，由 `registerApplication({dashboard})` 自动登记；停用或卸载注销入口，重连同步最新目录。可安装示例见 [插件应用示例](examples/dashboard-app/README.md)。现有 FPL 的具体工坊随各自应用插件迁入，当前公共服务包提供应用目录与安装接入能力。

- 模型与智能体：供应商分组、连接、模型、默认模型用途，五种 CLI 检测与连接。
- 模型测试：持久对话和附件、图像与视频生成、语音合成与识别、实时语音、本地视频增强。
- 调用日志：来源、模型、状态、耗时、真实用量和请求结果详情。
- 运行记录：位于 AI 能力，按来源和状态查看执行、停止运行、恢复结果查询及下载产物；首页保留最近运行。智能体测试位于编程智能体页面。
- 文件管理：分块上传、图片/音频/视频预览与下载。
- 外部连接：创建与撤销 DSH 配对，查看连接状态。

模型协议与本地模型范围见 [能力盘点](docs/scope.md)。本地 MLX / Metal 模型需要 Apple Silicon、macOS 15+；FFmpeg 使用本机可执行文件。RelayVSR 单次最多 300 帧、1080p 输入。

角色素材沿用本机交付包，放在 `.runtime/assets/brand/`，构建时物化到被忽略的 `web/public/brand/`。来源和使用范围见 [界面迁移记录](docs/ui-extraction.json)。插件图标与看板标签页统一使用品牌包的 `icon.webp`（项目 logo 的 256px 版本）；连接层构建将其复制到 `lib/icon.webp`，通过 manifest 和 `./icon` 导出，打包时随 `lib/` 分发。公开源码不携带个人角色资源。VAD 模型和 WASM 由锁定的 npm 依赖准备。

## 连接 DeepSeek Harness

1. 在独立看板的「外部连接」创建配对凭据。
2. 构建并打包连接层：

   ```sh
   pnpm build:connector
   pnpm pack --pack-destination .runtime/packages
   ```

3. 在 Harness 插件管理中安装生成的 tarball；插件包只包含连接代码和公共类型。
4. 打开 Harness「设置 → FPL Core 连接」，保存配对凭据。插件默认连接本机 9310 端口；其他地址通过插件 `endpoint` 参数指定。

连接层继续提供 `ctx.dshCore`。应用登记后可提交任务、交接资源和提供任务工具。Core 可通过连接层调用 DSH 已配置的原生模型；连接层也向 DSH 注册提交、查询和停止 Core 任务的工具。供应商密钥由 Core 管理，DSH Credentials 仅保存配对凭据。

DSH 断开后，独立供应商和本地任务继续执行；依赖 DSH 的在途调用保留输出并显示中断。重连不自动重放已中断的模型调用。

## 开发与验证

```sh
pnpm build
pnpm test
pnpm contracts:generate
pnpm test:consumer
pnpm test:harness  # 先生成连接器 tarball；仅使用隔离 profile
pnpm --dir web exec playwright install chromium
pnpm --dir web test:e2e
```

已有 Chrome 时可使用 `CORE_TEST_BROWSER_CHANNEL=chrome pnpm --dir web test:e2e`。所有自动测试使用 `.local/` 下的隔离状态。接口与恢复语义见 [API 契约](docs/api.md)，迁移原因见 [架构决策](docs/decisions.md)，证据见 [验收记录](docs/verification.md)。

## 独立运行包

运行 `pnpm release:service` 在 `.runtime/releases/` 生成可搬移运行目录，并在 `.runtime/packages/` 生成压缩包。运行包包含 Python 代码、锁定依赖、已构建看板和启动器；在其目录执行 `pnpm setup:service` 后即可 `pnpm start:with-dsh`。配置和业务状态初始为空，与开发目录相互独立。

DSH 连接层单独使用 `pnpm build:connector` 和 `pnpm pack --pack-destination .runtime/packages` 打包。更新桌面现有插件时，在插件管理中安装新包并重新配对。

## 集成中心

打开看板「集成」管理工具 API、MCP、Skill 和外部连接。DSH 配对已放入「外部连接」，每个连接独立显示状态与操作。「从 DSH 导入 / 导入到 DSH」复制选定配置及鉴权，默认停用，确认后可在目标端启用。此功能要求新版 0.3.0 DSH 连接器；仅更新独立服务不会替换桌面插件。接口、插件适配器和恢复规则见 [集成中心](docs/integrations.md)。


## Core 0.4 统一账号

看板名称为「未来道具研究所」，技术包与应用 ID 保持兼容。打开右上角账号进入 `/account`，管理员可创建/禁用账号、设置密码和撤销应用授权。初始本机账号沿用原 Core 状态；新账号拥有独立配置、资源和任务。首次可以用 `pnpm dashboard --no-open --output .runtime/core-launch.json` 生成仅本机可读的一次性入口，然后在账号页设置密码。不要发送该文件或提交到 Git。

工坊在自己的仓库运行 `pnpm setup`，再用其文档的 `connect-core --launch-file` 入口登记统一身份；9360–9365 对应视觉、剪辑、演示、思绪、Galgame、录音标注。应用直接登录和独立桥接也会出现在 Core 目录中。DSH 插件开关只控制连接层，独立服务使用自身启动器。应用包要求 Core 0.4.0，老的 DSH Core 连接器与原任务、资源协议仍兼容。


统一数据位置与旧 FPL 交接见 [存储与迁移](docs/storage.md)。本机所有者可从看板顶部“存储”进入设置，未来工坊初始化自动使用该根目录的自有子目录。
