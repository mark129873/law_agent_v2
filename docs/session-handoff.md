# 会话交接 -- 在当前会话完成前, 记录本轮会话信息与下一轮推荐目标的交接文档

## 当前已验证

- 现在明确可用的部分：
  - 后端 12 功能 + 前端 8 功能：BE-010(Langfuse) deprecated，BE-012(model-io JSONL)/BE-013(用量聚合) passing
  - 观测三层分工（ARCHITECTURE §3.0）：SQLite 会话事实（含 turn 级 input/output/context 三级 token 字段与会话双列累计）+ model-io JSONL（逐调用全量快照，`log/`）+ app.log 工程事件
  - compact 预算已激活真实 input（deps.last_input_tokens 由主循环 usage 事件喂入，原死路径）；前端 TokenBadge 显示真实上下文占用（context_used/context_window+百分比）
  - regenerate 回滚后由 recalc_session_usage 重算双列（修复被删轮 token 双算的潜伏 bug）
  - **回放审批留痕去重**：同一 request_id 只渲染最终状态一张卡（修复回放出现"等待批准"幽灵卡，与实时流单卡翻转一致；DB 仍双写事实）
  - **侧栏新建草稿弹回修复**：Sidebar 创建路径漏登记 draftIdsRef 导致草稿页 404 弹回首页，已统一收敛到 handleDraftCreated（App.tsx）
  - **UI 质感升级**：Composer 一体化浮动输入卡、欢迎页重排、侧栏选中竖条、token 层（tabular-nums/细滚动条/入场动画/圆角体系）、审批弹窗打磨——圆角与强调色体系见 ARCHITECTURE §7
  - **E2E 全量回归通过**：mock LLM（tmp/mock_llm_server.py，零成本）驱动真实前后端 17 项场景——流式/markdown/工具执行/任务板/subtask 双路径/审批批准+拒绝/错误重试/停止/409/刷新恢复/跨会话并行/删除/Enter 系/model-io/用量聚合，全部通过（截图 tmp/gui-verify/e2e-*.png）
  - 前端已有路由：`/` 欢迎页、`/session/:id` 会话视图；刷新/直链按地址恢复回放；未知与已删地址回首页，应用内新建的草稿留在空对话态
  - 三栏壳层对齐 ZCode 工作台语义：左栏头部按钮开合（localStorage 记忆 `ui.sidebar`），右栏 subtask 面板点卡打开/X 收起/再点恢复
  - 前端分包：markdown（react-markdown+highlight）在异步包，主包 284kB，无 Vite 告警
- 这轮实际跑过的验证：`uv run pytest -q` **70 passed**（model-io 3 例 + 用量聚合 2 例新增）；`npm run build` 无告警；清库真实启动 + fixture 会话浏览器实测徽标（tmp/gui-verify/v4-usage-badge.png）；UI 回归靠截图视觉验收（本机 IAB 的 Playwright 点击不可靠，用 evaluate 触发）

## 本轮改动

- 修复了哪些缺陷：
  - docs/clean-state-checklist.md 顶部是 v1 遗留核对结果（Session 065/276测试/Milvus/RAG），已重写为 v2 自包含清单
  - **布局回归（Session 004 踩坑）**：路由重构曾把三栏 flex 容器写成 Fragment，侧栏与主区垂直堆叠；DOM 断言查不出，截图发现后已修复。教训已写入 progress.md：UI 改动必须截图做视觉验收
  - **regenerate token 双算（潜伏 bug）**：recalc_tokens_used 原是孤儿函数从未接线，回滚删事实后 session 累计不修正；已重写为 recalc_session_usage 并接线
- 新增功能：路由与刷新恢复（FE-006）、subtask token 计入（BE-011）、代码分包（FE-007）、左/右栏可收起壳层（并入 FE-006）、model-io JSONL 逐调用记录（BE-012，替代 Langfuse）、用量聚合（BE-013）
- 移除：Langfuse SDK 及全部接线（obs/config/main/pyproject/.env.example/test_obs.py）

## 仍损坏或未验证

- 当前blocker：无
- 已知缺陷和风险：
  - 历史消息全量加载，超长会话可能卡（分页/虚拟化留 v2，设计决策）
  - 纯文本轮（无工具）不渲染工作块头——设计使然，若要"每轮都有块头"需产品决策
  - 本机 IAB 自动化环境：Playwright locator 点击频繁 actionability 超时（evaluate 触发正常，产品无碍；GUI 回归时注意）
- 未验证路径：无
- 下一轮会话需要注意的风险：改 models.py 表结构后删除 backend/data/ 重启重建（不做 schema 迁移）；测试后确认 tests/.tmp-data 已清理（Windows 文件锁）；改前端布局必须截图验收；真实跑 turn 会写 log/（含对话正文，勿外传勿入库）

## 下一步最佳动作

- 最高优先级未完成功能：真实使用打磨——工具卡 autoOpen、长会话分页/虚拟化
- 为什么它是下一步：功能清单全部 passing/deprecated 且无未验证路径，进入打磨期
- 什么结果才算 passing：按 feature_list 对应项补充证据（分页需大数据量会话实测）
- 这一步中哪些东西不要动：存储层契约（四表/回放结构）、SSE 事件类型、工作块状态机、_turn_sse_response 的单队列约定（审批事件依赖它）、ShellContext 的 Provider 包裹层级（掉了会复现布局堆叠）、modelio.py 的吞异常纪律（观测不能挡主流程）、recalc_session_usage 与 regenerate 的接线（删了会复发 token 双算）

## 命令

- 启动命令：后端 `cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8100`；前端 `cd frontend && npm run dev`（http://localhost:5173）
- 验证命令：`cd backend && uv run pytest -q`；`cd frontend && npm run build`
- 定向调试命令：后端日志 `backend/data/logs/app.log`；逐调用 LLM 快照 `log/model-io-<session_id>.jsonl`；LLM 配置校验失败看 POST /turn 的 400 detail；路由验证可用 `tmp/make_session_fixture.py` 直造夹具会话（不烧 LLM）
