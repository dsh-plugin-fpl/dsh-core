# 产品内嵌项目文件

公共 UI 的产品模式移除文件管理导航和首页入口；独立 Core 看板保留旧行为。`/core/files` 与 `/core/legacy-files` 展示旧文件整理，`/core/project-files?project=<UUID>` 展示对应项目附件。设置中的整理入口对已登录产品账号开放；机器数据目录仍仅初始所有者可见。

## 产品接口

产品在自己的网关中调用公共 `install_project_files`，传入自身公开路由目录、已装配传输与认证适配器。公共实现只通过公开业务接口验证项目与文件，不访问工坊数据库。

- `GET /v1/app/file-projects`：当前账号可访问项目，返回 `{items:[...]}`；思绪使用知识库，演示列表遍历分页。
- `GET /v1/app/project-files?project_id=<UUID>`：项目附件与归档回执。每次核对项目访问权限。
- `POST /v1/app/project-files`：请求 `{project_id,source,source_id}`；来源为 `core` 或 `application`。验证写入作用域、项目及源文件，成功后持久保存引用。
- `GET /v1/app/project-file-content?project_id=<UUID>&source_id=<UUID>`：先校验项目引用，再读取 Core 原件；测试记录隐藏不会让有效项目引用失联。
- `DELETE /v1/app/project-files`：相同请求结构，只移除项目引用，原件保留。
- `GET /v1/app/legacy-application-files`：通过公开文件夹与文件接口枚举旧公共素材，包含嵌套目录。
- `GET /v1/app/legacy-file-receipts`：当前账号的归档回执。

以上接口沿用产品网关的会话、Host、Origin 和 CSRF 边界；账号不接受请求参数指定。Core 文件保留原资源 ID；应用旧素材通过维护入口流式复制到当前账号的不可变资源，按来源 ID 保存可重试回执。原件不移动、不覆盖同名文件。Core 资源按摘要核验，应用素材流式读取并核对长度、记录摘要。项目副本独立于旧素材清理。回执包含来源元数据，并按项目、来源和文件 ID 去重；中断后可以重试。项目已删除时，仍可从旧文件整理再次关联原件。

## 数据与部署

只写当前内嵌 Core 账号的 `product-project-file-receipts` 配置。旧文件数据库、目录和原件保留；不对真实数据执行自动批量归档，不扫描其他应用或旧独立 Core 目录。旧独立 Core 数据先通过已有显式迁移入口导入目标工坊，再整理。

开发时六工坊选择本工作区 Core，并刷新公共 UI 依赖后构建。固定版本发布需先发布包含 `fpl_core.project_files` 的 Core，再更新工坊锁定提交；当前未提交的工作区源码不是可独立发布的固定版本组合。

## 验证

- Core 文件库与内嵌服务回归，加上视觉工坊的归档、重复执行、源文件删除及账号隔离测试，共 13 项。
- `tests/integration/project_files.py` 使用六个工坊各自的运行环境核对真实项目/旧文件接口，包含未登录与不存在项目的拒绝路径。
- 视觉工坊 `node tests/project-files-browser.mjs` 使用临时账号与真实 Chrome，覆盖旧链接、归档、项目编辑器入口、附件上传及移除引用，截图在本仓库 `.local/project-files-proof/`。可设置 `FPL_FILE_TEST_WEB_ROOT` 指向隔离构建的 Web 目录。
- 六工坊前端在临时源代码副本中安装冻结依赖并构建，避免并行安装流程改动共享 `node_modules`。
