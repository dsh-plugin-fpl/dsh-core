# 职责

- `python/dsh_core_worker/`：业务执行、配置、任务、会话、资源、凭据与 HTTP/WS 装配。`extracted/` 保存来源明确的供应商和本地协议闭包。
- `web/`：独立看板，保留 FPL 的主题、基础组件和功能页面。业务通过 Core API 访问，不导入 FPL 源码或账号服务。
- `src/`：DSH 连接层与公开类型；仅通过提供方公开启动器管理进程、不持有独立 Core 的业务数据库；仅拥有当前 DSH profile 的集成导入副本和宿主注册。
- `scripts/`：独立构建、契约生成、运行与发布入口。
- `python/tests/`、`tests/`、`web/e2e/`：隔离协议、网络连接与真实页面验收。
