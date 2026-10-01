# Architecture -- 架构

技术栈:
- 后端：Python 3.12 + FastAPI + uv 环境管理 + SQLite + SQLAlchemy 2.0
- 前端：React 18 + TypeScript + Vite + Tailwind CSS v4

参考资料：`.github_ZCode/`（会话存储 + 工作块 UI 形态）、`.github_codex/`（耗时格式与事件设计）、`scripts_mini_harness/mini_harness.py`（agent 循环思想）。

## 1. 总体数据流

```
浏览器 (React)
   │  POST /api/sessions/{id}/turn    POST .../approval   POST .../stop
   │  ┌──────────── SSE 事件流 ────────────┐
   ▼           ▼                   ▼
FastAPI (uvicorn, 127.0.0.1:8100)
   │
   ├─ sessions/store.py         # SQLite 四表：会话内容的唯一事实源（ZCode 同款）
   ├─ sessions/replay.py        # 从四表读出并拼装成回放结构（全量）
   ├─ sessions/turn_manager.py  # 会话→进行中 turn 注册表、停止、审批唤醒
   └─ agent/                    # 主循环、工具、权限、hooks、压缩、subtask
        │
        └─ Anthropic 兼容 API（流式，thinking 关闭）
```

## 2. 会话存储（ZCode 式：单库 SQLite 四表为唯一事实源）

### 2.1 数据库
- 库文件 `backend/data/app.db`，SQLite WAL + busy_timeout + foreign_keys，所有会话共库。表结构演进约定（产品决策 2026-09-28）：不做 schema 迁移，改 models.py 后删除 data/ 重启即全新建表。
- 四张实体表（照搬 ZCode `session-store` 形态，`data` 列存 JSON 字符串）：

| 表 | 列 |
|---|---|
| `session` | id(PK), title, model, created_at, updated_at, deleted_at(软删), tokens_used |
| `message` | id(PK), session_id, sequence, role(user/assistant), data(JSON), time_created, time_updated |
| `part` | id(PK), message_id, session_id, sequence, kind(text/tool_call/subtask/todo), data(JSON), time_created, time_updated |
| `session_entry` | id(PK), session_id, type(turn/approval/compaction/context), data(JSON), time_created |

### 2.2 写入机制（ZCode 同款：里程碑驱动实时 upsert）
- 全部 `insert ... on conflict(id) do update`；`sequence` 首次取 max+1、冲突时原样保留（防时间线漂移）；每次写后 touchSession 更新 `updated_at`。
- 用户消息提交时写 message；assistant 每个模型步开始即建 message 行；text part 在该步响应结束时**整段写入**（流式 delta 不落库）；工具 part 按生命周期逐态 upsert 同一 partID（pending→running→completed/failed/denied，data 里带输出与耗时）。
- **turn 事实**（工作块数据源）：`session_entry(type='turn')` = `{turn_id, started_at, ended_at, active_ms, state}`。`active_ms` 为服务端记账的权威工时（**排除等待审批的时间**），turn 收口时一次性写定；前端历史耗时只取落盘事实，禁止用当前时钟推算。
- 其余事实：`session_entry(type='approval')`（请求与决定留痕）、`type='compaction'`（压缩边界信息）、`type='context'`（每 turn 的 model/max_tokens 快照，系统提示词只影响其后轮次）。

### 2.3 回放（全量）与 resume
- **回放**：GET /api/sessions/{id} 后端从四表按 sequence 读出，按 turn 分组拼装为「用户消息 → 工作块条目 → 最终回复」结构一次性返回；游标分页/虚拟化留 v2。
- **resume**：按 sequence 重建模型可见历史，compact 边界（摘要消息）之后的部分才发给模型；`tokens_used` 从回放重算。
- **软删**：DELETE 置 `deleted_at`；列表与回放一律过滤已删会话。

### 2.4 目录约定（全部 gitignore）
```
backend/data/
  app.db             # SQLite（含 -wal/-shm）
  workspace/         # 工具沙箱根目录（safe_path 限制在此）
  logs/              # 滚动日志
```

## 3. Agent 核心（移植 mini_harness + 流式化 + 可中断）

主循环（`agent/loop.py`）：用户输入 → 压缩检查 → Anthropic 流式调用（text delta 实时推 SSE）→ 无 tool_use 且 Stop hook 无注入则结束；有 tool_use 则过权限闸门 → 线程池执行（Windows 用 PowerShell）→ tool_result 回填续轮。

继承 mini_harness 的关键设计：
- **错误不打断循环**：工具异常转为 "Error: ..." 字符串作为 tool_result 喂回模型。
- **反应式压缩重试**：API 报 prompt_too_long 时压缩后重试一次。
- **hooks 四事件点**：UserPromptSubmit / PreToolUse / PostToolUse / Stop。
- **工具双表**：`TOOLS`（给模型的 schema 列表）+ `TOOL_HANDLERS`（名字→函数）。基础 6 工具：bash、read_file、write_file、edit_file（old_text 须恰好出现一次）、glob、delete_file；扩展：todo_write、load_skill、subtask（30 轮独立循环、无 subtask 防递归）。
- **safe_path 沙箱**：全部文件工具 resolve 后必须位于 `data/workspace/` 内，bash 在该目录启动。
- **权限闸门**（`agent/permissions.py`，mini_harness 同款硬编码）：① deny-list 硬拒（sudo、rm -rf / 等）→ 错误回喂；② 高危操作（shell 删除、chmod、管道执行、越界写）→ 挂起等待用户审批；③ 其余自动执行。
- **Turn 管理**（`sessions/turn_manager.py`）：session_id → 进行中任务注册表；stop 置取消标志，循环在检查点优雅收尾（已生成内容以 stopped 状态落盘）；审批等待用 `asyncio.Event` 挂起并起止记账（供 active_ms）；同会话并发 turn 返回 409。

