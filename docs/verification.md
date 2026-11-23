# 独立服务验收记录

日期：2026-11-23。交付版本：Core / Dashboard / DSH Connector 0.2.0。所有验证使用隔离状态；现有桌面 Harness 的 0.1.0 插件和旧 profile 未替换。

| 项目 | 结果与证据 |
| --- | --- |
| Python 协议、HTTP 与持久状态 | 188 项通过。保留原供应商、CLI、本地模型与实时语音协议测试；新增票据、会话、Host/Origin/CSRF、配对范围、密钥脱敏与事务回滚、目录冲突、历史、任务事件、资源 MIME、上传完成重试与重开状态 |
| DSH 网络连接层 | 6 项通过。真实 Cordis Context 连接独立 HTTP/WS Core，覆盖 2 MB 分块资源、应用范围、原生模型流、所有者限制、关闭 DSH 保留 Core、在途回调保留部分输出，以及 Core 重启后自动重连 |
| 实际 Harness tarball | 新 0.2.0 tarball 安装到专用 `.runtime/harness-independent/` profile。真实 Harness LLM 注册的确定性适配器经连接层完成持久任务，返回 `FPL_CORE_TARBALL_OK`；资源交接通过，关闭该 Harness 后 Core 继续就绪。报告：`.local/harness-independent/latest.json` |
| 真实 FPL 页面 | Playwright 1 个完整场景通过。配置连接和模型、流式对话、刷新保留历史、调用日志、分块文件上传、预览弹窗 Esc、配对、深浅主题，以及 1440 / 860 / 390 宽度；无页面错误或失败 API 请求 |
| 独立运行包 | 在 `.runtime/releases/` 下安装运行包自己的环境，确认模块来自包内源码，空配置启动、并发与重复启动复用、端口冲突保护、停止后立即重启、文件持久化及停止全部通过。报告：`.local/release-acceptance/latest.json` |
| 构建与公开契约 | 连接层、看板 TypeScript/Vite 构建通过；OpenAPI 与 TypeScript 客户端重新生成；独立调用方示例编译通过；Ruff 与修改的 TypeScript/JS 格式检查通过 |
| 包内容 | DSH tarball 只含 lib、patch、公开文档和包描述，不含 Python、看板或业务数据。服务压缩包含包内 Python、uv 锁、构建看板与启动器，不含运行配置、密钥、测试状态或业务数据库 |
| 总工作区 | 结构检查通过，12 项管理工具测试通过；FPL 原仓库无受版本管理的改动 |

截图位于 `.local/browser-tests/`：`home-dark.png`、`home-light.png`、`catalog-dark.png`、`home-side-by-side.png`、`home-narrow.png`。浏览器测试的供应商响应来自确定性夹具；Harness 测试使用实际服务和原生适配器注册，不调用付费模型。

## 验证范围

原 0.1.0 阶段曾在桌面 Harness 调用真实 Flash 返回 `DSH_CORE_HARNESS_OK`，并实际运行 Codex、FFmpeg。这些是迁入协议的历史证据，不作为本次新连接器的真实账号调用结果。新连接器已完成隔离 Harness tarball 验收；正式桌面更新需要安装新包并在看板创建配对。

Qwen / Zonos / RelayVSR 的固定清单、安装恢复与运行协议已覆盖；本次未下载大型权重进行真机推理。实时语音供应商通过协议夹具验证，本次未发起真实麦克风通话。Vite 仍提示模型测试的语音相关懒加载包较大；首页入口独立拆包，不预加载整个测试页。

## 复测

```sh
pnpm contracts:generate
pnpm build
pnpm test
pnpm test:consumer
CORE_TEST_BROWSER_CHANNEL=chrome pnpm --dir web test:e2e
pnpm pack --pack-destination .runtime/packages
pnpm test:harness
pnpm release:service
# 对生成的运行目录安装独立环境，再执行：
uv run --no-project python scripts/verify-service.py <运行目录>
```

正式运行状态位于 `.runtime/`；`.local/` 只保存可清理证据。此交付未提交、推送或改变总仓库 gitlink。

## 2026-11-23：运行记录与智能体测试调整

运行记录移入 AI 能力二级菜单；首页保留最近运行。智能体测试归所选 CLI 的管理页面。`pnpm build`、`pnpm test` 通过（6 项连接测试、188 项 Python 测试）。浏览器原完整场景和新增 `web/e2e/run-records.spec.ts` 通过，后者覆盖来源/状态组合筛选与清除、停止失败后重试、恢复结果查询、输出与断连错误同时展示、任务详情深链接、智能体测试提交及回执链接。

来源跳转使用隔离 Core 的真实模型目录、会话与任务验证：建立两条对话后，从较早运行返回对应原对话。供应商执行使用确定性夹具，不启动真实 CLI 或付费调用。截图检查浅色/深色和 1440、860、390 宽度，无横向溢出；截图保存于 `.local/run-records/`。此次未改变 API、任务存储、连接器协议或真实业务数据，未重新安装桌面插件。

## 2026-11-23：应用入口恢复与插件目录（0.2.1）

恢复首页 AI 应用四分类文件夹、应用一级菜单和可搜索目录。文件上传改用原生按钮，图标/文字采用统一对齐盒模型。已合并另一任务的运行记录导航、来源跳转和智能体测试改动。

