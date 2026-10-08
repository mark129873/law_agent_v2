# ARCHITECTURE — 系统实现

> 用户行为：[PRODUCT.md](PRODUCT.md)；运行纪律：[RELIABILITY.md](RELIABILITY.md)。

## 1. 技术栈

| 层 | 实现 |
|---|---|
| 后端 | Python ≥3.12 · FastAPI · uv · SQLAlchemy 2 · SQLite |
| 前端 | React 19 · TypeScript 6 · Vite 8 · Tailwind CSS 4 · React Router |
| 模型 | Anthropic 兼容流式 API；thinking 关闭 |
| 形态 | 单用户、本机桌面 Web、单后端进程、无鉴权 |
| 参考 | mini_harness：循环；ZCode：存储/权限/UI；Codex：耗时格式 |

## 2. 数据流

```text
React :5173 → /api 代理 → FastAPI 127.0.0.1:8100
  POST turn → 后台任务 → 压缩 → LLM → 权限 → 工具 → 结果回填
                              ├─ recorder → SQLite → GET 回放
                              └─ 同一 Queue → SSE → React
  POST approval / stop → 审批等待 / 停止标志
```

| 模块（backend/app/） | 职责 |
|---|---|
| api/sessions.py | 接口、依赖组装、后台泵、SSE |
| agent/loop.py · llm.py | 助手循环、模型流 |
| agent/tools.py · permission_service.py | 工具分发、路径检查、权限 |
| sessions/store.py · recorder.py · replay.py | 写入、工时/用量、回放/模型历史 |
| sessions/turn_manager.py · approvals.py | 并发、停止、审批 |
| modelio.py · obs.py | 调用快照、工程日志 |

## 3. 存储

### 3.1 四表

| 表 | 内容 | 规则 |
|---|---|---|
| session | 标题、模型、时间、deleted_at、输入/输出累计 | 首条消息建行；标题前 30 字 |
| message | 用户/助手消息、turn_id、sequence、data | sequence 在会话内递增 |
| part | text / tool_call / subtask / todo / error | 挂在 message 下，消息内递增 |
| session_entry | turn / approval / compaction / context | 正常写入只追加；turn_id 供回滚 |

- data 为 JSON 字符串；id 为 uuid4 hex；时间为 epoch 毫秒；更新不改 sequence。
- SQLite：WAL、busy_timeout=5000ms、foreign_keys=ON；软删过滤列表/详情。
- **不迁移 schema**：改 models.py 表结构 → 删除 backend/data/ → 重启建表。

### 3.2 写入

1. 提交：user message + context；模型步开始：空 assistant message。
2. 模型步正常结束/主动停止：整段 text part；delta 不逐字落库。
3. 工具：同一 part 更新 pending → running → completed/failed/denied。
4. 收口：写 turn 事实，累加会话用量；每次写入更新会话时间。

### 3.3 事实

| type | 关键数据 |
|---|---|
| turn | 状态、起止时间、active_ms、输入/输出/上下文/缓存 token |
| approval | request_id、tool、input、reason、status；请求/决定各一条 |
| compaction | before_sequence、summary_text、tokens_before/after |
| context | model、max_tokens、time |

### 3.4 工时

active_ms = 总耗时 − 审批等待；服务端收口写定。前端运行时用秒表，结束后取落盘工时。

### 3.5 回放与重跑

1. **load_replay → 前端**
   - 按 turn_id 分组；末条 text 为 final_text，其余进工作块。
   - 审批按 request_id 取最新状态；未决请求恢复弹窗。
2. **load_history → 模型**
   - 重建 Anthropic messages；tool_use 后补 tool_result。
   - 压缩后仅发送“摘要 + before_sequence 之后的消息”。
3. **regenerate → 最后一轮**
   - 保留用户消息；删除旧助手消息/部件/该轮事实；改挂新 turn。
   - 重算会话用量；文件/命令副作用不撤销。

### 3.6 目录（不入库）