## 4. 上下文压缩（纯 ZCode compact）

- **auto compact 阈值**：上下文窗口 − 32K 输出预留 − 13K buffer；token 用量优先取 API 返回的真实 usage；连续失败 3 次熔断。
- **compact**：固定摘要 prompt（要求逐字保留用户消息与关键约束），摘要消息落库成为边界，此后 resume 只发边界之后的消息；压缩事实写 `session_entry(type='compaction')`（tokens_before/tokens_after/summary_message_id）→ 前端"已压缩"提示。
- **microcompact**：本地清旧工具结果——旧 tool part 的输出改写为占位标记，保留最近 5 条完整。

## 5. API 与 SSE 协议

| 方法路径 | 说明 |
|---|---|
| POST /api/sessions | 新建会话（draft，不落库） |
| GET /api/sessions | 会话列表（过滤软删，含 running 标志供侧栏呼吸点轮询） |
| GET /api/sessions/{id} | 会话详情 + 全量回放（消息、工作块条目、turn 事实） |
| DELETE /api/sessions/{id} | 软删除（deleted_at 标记） |
| POST /api/sessions/{id}/turn | 发送用户消息，**响应即 SSE 事件流**（前端用 fetch ReadableStream 消费） |
| POST /api/sessions/{id}/stop | 停止当前 turn |
| POST /api/sessions/{id}/approval | 提交审批决定 {request_id, approved} |
| POST /api/sessions/{id}/regenerate | 重新生成（按 sequence 回滚该 turn 的行 + 重跑） |
| GET /api/health | 健康检查 |

SSE 事件（`event: <类型>` + `data: <JSON>`，15s 心跳）：
`turn_started{turn_id, started_at}` / `delta{text}` / `tool_started{tool_call_id, name, input}` / `tool_completed{tool_call_id, status, output_preview}` / `todo_updated{items}` / `subtask_started{subtask_id, goal}` / `subtask_delta{subtask_id, text}` / `subtask_completed{subtask_id}` / `approval_request{request_id, tool, input, reason}` / `token_count{...}` / `compacted{tokens_before, tokens_after}` / `turn_completed{turn_id, ended_at, active_ms, state}` / `turn_aborted` / `error{message}`。

## 6. 配置与观测

- 全部参数入 `.env`，`backend/.env.example` 逐项中文注释。
- **观测双系统、内容不重复**：
  - `logging`（`app/obs.py`：控制台 + 滚动文件 data/logs/）：记**工程事件**——turn 生命周期、工具调用与耗时、审批请求与结果、压缩发生、错误堆栈；只记事件与 id，不记 prompt/回复正文。
  - Langfuse（默认关闭）：记 **LLM 交互细节**——system prompt、messages、completion、token 用量，按 session_id(trace)/turn_id(span) 组织。

## 7. 前端架构（React 18 + TS + Vite + Tailwind v4）

参照 `.github_ZCode/packages/ui/src/v4/` 的工作块形态：

```
frontend/src/
  api/client.ts        # fetch + SSE 解析
  types.ts             # 与后端 SSE/回放契约对应的类型
  main.tsx             # BrowserRouter 入口
  hooks/useSessionStream.ts
  components/
    Sidebar.tsx        # 会话列表 + 呼吸点 + 新建/删除
    ChatArea.tsx       # 消息流容器
    TurnGroup.tsx      # 工作块：块头状态机 + Collapsible + 归属切分
    WorkItem.tsx       # 工具卡（二级折叠 + 探索/执行聚合）
    SubtaskCard.tsx    # subtask 卡（点击打开右侧面板）
    SubtaskViewer.tsx  # 右侧 subtask 输出面板
    TodoCard.tsx  ApprovalModal.tsx  TokenBadge.tsx  ErrorCard.tsx
    Composer.tsx       # 输入框（Enter/Shift+Enter、停止按钮）
    MessageItem.tsx    # Markdown + 代码高亮 + 复制
```

- **路由**（react-router-dom）：`/` 欢迎页；`/session/:sessionId` 会话视图（SessionView）。选中列表项/新建草稿即导航到对应地址；刷新按地址恢复会话回放——详情 404（草稿无落盘内容、未知或已删除 id）统一回首页。SessionView 以 `key=sessionId` 重挂载，天然隔离会话切换时的局部状态（详情、实时轮次、subtask 面板）。

- **TurnGroup 状态机**：running 块头"工作中 {duration}"每秒 tick（仅 running 态允许用当前时钟）；completed"已工作 {duration}"取落盘 active_ms 固定值；stopped"已停止"强制展开；完成瞬间自动收起；历史回放同形态。
- 视觉：浅色单主题——zinc-50 底 + 白面板 + zinc-200 发丝线 + zinc-900 正文；主按钮近黑 + 单一强调色；代码/数字/耗时用等宽字体；无渐变、无发光、无 emoji；Phosphor 图标；CSS transition 轻动效并尊重 prefers-reduced-motion；空/加载/错误态齐全。
- Vite dev 代理 `/api` → 8100。

## 8. 并发与失败语义

- 同会话单 turn：第二个 turn 请求返回 409；不同会话并行（工具执行在线程池，不阻塞事件循环）。
- 服务重启：进行中 turn 随进程消失，存储中无收口事实的轮次回放为 stopped。
- 刷新回看：打开会话即从四表回放渲染，不依赖 SSE 重放。
