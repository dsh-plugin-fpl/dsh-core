# 统一存储与显式迁移

本机所有者在看板顶部的“存储”入口打开 `/storage`。默认建议目录为 `~/Library/Application Support/DSH-FPL`；预检只读取数据并生成计划，确认迁移后正常停写、复制与校验，恢复 Core/工坊连接。完成前暂不接收业务写入，原目录保留。

根目录下 `core/` 保存 Core 各账号状态，`workshops/<稳定应用 ID>/` 保存工坊自有业务。代码、Web 构建、解释器与部署控制在安装仓库 `.runtime/`。机器定位文件为 `~/Library/Preferences/dsh-fpl-storage.json`，只保存版本、路径、实例标识和插件凭据路径重定向，不保存密钥。已配置磁盘缺失或实例标识错误时拒绝启动，不创建空库替代。

`--root` / 工坊的 `--state-root` 显式覆盖用于隔离安装和测试。测试可设置 `DSH_FPL_STORAGE_CONFIG` 和 `DSH_FPL_STORAGE_CONTROL`，不得使用真实定位或控制状态。新增工坊的正常初始化入口按定位文件分配自己的子目录；后续迁移前必须通过公开维护入口登记，发现未登记目录会停止预检。

## 公开维护入口

Core 命令通过 `node scripts/core.mjs <命令>` 运行：

- `storage-status`：查看位置、参与者和任务。
- `storage-register --descriptor <文件>`：登记提供方的 v1 存储描述（id/module/python/repository/root/port/files/bytes）。描述不含密钥。
- `storage-register-source --descriptor <文件>`：登记显式旧 FPL 来源，并通过其公开 CLI 读取范围。
- `storage-plan --target-root <空绝对目录> --revision <当前版本>`：冻结预检。只有显式 `--include-legacy` 才包含旧来源。
- `storage-migrate --id <计划 ID>`、`storage-job --id <ID>`、`storage-resume --id <ID>`、`storage-cancel --id <ID>`：执行、查看、中断后恢复或取消。
- `storage-export --root <离线源> --output <目标> --receipt <文件>`：由 Core 自己导出运行状态。
- `storage-import --root <离线目标> --bundle <公开包> --primary-owner <来源主账号> --transfer-key-file <私有密钥文件> --receipt <文件>`：由 Core 自己导入来源。

HTTP 对应 `/v1/storage`、`/plans` 和 `/migrations`，正式定义由 OpenAPI 提供。只允许已验证的本机初始所有者管理机器存储；其他管理员同样受此限制。来源、CSRF 和账号隔离继续生效。

工坊提供 `storage-location/info/check/export/import/verify`，只处理自己声明的表和原件；Core 协调器不打开工坊数据库。DSH 连接器读取公开定位合同，将旧 `plugin.json` 路径解析到当前副本，保留现有配对。

## 完整性、恢复与旧来源

复制使用有界流或独立 APFS COW 文件，检查长度和 SHA-256。SQLite 用所属入口的一致性快照与完整性/外键检查，旧账本不改写。计划绑定存储配置版本；回执、迁移前定位信息及进度在安装目录 `.runtime/storage-control/`，独立进程可跨 Core 重启继续。

健康检查前维持新目录停写。完成验证后先提交切换检查点再开放写入；之后禁止自动回退新目录，应恢复任务完成服务核验。取消在检查点前恢复原目录，已结束的回退任务重新预检，不重用旧副本覆盖新增内容。

旧 FPL 通过其来源维护入口交接。主要来源账号明确映射到当前 Core 所有者，其他账号保持隔离并兼容旧 Argon2 登录；不导入浏览器会话。凭据在提供方内重新加密。模型会话、附件、调用日志和任务保留，未知/未完成历史不自动执行。Core 文件页提供目录、搜索及游标分页；维护导入支持大于正常上传限额的原件，日常上传限制保持原有约束。未引用和升级保留的文件放私有恢复归档。

不符合当前连接规则的旧公网上 HTTP 地址保留在私有历史中，活动连接使用停用占位地址，需由所有者重新配置。源站已经清理的临时上传显示缺失说明，已有转写和日志继续保留。模型权重与下载迁入，解释器由所属安装器按已校验缓存重建。源站恢复后不做双向同步。

`storage-import-models` 是停写且已停止的 Core 的补充模型入口：读取来源公开模型包并校验后，写入目标自己的模型目录，持久记录独立回执；恢复任务复用已完成业务导入，不重复导入项目。来源导出先核对实际原生 Worker 的模型根目录与已安装清单，避免误选旧缓存。
