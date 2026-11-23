# dsh-core

FPL 面向 DeepSeek Harness 的公共能力底座。当前仅完成仓库文档初始化，尚无可安装包、运行时实现、公开 API 或业务测试。

## 规划方向

候选公共模块为应用与能力注册、公共配置、身份授权适配、模型接入、Agent 接入、资源交接、任务恢复以及宿主装配。每项能力的归属与接口需通过真实应用验证；该清单不构成已实现功能或稳定接口承诺。

文件库、生成引擎、渲染器和各工坊业务不默认放入 core。其他插件通过未来明确的公开包、Service 或 API 接入，不引用 core 私有实现。

## 开发

独立克隆：

```sh
git clone https://github.com/dsh-plugin-fpl/dsh-core.git
cd dsh-core
git status --short --branch
```

先读 [AGENTS.md](AGENTS.md)。目前无需安装依赖，没有可运行的构建与功能测试；文档变更检查内容、链接和 `git diff --check`。引入实现时必须同步补充真实命令、接口与验证。

整套开发使用 [my-dsh-plugins](https://github.com/dsh-plugin-fpl/my-dsh-plugins)，从其根目录启动 Agent 并显式读取两层规则。单独开发本仓库不依赖父目录或本机 FPL 源码。

本仓库尚未选择许可证，公开可见不代表已授予开源许可。
