# 干净收尾状态检查清单

## Session 065 核对结果（2026-09-20）

- [x] 标准后端启动路径、Embedding/Reranker 启动器和 `/api/health` 已验证
- [x] 全量 `pytest`：276 passed、5 skipped、1 warning；跳过项仅为本机 Milvus 集成服务不可达
- [x] 50 条真实 RAG 评测完成，报告与 Langfuse 50/50 trace 已对账
- [x] 评测导入的 5 个临时文档已按精确 ID 删除；未执行共享库 reset
- [x] `feature_list.json`、`progress.md`、`session-handoff.md`、README 和架构/产品文档已同步
- [x] 本轮未新增敏感文件；`backend/log`、`.env` 等不纳入提交范围

- 执行`init.md`的内容, 确保项目可正常构建或启动, 保证下一轮会话可以直接运行项目
- 测试先根据 `docs/RELIABILITY.md` 进行测试干净环境管理, 再确认所有测试通过, 包括但不限于:
  - 单元测试
  - 集成测试
  - 接口测试
  - 端到端测试
- progress.md 记录到当前会话的进度
- feature_list.json 功能状态与实际开发进度一致, 真实反映 passing 和未验证的边界
- session-handoff.md 确认记录当前会话的交接摘要
- 冷热分层沉降检查（规则见 AGENTS.md）：progress.md Session 数 ≤ 15、feature_list.json passing 条目 ≤ 40；
- 没有任何半成品步骤处于未记录状态

- 检查代码仓库状态
    - git 状态中无意外新增文件
    - 没有提交敏感文件（.env、密钥凭证）
    - `dist` ,` node_modules`, `data` 目录文件未被提交; `backend/log` 日志目录未被提交
