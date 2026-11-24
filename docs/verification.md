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


## 2026-11-23：Core 0.4 与六工坊统一账号

Core 211 项 Python（含新账号、实时撤权与语音目录边界）和 8 项连接测试通过；公开调用方编译、OpenAPI 再生成、连接器及看板构建通过；原两个 Chrome 场景通过。新增六应用组合通过公开 CLI/HTTP/WS 启动七个真实进程，验证两账号隔离、旧 owner 保留、PPTX 导出、思绪写作/社区逐篇撤权/显式官方导入/飞书幂等写入、Galgame 剧情、WAV 导入与 ASR、跨应用授权、真实 FFmpeg 片段复制至视觉自己的画布原件。实际 Harness 安装七份当前 tarball，六应用登记正确，关闭 Harness 后服务仍就绪。

联合证据 `.local/workshops-accounts/1795443042516017000/` 含 report、harness-registration、ui-result 与页面截图。模型与飞书使用协议夹具，未调用真实付费供应商或修改旧 FPL 数据。页面等待编辑器加载及账号管理的补充结果在总仓库联动记录维护。

## 2026-11-23：移除看板运行记录

移除 AI 能力中的运行记录菜单、首页最近运行、独立列表/筛选/详情和链接；旧 `/tasks?task=…` 显示首页。智能体测试改为在当前页面读取本次任务，支持进度、输出、错误、停止失败后重试和原回执恢复查询。任务 API 与持久数据未变。

`pnpm build` 和 `pnpm test` 通过（8 项连接器、205 项 Python）；Chrome 完整浏览器验收 2 项通过。新用例覆盖无运行入口、首页不读取任务列表、智能体结果与错误同时显示、停止和恢复，以及浅色/深色 1440、860、390px 无横向溢出。截图与浏览器日志在 `.local/remove-run-records/` 和 `.local/remove-run-records-e2e.log`。未运行真实智能体或付费供应商请求。


最终补测：212 项 Python、9 项连接/目录投影测试通过；思绪 89 项 Python，新增纯插件凭据的知识库读取和实时撤权。公开七进程组合加入六个无浏览器 cookie 的插件调用后全部通过，证据 `.local/workshops-accounts/1795446055115708000/`。OpenAPI 与 TypeScript 重新生成，Core 看板构建通过。

同一包/入口由独立服务和 DSH 双重登记时，看板只显示一张可用卡片并保留旧详情链接；关闭 DSH 后直接服务仍 ready。桌面已按正常插件管理安装 Core 0.4 和六个工坊（视觉/剪辑 0.2，其余 0.1），六个工坊中文名称与对应头像、启用状态可见。连接配置经公开 ConfigEditor 写入；一次性配置助手执行后已卸载。


最终写作流验收：提供方隔离模型夹具支持显式分块间隔；公开七进程组合检查首个思绪 SSE 到达时 Core 仍处于 running/processing，关闭 HTTP 响应后 Core 持久任务变为 cancelled。夹具参数不进入正式服务。


全新锁定 Node 安装与生产构建通过。首次 Python 环境并发冷启动可能超过原 Node 夹具的 5 秒轮询预算；夹具改为最多 30 秒，保留逐次健康检查，进程提前退出立即报告 stderr，不改变正式服务启动参数。


## 2026-11-24：统一存储与显式来源迁移

Core 提供所有者限定的存储设置、预检与可恢复切换；六工坊通过自己的维护入口导入并使用统一根目录。逐文件 SHA-256、SQLite 完整性与外键核验通过；正式迁移后七服务就绪，六工坊统一账号和跨应用连接均正常。公开文件库分页覆盖全部 12,987 个来源素材，图片和大于 256 MiB 视频的 Range 内容与来源一致；三个已启用本地模型恢复为 installed。原站通过正常生命周期恢复。未发起真实付费生成。

Core 227 项 Python、9 项连接层测试及构建通过；存储页面 Chrome 明暗主题和 1440/800/390px 验证通过。六工坊 Python 测试分别为视觉 22、剪辑 13、演示 60、思绪 97、Galgame 72、录音标注 9，各自三项 Node 测试及构建通过。来源导出和仓库架构共 53 项测试通过。密钥、账号、来源内容和完整回执只保存在私有运行目录。

六工坊 DSH 名称改为 dsh-fpl- 前缀，包 ID 与稳定应用 ID 保持兼容。更新包已构建；本轮桌面插件热更新仍待正常 GUI 安装，不能用 CLI 绕过 desktop profile 管理。