| 路径 | 内容 |
|---|---|
| backend/data/ | app.db（含 WAL/SHM）、workspace/（含 .rubbish/）、logs/app.log |
| 同上 | execution_state.json、permission_rules.json、plans/ |
| `log/model-io-<session_id>.jsonl` | 模型调用快照 |
| backend/tests/.tmp-data/ | 测试数据 |

### 3.7 用量

| 口径 | 来源 |
|---|---|
| turn 输入/输出/缓存累计 | 主循环 + 子助手 usage |
| session.input_tokens / tokens_used | turn 收口累加；回滚后 recalc_session_usage 重算 |
| context_used | 最近已收口轮的最后一次 input_tokens，近似占用 |
| TokenBadge | context_used / context_window；详情刷新后更新 |

调用明细只进 model-io；不分摊到 part，不另建用量库。

## 4. 助手与权限

### 4.1 主循环

- run_turn：开始检查压缩 → 最多 40 个模型步 → 工具逐个执行/回填。
- 无工具且 Stop hook 无注入则结束；工具异常转 Error；LLM 异常/步数耗尽为 failed。
- 停止在流中、模型步开始、工具前检查；不强杀已启动命令。

| 能力 | 实现 |
|---|---|
| 基础工具 | bash、read_file、write_file、edit_file、glob、delete_file |
| 扩展工具 | todo_write、load_skill、subtask、enter/exit_plan_mode |
| 文件 | read/write/edit/delete 经 safe_path；old_text 恰好一次；删除移入 .rubbish/ |
| 命令 | PowerShell，cwd=workspace；默认超时 120s；保留尾部 30,000 字符 |
| 任务/技能 | 整板≤20项、最多1项进行中；`backend/skills/<name>/SKILL.md` 按需读取 |
| hooks | PreToolUse/PostToolUse/Stop 已触发；UserPromptSubmit 仅预留 |

工具双表：TOOLS提供模型schema；TOOL_HANDLERS分发同步工具，subtask/todo另走异步分支。

**路径边界：PowerShell 无系统沙箱；glob 未经过 safe_path 同等检查。**

### 4.2 权限

| 状态 | 默认行为 |
|---|---|
| build | 只读/低风险会话操作免批；写、改、删、一般命令审批 |
| edit | workspace 写/改免批；其余按 build |
| yolo | 非计划态直通；硬拒仍生效 |
| plan_enabled | 独立开关；只读非破坏操作放行，其余拒绝 |

1. 顺序：硬拒 → 计划进出特判 → yolo（非计划态）→ deny → ask → 计划检查 → allow → edit → build。
   - 硬拒：禁止命令片段、文件路径越界；yolo 早于自定义规则。
   - bash 白名单且无管道/重定向/命令链时降为只读。
2. 规则：精确、`*` 通配、`cmd:*` 词边界前缀。
   - 文件仅加载 allow/deny；ask 仅评估器支持，持久化未接通。
   - “总是允许”仅 bash；普通命令存首词前缀，高危根命令存整条精确规则。
3. 审批：allowOnce/fullAccess/allowAlways/deny；拒绝反馈≤4096字符，回填模型。
   - 计划获批：归档 Markdown → 关闭 plan_enabled → 继续实施。

### 4.3 子助手

- 独立历史，最多 30 步；父轮内串行，无递归。
- 当前可用：6 基础工具 + load_skill + 计划进出；无 todo_write/subtask。
- 权限同父级；审批走父会话，实时请求不提供 fullAccess。
- SSE 推输出；数据库保存目标/状态/输出；内部步骤只在调用快照中，usage 计入父轮。

### 4.4 状态范围

- turn/审批注册表在内存；同会话单 turn，跨会话可并行。
- mode、plan_enabled、规则全项目共享；跨会话修改影响后续工具评估。

## 5. 压缩

| 项 | 当前实现 |
|---|---|
| 时机/预算 | 仅 turn 开始；窗口 − 输出预留 − buffer（默认 −32K−13K） |
| 计量 | 支持真实 usage 优先；路由未继承上一轮计量，入口通常按字符/4估算 |
| microcompact | 库内旧工具输出改占位符，默认保留最近5条；原输出不另归档 |
| compact | 摘要历史，保留当前请求；summary_text + before_sequence 落事实 |
| 失败 | 摘要超时120s；连续失败3次熔断，重启清零 |
| 未接通 | prompt_too_long 专用重试；摘要调用的 model-io/用量记录 |

