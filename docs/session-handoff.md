# 会话交接 -- 在当前会话完成前, 记录本轮会话信息与下一轮推荐目标的交接文档

## 当前已验证

- 现在明确可用的部分：
  - 后端 10 功能全部 passing（BE-1~BE-10，见 docs/feature_list.json）：会话四表存储、CRUD+resume、agent 工具循环（6 基础工具+todo/skill/subtask）、SSE 流式、交互审批、ZCode compact+microcompact、重新生成、logging+Langfuse（默认关）
  - 前端 5 功能全部 passing（FE-1~FE-5）：三栏布局、会话列表（呼吸点/删除确认）、TurnGroup 工作块（ZCode 状态机+codex 耗时）、流式聊天+Markdown、审批弹窗+重试、subtask 面板+聚合组
  - E2E 浏览器联调通过：新建→对话→工具写文件→刷新回看→错误重试→resume 续聊→删除
- 这轮实际跑过的验证：`cd backend && uv run pytest -q`（70 passed）；`cd frontend && npm run build`；uvicorn 真实启动 + 浏览器全流程手动操作（真实 LLM 一次短对话）

## 本轮改动

- 新增了哪些代码或行为：backend/app 全部（config/db/models/obs/main + api/sessions + agent/{loop,llm,tools,tool_exec→tools,permissions,hooks,compact,subtask,todo,skills} + sessions/{store,replay,recorder,turn_manager,approvals}）；frontend/src 全部；docs 三份文档与 feature_list 全量定稿
- 基础设施或 harness 发生了哪些变化：backend 用 uv 独立工程（pyproject+uv.lock）；SQLite WAL 四表；数据目录收敛为 data/{app.db,workspace,logs}；默认端口 8100（8000 被本机 Godot MCP 占用）

## 仍损坏或未验证

- 当前blocker：无
- 已知缺陷和风险：
  - schema 演进无版本化迁移（本次加列靠清 data/ 解决；再改表结构必须先引入迁移）
  - subtask 子循环的 token 未计入 Langfuse/会话统计
  - 历史消息全量加载，超长会话可能卡（分页/虚拟化留 v2）
  - failed 轮的块头显示"已停止"（与产品表一致但语义可再分）
- 未验证路径：交互审批 UI 仅后端状态机验证过，浏览器里未实测完整审批往返；subtask 未在浏览器实测
- 下一轮会话需要注意的风险：改 models.py 后必须处理旧 data/app.db（删库或迁移）；测试后确认 tests/.tmp-data 已清理（Windows 文件锁）

## 下一步最佳动作

- 最高优先级未完成功能：真实使用打磨——审批与 subtask 的浏览器实测补验
- 为什么它是下一步：这两条路径已有后端测试覆盖但缺 UI 实测，是仅剩的未验证路径
- 什么结果才算 passing：浏览器里完成一次"高危命令→弹窗→批准→执行"与一次"subtask→右侧面板实时输出"
- 这一步中哪些东西不要动：存储层契约（四表/回放结构）、SSE 事件类型、工作块状态机

## 命令

- 启动命令：后端 `cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8100`；前端 `cd frontend && npm run dev`（http://localhost:5173）
- 验证命令：`cd backend && uv run pytest -q`；`cd frontend && npm run build`
- 定向调试命令：后端日志 `backend/data/logs/app.log`；LLM 配置校验失败看 POST /turn 的 400 detail
