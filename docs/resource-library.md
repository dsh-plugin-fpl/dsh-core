# GitHub 资源库协议

Core 提供资源源读取和本地安装，资源内容由各来源维护。六个工坊复用公共 Core；不读取相邻仓库或其他应用的数据库。不要求 FPL 网站账号或 GitHub 凭据。首轮只读取公开 github.com 仓库。

## 本地 HTTP 接口

所有接口沿用当前账号会话，写接口要求 `configuration.manage` 和既有 CSRF 校验。产品内嵌使用 `/v1/core/resource-sources` 等路径，由公共 CoreHttp 适配，无需每个产品重复实现。

- `GET /v1/resource-sources`：来源清单，默认 `https://github.com/EmiyaredA/fpl-resources`。
- `POST /v1/resource-sources`：`{url, ref?, name?}`。支持仓库、`tree` 目录及 `blob/.../SKILL.md` 链接；先校验远端再保存。ref 为空使用默认分支；含 `/` 的分支须显式填写完整名称。
- `DELETE /v1/resource-sources/{id}`：移除来源，保留本地副本、缓存和回执。
- `GET /v1/resource-sources/{id}/catalog`：读取缓存目录，无缓存时获取。返回完整 revision、items、校验 errors、refresh_error、refreshed_at。
- `POST /v1/resource-sources/{id}/refresh`：解析分支为完整提交 SHA，重新校验目录。失败保留旧目录，并返回 refresh_error。界面打开目录时对超过五分钟的缓存后台刷新。
- `GET /v1/resource-sources/{id}/items/{key}?revision={SHA}`：固定快照详情、文本入口、声明的预览与已解析固定版本依赖。
- `GET /v1/resource-sources/{id}/items/{key}/file?revision={SHA}&path={相对文件}`：仅返回清单声明的文件；主动内容不以内联 HTML/SVG 执行。
- `GET /v1/resource-library/imports`：当前账号导入回执。
- `POST /v1/resource-library/imports`：`{source_id, revision, resource_key, digest, names?, configuration?}`。names 是 resource_key 到本地名称的映射；configuration 是 resource_key 到可选本机 `command`、`arguments`、`url` 的映射。凭据由用户随后在集成中心配置。

目录和下载绑定相同完整 Git 提交，文件按 Git blob 摘要校验；同一资源版本内容发生变化拒绝替换。依赖需在同一快照中有固定 id/version，循环或缺失依赖不安装。单来源最多 300 项、单资源最多 1000 文件；Git 树最多 30000 项，截断目录拒绝使用。文件上限 64 MiB，导入展开上限 100 MiB，Skill ZIP 上限 20 MiB，继续使用 Skill 目录自身的校验限制。排除路径越界、符号链接、环境文件、密钥和运行状态。

导入到当前账号的本地集成目录，默认停用。导入与回执在同一事务中提交，文件放置失败补偿；重复点击返回已有回执，已删除副本可重新导入。新版要求独立名称，不改写用户已有配置。回执记录来源仓库、完整提交、资源版本、内容摘要及本机集成 ID；移除来源不删除回执。

## 来源与配方格式

兼容 fpl-resources 当前 `source.json` 的 `0.1-draft` 与 `resource.yaml` 清单，直接读取固定提交文件，不要求另行启动看板或生成下载网站。清单列出 id、name、type、version、description、license、status、format、entry、files；可声明 tags、targets、origin、previews、dependencies。Skill 的 files 必须包含标准 SKILL.md 与所需附件。普通仓库没有资源清单时，按 SKILL.md 所在目录识别独立 Skill，排除嵌套的其他 Skill 目录。

集成资源使用 `type: integration`、`format: fpl-integration-v1`，entry 指向 JSON 配方：

```json
{"kind":"tool_api","config":{"name":"示例 API","method":"GET","url":"https://example.com/api","auth_kind":"bearer"}}
```

```json
{"kind":"mcp","config":{"name":"示例 MCP","transport":"stdio","command":"example-mcp","arguments":[]}}
```

兼容现有 PortableIntegration 的 `kind: tool-apis`，以及 config/manifest 字段。来源的 secret、bearer_token、headers、environment、cwd、enabled 不导入；请求模板中的固定凭据拒绝安装。stdio 配方缺少本机命令时，在详情本机配置中填写，再导入停用副本。不运行命令、不验证远端、不导入来源凭据。

模板、工作流、预设、素材及方案包首轮只浏览，不冒充已完成工坊业务导入。草稿保持原状态与许可说明，导入不执行公开发布。预览按现有 content_sha256 算法核对，过期预览不展示；来源没有关联摘要时明确标记未校验。

## 验证与交付

提供方测试 `python/tests/test_resource_library.py` 覆盖 GitHub 固定读取、摘要、失败缓存、依赖事务、重复导入及账号边界；`python/tests/test_creative_connection.py` 继续验证旧接口兼容。六工坊经真实产品边界验证页面和安装结果，远端夹具与真实 GitHub 验证分别记录。

本地联合开发可直接构建当前 Core 公共 UI，并重新安装六工坊的 file 依赖。独立克隆的固定版本只有在提供方提交推送后更新 fpl-core.lock.json 才会改变；本轮不自动提交、推送或发布资源。