## 2026-11-24：原版文件管理恢复

- `pnpm build`：通过连接器、TypeScript 和看板构建；仅保留既有依赖 use-client 和大 chunk 提示。
- `pnpm test`：13 个连接器测试、233 个 Python 测试通过。新增文件库隔离/CSRF、并发版本、目录循环、空目录删除、文件显示名/位置/移除和原件保留、分页筛选搜索、ZIP/目录往返、导入幂等与中断恢复、路径和数量限制，以及超过默认 multipart 1000 字段的预检回归。
- `CORE_TEST_BROWSER_CHANNEL=chrome pnpm --dir web test:e2e`：4 个真实 Chrome 旅程通过。1920/800/390px × 明暗主题 × 列表/网格共 12 张截图，无横向溢出；完成真实文件选择、SVG/文本预览、新建/重命名/移动/移除、空目录删除、ZIP/本地文件夹导入与 ZIP 下载。
- 最终 multipart 适配后单独重跑 `playwright test e2e/files.spec.ts`：通过（4.4s）。最终 ruff、Prettier、`tsc --noEmit` 与 `git diff --check` 均通过。
- 本机证据：`.local/files-browser/`、`.local/files-full-tests.log`、`.local/files-browser-tests.log`、`.local/files-browser-final.log`、`.local/files-render-check-build.log`。所有自动化使用隔离服务、账号和生成文件；未调用真实付费供应商，未修改现有业务数据或迁移账本。
- 运行实例：只读核对时 9310 Core 尚未加载新文件编辑接口。自动审批拒绝正式停止/重启步骤，理由是用户尚未明确授权这次服务中断；重载未执行，等待显式授权后通过正常 CLI 应用。

## 2026-11-24：应用与外部连接分类修正

- `pnpm build` 与生成的 OpenAPI/TypeScript 契约通过；桥接响应新增 `kind`、`application_id`，未改变配对和应用注册协议。
- `pnpm test`：20 个连接器测试、239 个 Python 测试通过。新增分类回归覆盖名称与身份不一致、应用在线及断线、撤权后重启、账号绑定隔离，并确认应用仍在应用目录中。
- `CORE_TEST_BROWSER_CHANNEL=chrome pnpm --dir web test:e2e`：4 个 Chrome 场景通过。真实隔离应用桥接不出现在外部连接列表，应用仍可打开；DSH 断开且应用仍在线时，顶部显示「连接 DSH」。外部连接在明暗主题及 1440/860/390px 截图复核通过，无横向溢出；证据在 `.local/browser-tests/integrations-*.png`。
- 补充计数断言后单独重跑 `independent.spec.ts` 通过（16.9s）：DSH、飞书及独立应用同时在线时，外部连接仍为 2 个。断言等待初始目录加载完成，避免读取部分加载时的瞬时计数。
- TypeScript、Prettier、新增 Python 测试的 Ruff 检查和 `git diff --check` 通过。扩展 Ruff 检查发现 `identity_gateway.py` 既有的未使用局部变量 `session`（RUF059），本次未修改该行。总仓库结构检查仍因任务开始前已有的 Core 固定版本偏移失败（索引 `d7f5b648dde1`，工作树 HEAD `bb15fae3eb78`），本次未更新 gitlink。
- 验证使用隔离服务与账号，未操作真实配置和业务数据，未重启当前 9310 实例，未提交、推送或发布。


## 2026-11-24：SenseAudio 退役验收

- `pnpm build`、`pnpm test` 与 `pnpm test:consumer` 通过；退役专项覆盖类型拒绝、目录/默认值/密钥清理、共享凭据、失败回滚、重复启动、跨账号启动及旧数据导入。
- 旧任务保留输入、输出和供应商回执，未完成任务中断，HTTP/WS 手动恢复与界面重试均被拦截。实时语音兼容接口返回空选项或明确的能力不可用响应。
- 六工坊构建、26 项 Node 测试与 287 项 Python 测试通过。公开契约与生成客户端不再包含退役供应商枚举，历史任务的供应商标识仍可读取。
- Chrome 的 4 项端到端测试通过；隔离旧配置夹具验证七类连接在启动后移除，协议菜单保留方舟等其他提供方，目录在明暗主题和 1440/800/390px 下可读。截图位于本地 `.local/browser-tests/retired-catalog-*`，不包含真实账号数据。
- 正常启动器已用于加载本机源码变更；真实目录只做授权清理与只读核对，没有创建验收账号、提交任务或执行付费供应商调用。整套真实供应商付费生成未执行，来源审计记录保留。
