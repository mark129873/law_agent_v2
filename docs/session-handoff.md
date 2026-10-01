# 会话交接 -- 在当前会话完成前, 记录本轮会话信息与下一轮推荐目标的交接文档

## 当前已验证

- 现在明确可用的部分：
  - 后端 10 功能 + 前端 8 功能全部 passing（docs/feature_list.json 共 18 项）
  - 前端已有路由：`/` 欢迎页、`/session/:id` 会话视图；刷新/直链按地址恢复回放；未知与已删地址回首页，应用内新建的草稿留在空对话态
  - 三栏壳层对齐 ZCode 工作台语义：左栏头部按钮开合（localStorage 记忆 `ui.sidebar`），右栏 subtask 面板点卡打开/X 收起/再点恢复
  - subtask 子循环 token 已计入会话统计与 Langfuse（metadata 带 subtask_id）
  - 前端分包：markdown（react-markdown+highlight）在异步包，主包 284kB，无 Vite 告警
- 这轮实际跑过的验证：`uv run pytest -q` 71 passed；`npm run build` 无告警；清库真实启动 health/空列表；浏览器实测四态截图（tmp/gui-verify/，含侧栏收起刷新持久化）；UI 回归靠截图视觉验收（本机 IAB 的 Playwright 点击不可靠，用 evaluate 触发）

## 本轮改动

- 修复了哪些缺陷：
  - docs/clean-state-checklist.md 顶部是 v1 遗留核对结果（Session 065/276测试/Milvus/RAG），已重写为 v2 自包含清单
  - **布局回归（本轮踩坑）**：路由重构曾把三栏 flex 容器写成 Fragment，侧栏与主区垂直堆叠；DOM 断言查不出，截图发现后已修复。教训已写入 progress.md：UI 改动必须截图做视觉验收
- 新增功能：路由与刷新恢复（FE-006）、subtask token 计入（BE-011）、代码分包（FE-007）、左/右栏可收起壳层（并入 FE-006）

## 仍损坏或未验证

- 当前blocker：无
- 已知缺陷和风险：
  - subtask"执行中→已完成"翻转瞬间仍未直接观测（机制等价已验证）
  - 历史消息全量加载，超长会话可能卡（分页/虚拟化留 v2）
  - failed 轮回放块头显示"已 stopped"（语义可再分）
  - 本机 IAB 自动化环境：Playwright locator 点击频繁 actionability 超时（evaluate 触发正常，产品无碍；GUI 回归时注意）
- 未验证路径：无
- 下一轮会话需要注意的风险：改 models.py 表结构后删除 backend/data/ 重启重建（不做 schema 迁移）；测试后确认 tests/.tmp-data 已清理（Windows 文件锁）；改前端布局必须截图验收

## 下一步最佳动作

- 最高优先级未完成功能：真实使用打磨——工具卡 autoOpen、长会话分页/虚拟化
- 为什么它是下一步：18 项功能全部 passing 且无未验证路径，进入打磨期
- 什么结果才算 passing：按 feature_list 对应项补充证据（分页需大数据量会话实测）
- 这一步中哪些东西不要动：存储层契约（四表/回放结构）、SSE 事件类型、工作块状态机、_turn_sse_response 的单队列约定（审批事件依赖它）、ShellContext 的 Provider 包裹层级（掉了会复现布局堆叠）

## 命令

- 启动命令：后端 `cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8100`；前端 `cd frontend && npm run dev`（http://localhost:5173）
- 验证命令：`cd backend && uv run pytest -q`；`cd frontend && npm run build`
- 定向调试命令：后端日志 `backend/data/logs/app.log`；LLM 配置校验失败看 POST /turn 的 400 detail；路由验证可用 `tmp/make_session_fixture.py` 直造夹具会话（不烧 LLM）
