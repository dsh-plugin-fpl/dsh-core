# 验证与交付

运行 `pnpm build`、`pnpm test`；页面改动运行 `pnpm --dir web test:e2e` 并检查明暗主题及宽屏、并排窗口、窄屏。接口变更运行 `pnpm contracts:generate`，生成物不得手改。公开类型变更验证 examples 的独立编译。连接层修改使用 tarball 在隔离 Harness profile 验收。

架构和运行约定变化更新 docs/decisions.md。同时遵守 my-dsh-plugins 总仓库规则，先交付 core，再更新组合引用。显式暂存本次文件；提交、推送与正式安装按用户授权执行。

本机正式看板使用 `.runtime/assets/brand/` 的既有品牌包时，构建与验证使用 `pnpm build:local`，交付前确认 logo 与首页角色图片/动画正确。不要用通用 `pnpm build` 覆盖本机运行页面。通用分发、候选包及发布仍使用不含私人品牌资源的默认构建。
