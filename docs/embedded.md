# 产品内嵌 Core

`fpl-core` 是 Python 公共包；源码及供应商实现由本仓库维护。产品通过公开 `fpl_core` 入口使用，不导入 `dsh_core_worker` 私有模块。独立 Core 服务继续作为同一实现的兼容入口。

```python
from pathlib import Path
from fpl_core import CoreHost, CoreOptions

options = CoreOptions("visual-workshop", "视觉创作工坊", Path("/owned/product/core"))
async with CoreHost(options) as core:
    # token 必须来自已认证产品会话，不能以请求传入的账号 ID 代替。
    result = await core.call(token, "capabilities", {})
```

构造和导入不写入数据、不启动线程或监听端口；`prepare()` 是显式状态初始化，异步上下文管理后台执行资源。产品的数据目录由宿主传入，每个账号仍使用隔离的运行状态。`call()` 每次验证会话，复用现有授权、幂等、任务和资源语义；本机连接作用域在重启后保持稳定。

`provision_product()` 与 `local_product_session()` 是可信安装器／启动器入口，不得直接开放给网络请求。桌面启动入口须使用一次性随机凭据和来源校验。`call_account()` 只供产品内部已经核验身份的持久后台任务调用，持续检查账号及产品授权。

`CoreHttp` 把公共 HTTP 操作装配到产品 `/v1/core`，由产品先验证来源、CSRF 和会话，再在进程内转交既有 Core 边界；不建立回环网络连接。Core 会话 Cookie 不输出给产品浏览器。产品身份调用由 `identity_request()` 走进程内 ASGI 适配。

## 公共前端与桌面运行

`packages/core-ui` 导出 `CoreWorkspace` 和 `configureCorePlatform`。产品传入自己的 API 前缀、路由前缀和 CSRF Cookie 名；React 与 QueryClient 使用产品提供的 peer dependencies。构建命令为 `pnpm --dir web exec vite build --config vite.library.config.ts`。

`packages/desktop-runtime` 提供 Electron 生命周期。产品声明 Python 入口、页面、数据目录和端口；窗口启用 context isolation 与 sandbox。关闭窗口保留应用，退出应用发送正常停止信号。已实现草稿与活动任务退出提示，并验证视觉产品启动和正常退出。

`python scripts/package-desktop.py --help` 是 macOS arm64 构建入口。候选包含可搬移 Python、锁定生产依赖、公共 wheel、已构建界面和 Electron；本地 ad-hoc 签名不代表 Developer ID 签名或公证。媒体工具与非系统动态库随包交付，视觉候选已通过不依赖 Homebrew PATH 的编码检查。

## 产品装配与迁移

`fpl_core.adapters` 提供身份适配、同进程能力池及初始化；`fpl_core.desktop.install_routes` 装配产品的一次性桌面会话、密码登录、公共 HTTP 前缀及只读产品存储位置。必须先通过产品自身的 Host、Origin、会话与 CSRF 中间件。`provision_product(relocate=True)` 仅供可信本机启动器，校验既有产品凭据后更新回调端口，不改变账号授权。

公共 UI 配置传入完整产品名和 `projectsUrl`；“项目”点击后直接打开产品列表，不渲染旧应用中心。进程内模式禁止独立 Core 的全局存储搬迁接口，产品状态须作为整体管理。

六工坊已接入公共包、公共页面和独立桌面。公开 `export_product` / `verify_snapshot` 维护 Core 离线快照、账号与 scope 映射、凭据轮换和校验；各工坊只处理自己的项目数据。迁移测试通过正式业务接口创建并恢复项目、素材及资源，也覆盖转换中断、来源后续写入、目标新写入与发布后回执恢复。

最终桌面候选版本统一为 0.2.0。报告由联合工作区 TestWithProof 保存，截图留待人工审阅。新桌面的跨产品授权与 DSH 直接端点仍待组合验收；现有独立服务及 DSH 兼容入口保留。正式公开分发、公证和用户真实数据切换不在本轮自动执行。