## 6. API 与 SSE

### 6.1 API

会话前缀：/api/sessions。

| 方法 | 路径 | 行为 |
|---|---|---|
| POST / GET | 空路径 | 草稿 id / 倒序列表（最多200条） |
| GET / DELETE | /{id} | 全量详情 / 会话软删 |
| POST | /{id}/turn | text、mode?、plan_enabled?；返回 SSE |
| POST | /{id}/regenerate | 最后一轮重跑，返回 SSE |
| POST | /{id}/stop | 置停止标志 |
| POST | /{id}/approval | request_id、option_id、feedback?；兼容 approved |
| GET | /api/health | 健康检查，不依赖模型配置 |
| GET | /api/permission/state | 共享执行状态 |

### 6.2 SSE

帧为 event + JSON data；15s 注释心跳。**循环、审批、SSE 共用一个 Queue。**

| 事件 | 用途 |
|---|---|
| turn_started / delta / turn_completed | 建轮、正文、收口 |
| tool_started / tool_completed | 工具卡；流中预览，全文取详情 |
| subtask_started / subtask_delta / subtask_completed | 子助手卡/面板 |
| todo_updated / compacted | 任务板/压缩提示 |
| approval_request / approval_resolved | 审批/留痕 |
| token_count / error | 用量/错误 |

## 7. 前端

| 模块 | 职责 |
|---|---|
| App · ShellContext | 三栏 flex 壳、路由、草稿；左栏状态存 localStorage ui.sidebar |
| api/client · types · useSessionStream | fetch/SSE/协议；归约实时 turn，结束重拉详情 |
| ChatArea · TurnGroup · MessageItem | 对话/工作块/卡片；Markdown与高亮异步加载 |
| Composer · ApprovalModal | 输入、模式草稿、停止、审批/计划 |
| Sidebar · SubtaskViewer · TokenBadge | 5s轮询、子助手面板、上下文徽标 |

- 路由：/、/session/:id；以 sessionId 重挂载；草稿仅内存登记，刷新回首页。
- 保持三栏 flex/Provider 与子助手实时对象引用。
- 视觉：浅色 zinc、sky 强调、Phosphor、等宽数字、尊重减少动态效果；圆角 lg/xl/2xl。

## 8. 失败与缺口

| 场景 | 行为 |
|---|---|
| 重复生成/运行中删除；缺配置；未知资源 | 409；400；404 |
| SSE断开 | 后台继续；刷新读已落盘内容，不续流 |
| 服务重启 | 内存任务消失；未收口轮为 stopped |
| LLM异常 | failed；异常步部分正文未保证落盘 |

**2026-10-08 静态发现；未运行复现、未修复：**

| 缺口 | 源码 |
|---|---|
| 审批等待不响应停止；弹窗无停止入口 | approvals.py、turn_manager.py、ApprovalModal |
| 详情传 session id 集合，回放按 turn id 判断，运行态可能误判 | api/sessions.py、replay.py |
| 刷新子助手审批丢失 fullAccess 限制；重启可能恢复失效审批 | replay.py、approvals.py |
| tool_started 在执行结束后才发；自动放行未写审批事实 | loop.py |

压缩接线缺口见 §5；长会话仍全量回放。

## 9. 配置与观测

- backend/.env：模型连接、输出上限、命令超时、压缩预算、数据/日志目录；相对目录基于 backend/。
- 发起 turn 必需 ANTHROPIC_API_KEY、MODEL_ID；系统提示词每轮现读。
- 启动自动建目录/表；标准命令指定 127.0.0.1:8100，前端代理固定8100。
- 三层分工：SQLite会话事实 / model-io调用快照 / app.log工程事件；Langfuse已移除。细则见 RELIABILITY。
