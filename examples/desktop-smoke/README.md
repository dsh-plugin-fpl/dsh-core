# Desktop consumer smoke test

这是通过 `ctx.dshCore` 调用 Harness 原生 Flash 模型的独立消费插件。启用时执行一次短请求，结果写到 `$DSH_HOME/.runtime/dsh-core-verification/desktop.json`。

先安装并启用 dsh-core，再将此目录打包，在 Harness「插件 → 添加插件」中安装并启用。看到 completed 和 `DSH_CORE_HARNESS_OK` 后即可在插件管理器卸载此验证插件。测试报告与 core 任务回执保留在运行目录。

此示例只调用公开的 `dshCore`、`llm`、`profileContext` 服务，使用 Harness 已登录的模型连接。
