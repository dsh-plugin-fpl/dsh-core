# 公共能力与独立运行范围

## 当前组成

| 部分 | 所有者与职责 |
| --- | --- |
| 独立服务 | Python 执行器、加密凭据、连接/默认值、任务、测试会话、事件与资源 |
| 独立看板 | 复用 FPL 主题、侧栏、模型目录、智能体、本地模型、测试和日志；新增 Core 任务与外部连接装配 |
| DSH 连接层 | `ctx.dshCore`、受限任务工具、原生模型和应用工具的双向桥接、配对状态 |

第一阶段按本机个人使用交付。模型协议、本地模型和五种 CLI 沿用已迁入的实现；新服务和看板无需安装或启动 FPL、DSH。Core 已提供本机默认身份和统一账号，六个业务工坊通过公开协议独立运行。公网与云控属于后续范围。网站账号作为独立连接，为创意工坊提供公开能力发布与本地副本安装。

## 公共能力

11 个 provider kind：`local_qwen_tts`、`local_zonos_tts`、`local_relayvsr`、`local_ffmpeg`、`volcengine_image`、`openai`、`deepseek`、`anthropic`、`volcengine_video`、`zealman_video`、`autodl_video`。

五种 CLI：Codex、Claude Code、OpenCode、DeepSeek Harness、Pi，沿用各 CLI 的登录。Qwen3 TTS、Zonos3 Mini、RelayVSR 使用固定清单与下载校验；FFmpeg 使用本机程序。RelayVSR 首版限制为单次 300 帧、1080p 输入。

协议迁移来源见 [extraction.json](extraction.json)，界面来源见 [ui-extraction.json](ui-extraction.json)。迁入代码使用本包自己的依赖，不导入 FPL 的网站组合或数据库。当前从空配置开始，旧 Harness profile 数据原样保留。

## 后续扩展

保留 my-dsh-plugins 总仓库和现有 dsh-core 子模块。后续应用各自拥有业务状态、页面和发布版本，通过公开 HTTP API 或 `ctx.dshCore` 使用公共能力；应用目录只登记实际已安装入口。总仓库维护组合、依赖方向和联动验收。

## 集成管理

0.3.0 复用工具 API、MCP、Skill 与外部连接四类现有页面和协议，新增 Core 自有状态、运行回执以及 DSH 两端独立配置副本。连接列表统一承载 DSH 配对和飞书服务；应用安装与目录供给保持插件生命周期。详情和扩展边界见 [集成中心](integrations.md)。
