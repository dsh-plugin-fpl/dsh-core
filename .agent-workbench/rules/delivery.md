# 验证与交付

- 按改动选择 `pnpm test:connector`、`pnpm test:python`、`pnpm typecheck`；公开类型改动运行 `pnpm test:consumer`，接口改动运行 `pnpm contracts:generate`，不手改生成物。
- 本机已装品牌包时用 `pnpm build:local`，检查原 logo 与角色图片／动画；通用分发使用 `pnpm build`，不携带个人品牌资源。页面改动按需运行 `pnpm --dir web test:e2e`，核对明暗主题与宽窄屏。
- 连接层安装变更用隔离 Harness profile 和实际 tarball 验证；重要决定更新[决策记录](../../docs/decisions.md)。联合工作区交付时先固定 Core 提交，再核对总仓库 gitlink；不将未验证能力写成已实现。
