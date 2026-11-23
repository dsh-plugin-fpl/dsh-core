# AGENTS.md

## 工作范围

本仓库拥有独立 Core 服务、复用 FPL 界面的看板，以及可选的 DSH 双向连接层。开始前读取 README、[范围](docs/scope.md)、[接口](docs/api.md)和[决策](docs/decisions.md)，检查分支与修改，保留已有工作。任务分支使用 `codex/` 前缀。

## 职责

- `python/dsh_core_worker/`：业务执行、配置、任务、会话、资源、凭据与 HTTP/WS 装配。`extracted/` 保存来源明确的供应商和本地协议闭包。
- `web/`：独立看板，保留 FPL 的主题、基础组件和功能页面。业务通过 Core API 访问，不导入 FPL 源码或账号服务。
- `src/`：DSH 连接层与公开类型；不启动 Python、不持有独立 Core 的业务数据库；仅拥有当前 DSH profile 的集成导入副本和宿主注册。
- `scripts/`：独立构建、契约生成、运行与发布入口。
- `python/tests/`、`tests/`、`web/e2e/`：隔离协议、网络连接与真实页面验收。

## 开发规则

业务操作属于 Core。看板与连接层不得直接改数据库。应用通过公开 API/`ctx.dshCore` 登记与调用，不导入其他应用的私有实现。身份从本机已认证会话或已配对连接取得，应用 ID 由连接范围限定。

长任务保留幂等回执、取消、提交结果核对和恢复；不自动重放未知结果的付费请求。关闭 DSH 不停止 Core。宿主回调断连需明确结束并保留已有输出。

临时验证产物放 `.local/<任务>/`，创建前用 `git check-ignore` 核验；正式运行代码、构建、环境、配置、资源与恢复点放 `.runtime/`。正式入口不得依赖 `.local/`、FPL 源码路径或旧 Harness profile 数据。凭据不得输出和提交。角色资源沿用来源记录与本机交付范围。

## 验证与交付

运行 `pnpm build`、`pnpm test`；页面改动运行 `pnpm --dir web test:e2e` 并检查明暗主题及宽屏、并排窗口、窄屏。接口变更运行 `pnpm contracts:generate`，生成物不得手改。公开类型变更验证 examples 的独立编译。连接层修改使用 tarball 在隔离 Harness profile 验收。

架构和运行约定变化更新 docs/decisions.md。同时遵守 my-dsh-plugins 总仓库规则，先交付 core，再更新组合引用。显式暂存本次文件；提交、推送与正式安装按用户授权执行。