实际仓库构建和调用方示例编译通过，195 项 Python 测试、7 项连接层测试通过，2 个浏览器场景通过。新增覆盖应用登记、卸载/停用注销、重连快照、离线状态和非法入口地址的原子拒绝；浏览器验证应用搜索和详情、文件选择器的键盘激活及上传图标/文字中心对齐（偏差小于 1 CSS 像素）。

`pnpm test:harness` 用专用隔离 profile 安装 0.2.1 连接器和打包后的 dashboard-app-example；验证两份插件应用元信息实际出现在看板 API，示例独立页面可以打开，并保留原生模型回调和资源交接验收。报告 `.local/harness-independent/latest.json` 包含 dashboard_registration 与 example_application_page 两项结果。未替换桌面现有插件。

截图在 `.local/browser-tests/`：home-with-app.png 为隔离插件应用的实际登记效果；home-dark.png / home-light.png 为恢复的初始应用布局；files-light.png / files-dark.png 为文件页；home-side-by-side.png / home-narrow.png 为窄窗口。正式 Core 已在 9310 重启，配置、文件和运行记录保留。具体 FPL 工坊仍由各自的后续应用插件迁入。

## 2026-11-23：集成中心、DSH 双向配置副本与统一连接卡片（0.3.0）

实际 dsh-core 仓库完成构建、OpenAPI 生成、公开调用方编译：202 项 Python、8 项连接层测试通过；2 个真实 Chrome 场景通过。新增覆盖四类目录与配置鉴权、MCP cwd/Headers/超时、完整 Skill 附件、源变化、目标 revision 冲突、部分成功、未知导入结果只读核对、幂等调用及无自动执行。

连接页并入「集成中心 → 外部连接」，DSH 与飞书逐项使用一致卡片；DSH 复用现有蓝色图标，默认名为 DSH。配对与新建配置使用弹窗，旧 /settings 兼容跳转。浏览器实测四标签、双向导入、输入/回执不显示配置密钥、浅深主题及 1440 / 860 / 390 宽度，无页面错误与横向溢出。截图 `.local/browser-tests/integrations-深色-1440.png` 等是隔离数据，示例飞书连接并非真实账号。

最终 0.3.0 tarball 在专用隔离 Harness 安装通过：原生 MCP 通过公开 Config Editor/Plugin Inventory 读取（Settings 仅投影 volatile 字段，不能覆盖 MCP 全配置），Skill 通过公共注册表及完整 ZIP 导出；四类经 Core 往返复制后全部停用，显式启用后的 Tool API/MCP 注册与 Skill 附件读取通过。报告 `.local/harness-independent/latest.json` 的 integrations 字段记录结果；原生模型、资源、应用目录与独立生命周期检查继续通过。桌面现有插件未替换。

独立服务运行包完成自身环境安装与隔离验证：包内模块、空配置、并发/重复启动、端口冲突、停止/重启及状态保留均通过，报告 `.local/release-acceptance/latest.json`。集成模块通过 Ruff 与格式检查。正式 9310 服务更新前通过认证 API 确认无在途任务；更新后 /ready 返回 0.3.0，四类目录和旧地址跳转通过，既有配对保留。源代码恢复点位于 `.runtime/core-integrations-transition/`，不改原业务 schema。

飞书真实凭据、付费工具及真实第三方插件的私有配置未调用；它们需通过公开适配器接入。未提交、推送或更新总仓库 gitlink。保留并合入同期的插件图标、manifest 导出和文档打包修改，FPL 主仓库保持干净。

## 2026-11-23：独立工坊的公共生成参数（0.3.1）

`capabilities` 增加不含凭据的供应商生成参数目录；看板 generation-options 复用相同的尺寸和工作流来源，修正先前固定 1024 尺寸与供应商不符的问题。连接层公开 CapabilityCatalog 类型，旧服务缺少新增字段时仍兼容。

205 项 Python、8 项连接层测试通过，连接器构建、公开调用方编译与 OpenAPI 再生成通过。使用真实 Core WebSocket、任务和分块资源，配合隔离确定性供应商执行，将生成结果发布到独立视觉画布；未调用真实付费供应商。两个工坊另在联合工作区维护真实 FFmpeg 媒体交接和 tarball 安装验收。本次无业务 schema 变化，不迁移旧 FPL 数据。

## 2026-11-23：用户桌面 DSH 正式安装

按用户明确授权，将 Core 0.3.1 及视觉创作工坊、视频剪辑工坊 0.1.0 安装并启用到桌面 profile。两个工坊的本机安装包使用对应角色的现有 256px WebP 头像，保留公开源码的通用图标；本机包和头像保存在各仓库 `.runtime/`，不发布私人素材。DSH 图标上限为 256 KiB，头像包分别使用约 13/15 KiB 的素材。

真实桌面 0.2.2-rc.1 暴露旧宿主包覆盖问题：原 Core 普通依赖使 pluginInventory/list 返回 404。移为宿主可选 peer、保留固定开发依赖后，连接器构建及 8 项连接测试通过。首次热更新仍持有已删除模块的位置，完整重启 DSH 后库存接口恢复；桌面插件页实测显示两个正确头像且三个开关均启用。

通过正式登录与公开 RPC 完成 Core 配对；既有业务数据、会话和其他插件保留。两套工坊使用各自正常启动器初始化空状态并监听 9360/9361；Core 目录中两应用均 ready，服务就绪与头像读取通过。无真实付费生成。回退所需 profile 清单/锁/覆盖配置和原安装包位置记录在 `.runtime/desktop-install/`，不备份无关业务数据库。不含凭据的最终结果为该目录 installed.json。
