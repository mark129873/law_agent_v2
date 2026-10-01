# 干净收尾状态检查清单

结束会话前逐项核对。核对结果按会话记录在 `docs/progress.md`，本文件只保留可复用的检查项本身。

## 启动与测试验证

- [ ] 执行 `docs/init.md` 的内容，确保项目可正常构建或启动，下一轮会话可以直接运行项目
- [ ] 按 `docs/RELIABILITY.md` 的测试干净环境管理执行测试（pytest 用 `backend/tests/.tmp-data/`；浏览器手测/E2E 前先清空 `backend/data/`），确认全部通过，包括但不限于：
  - 单元测试 / 集成测试 / 接口测试：`cd backend && uv run pytest -q`
  - 前端构建：`cd frontend && npm run build`
  - 端到端测试（涉及 UI 或交互行为改动时浏览器实测）
- [ ] 测试后确认 `backend/tests/.tmp-data/` 已清理（Windows 文件锁可能残留）

## 文档同步

- [ ] `docs/progress.md` 记录到当前会话的进度
- [ ] `docs/feature_list.json` 功能状态与实际开发进度一致，真实反映 passing 和未验证的边界（passing 必须附验证证据）
- [ ] `docs/session-handoff.md` 确认记录当前会话的交接摘要，以及仍未解决的风险或 blocker
- [ ] 涉及架构或用户可见行为改动的，`docs/ARCHITECTURE.md` 与 `docs/PRODUCT.md` 已同步更新
- [ ] 没有任何半成品步骤处于未记录状态

## 冷热分层沉降

- [ ] `docs/progress.md` Session 数 ≤ 15，超出时把最旧会话条目移入 `docs/archive/`
- [ ] `docs/feature_list.json` passing 条目 ≤ 40

## 代码仓库状态

- [ ] git 状态中无意外新增文件（未跟踪文件要么入库、要么进 .gitignore、要么移入 tmp/）
- [ ] 没有提交敏感文件（`.env`、密钥凭证）
- [ ] `frontend/dist/`、`frontend/node_modules/`、`backend/data/`、`backend/tests/.tmp-data/` 未被提交（运行日志在 `backend/data/logs/` 下，随 `data/` 一并 ignore）
- [ ] `tmp/` 临时文件目录不入库
