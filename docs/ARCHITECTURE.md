# Architecture -- 架构

> 分工：本文讲**系统怎么实现**（组成/数据流/设计决策）；用户可见行为见 `PRODUCT.md`；可靠性纪律见 `RELIABILITY.md`；文档导航见 `README.md`。

## 1. 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 · FastAPI · uv · SQLite(WAL) · SQLAlchemy 2.0 |
| 前端 | React 18 · TypeScript · Vite · Tailwind CSS v4 · react-router-dom |
| 图标 | Phosphor Icons（单一家族，不混用） |
| 模型 | Anthropic 兼容 API，流式，thinking 全局关闭 |

参考仓库（`.github_*`，均不入库）：`ZCode`（会话存储 + 工作块 UI + 工作台壳层）、`codex`（耗时格式与事件设计）、`scripts_mini_harness/`（agent 循环思想母本）。

## 2. 总体数据流

```
浏览器 (React :5173, Vite 代理 /api)
   │  POST /turn(响应即 SSE 流)  POST /approval  POST /stop  GET 回放
   ▼
FastAPI (uvicorn 127.0.0.1:8100, 无鉴权仅本机)
   ├─ api/sessions.py         # 路由；turn = 后台泵任务 + asyncio.Queue + SSE 帧
   ├─ sessions/store.py       # SQLite 四表 = 唯一事实源（实时 upsert）
   ├─ sessions/replay.py      # 四表 → 全量回放 / 模型可见历史
   ├─ sessions/turn_manager   # 会话→进行中 turn 注册表、stop、并发 409
   ├─ agent/loop.py           # 主循环：压缩检查→流式调用→权限→工具→回填
   │    ├─ permissions.py     # deny 硬拒 / 高危审批 / 自动放行
   │    ├─ subtask.py · todo.py · skills.py · hooks.py · compact.py
   │    └─ tools.py           # 6 基础工具 · safe_path 沙箱 · PowerShell
   └─ obs.py                  # logging(工程事件) + Langfuse(LLM 细节)
```

## 3. 会话存储（单库四表 = 唯一事实源）

### 3.1 数据库约定

| 项 | 约定 |
|---|---|
| 库文件 | `backend/data/app.db`；WAL + busy_timeout + foreign_keys；全会话共库 |
| schema 演进 | **不做迁移**（产品决策 2026-09-28）：改 `models.py` → 删 `data/` 重启即全新建表 |
| id / 时间戳 | 32 位 uuid4 hex / epoch 毫秒浮点（`time.time()*1000`） |
| 删除 | DB 软删（`deleted_at` 非空，列表/回放一律过滤）；用户视角=永久删除 |

### 3.2 四表结构（ZCode session-store 同款，data 列存 JSON 字符串）

| 表 | 列 | 关键约定 |
|---|---|---|
| `session` | id, title, model, created_at, updated_at, deleted_at?, tokens_used | title=首条消息截断 30 字不可改名；tokens_used=turn 收口累加（regenerate 时重算修正） |
| `message` | id, session_id, sequence, **turn_id**, role(user/assistant), data, time_created, time_updated | data={"text"}；turn 是行上的标签不是容器，回放按它分组 |
| `part` | id, message_id, session_id, sequence, **turn_id**, kind, data, time_created, time_updated | kind：text / tool_call / subtask / todo；工具按生命周期逐态 upsert 同一行 |
| `session_entry` | id, session_id, type, **turn_id**, data, time_created | 会话级事实，**只插入不更新**；turn_id 支撑按轮回滚 |

### 3.3 写入机制（里程碑式实时 upsert）

- 全部 `insert … on conflict(id) do update`；`sequence` 首次取 max+1、冲突时原样保留（防时间线漂移——ZCode 关键规则）；每次写后 touch `session.updated_at`。

| 写入时机 | 落盘内容 |
|---|---|
| 用户提交 | user message 行 |
| 每个模型步开始 | assistant message 行（里程碑语义，正文随后填充） |
| 该步响应结束 | text part **整段写入**（流式 delta 不落库） |
| 工具生命周期 | tool part 逐态 upsert：pending→running→completed/failed/denied（data 带输出与耗时） |

### 3.4 会话级事实（session_entry 的 type 与 data）

| type | data | 消费方 |
|---|---|---|
| turn | {turn_id, started_at, ended_at, active_ms, state} | 工作块头。**active_ms=服务端权威工时，排除审批等待**，收口一次写定；前端历史耗时只取落盘值，禁止用当前时钟推算 |
| approval | {request_id, tool, approved, time} | 审批留痕卡 + 刷新恢复弹窗 |
| compaction | {summary_message_id, tokens_before, tokens_after} | "已压缩"提示 + resume 边界 |
| context | {model, max_tokens, system_prompt_mtime} | 每 turn 上下文快照；系统提示词只影响其后轮次 |

### 3.5 回放与 resume

| 出口 | 逻辑 |
|---|---|
| `load_replay`（给前端） | 按 sequence 读取 → 按 turn 标签分组 → 用户消息 + 工作块条目 + 末条 text 提升为 final_text；无收口事实的轮：在 running 集合=running，否则=stopped（孤儿轮） |
| `load_history`（给模型） | Anthropic messages 形态；compact 边界（摘要消息）之后才下发；assistant 紧跟处补合成 tool_result；tokens_used 从回放重算 |

### 3.6 目录约定（全部 gitignore）

```
backend/data/
  app.db(-wal/-shm)   # SQLite
  workspace/          # 工具沙箱根（safe_path 限制于此）
  logs/               # app.log 滚动日志（5MB × 5 份）
```

## 4. Agent 核心（mini_harness 移植 + 流式 + 可中断）

一个 turn = 一次 `run_turn()`（上限 40 步安全阀）：

```
用户输入 → 压缩检查 → LLM 流式调用（text delta 实时推 SSE；usage → add_tokens/token_count/model-io JSONL）
   ├─ 无 tool_use → Stop hook 无注入则收口（有注入则续轮）
   └─ 有 tool_use → 权限闸门 →（高危）审批挂起·active_ms 记账暂停 → 线程池执行(PowerShell) → tool_result 回填续轮
```

### 4.1 继承 mini_harness 的关键机制

| 机制 | 实现 |
|---|---|
| 错误不打断循环 | 工具异常转 `Error: …` 字符串作 tool_result 喂回；LLM 异常转 error 事件并以 failed 收口 |
| 工具双表 | `TOOLS`(给模型 schema) + `TOOL_HANDLERS`(名字→函数)；基础 6 工具 bash / read_file / write_file / edit_file(old_text 恰好一次) / glob / delete_file；扩展 todo_write / load_skill / subtask |
| safe_path 沙箱 | 文件工具 resolve 后必须位于 `data/workspace/` 内；bash 在该目录启动 |
| hooks | UserPromptSubmit / PreToolUse / PostToolUse / Stop 四事件点 |
| 停止 | stop_flag 检查点：流中 / 每工具前 / 每轮开始；命中即 stopped 收口，已生成部分已落盘 |
| 反应式压缩重试 | API 报 prompt_too_long → 压缩后重试一次 |

### 4.2 权限闸门三档（`permissions.py`，硬编码词表）

| 档 | 触发 | 行为 |
|---|---|---|
| deny | sudo、`rm -rf /` 等禁止清单 | 硬拒，Error 回喂 |
| approve | shell 删除、chmod、管道执行、越界写 | 挂起等用户审批（SSE 推 approval_request）；拒绝理由回喂，模型自行改道 |
| allow | 其余 | 自动执行 |

### 4.3 subtask 子助手

- 全新历史跑 **30 轮独立循环**，仅基础工具（无 subtask 防递归）；最终文本作 tool_result 返回父级。
- subtask_started/delta/completed 事件直推队列，右侧面板实时累积；落盘只留"目标+状态+最终输出"一张卡（子循环内部步骤不落库）。
- **token 口径与主循环一致**：usage → add_tokens 计入会话、发 token_count、写入 model-io JSONL（带 subtask_id）。
- deny-list 仍生效；高危自动放行（子助手是父任务委派的执行细节，无交互审批）。

### 4.4 Turn 管理（`turn_manager.py`）

session→进行中 turn 注册表；同会话并发 turn 返回 409；stop 置取消标志、循环在检查点优雅收尾；审批等待用 `asyncio.Event` 挂起并暂停 active_ms 记账。

## 5. 上下文压缩

| 机制 | 规则 |
|---|---|
| auto compact | 阈值 = 上下文窗口 − 32K 输出预留 − 13K buffer；token 优先取 API 真实 usage；连续失败 3 次熔断 |
| compact | 固定摘要 prompt（逐字保留用户消息与关键约束）；摘要消息落库成为边界，此后 resume 只发边界之后；compaction 事实落库 |
| microcompact | 本地清旧工具输出为占位标记，保留最近 5 条完整 |

## 6. API 与 SSE 协议

### 6.1 路由

| 方法路径 | 说明 |
|---|---|
| POST /api/sessions | 新建（draft：只发 id 不落库） |
| GET /api/sessions | 列表（过滤软删、倒序、含 running 呼吸点标志） |
| GET /api/sessions/{id} | 详情+全量回放（含未决审批恢复）；404=不存在/已删 |
| DELETE /api/sessions/{id} | 软删；进行中返回 409 |
| POST /api/sessions/{id}/turn | 发消息，**响应即 SSE 流**（fetch ReadableStream 消费）；缺 LLM 配置 400 |
| POST /api/sessions/{id}/stop | 停止当前 turn（检查点优雅收口） |
| POST /api/sessions/{id}/approval | {request_id, approved} 唤醒等待中的 turn（与连接无关） |
| POST /api/sessions/{id}/regenerate | 按 turn_id 回滚该轮行+事实，保留用户消息重跑（错误重试同机制） |
| GET /api/health | 健康检查（不依赖 LLM 配置） |

### 6.2 SSE 事件（帧 = `event: <type>` + `data: <JSON>`；15s 心跳注释帧保活）

| 事件 | 载荷要点 | 前端消费 |
|---|---|---|
| turn_started | turn_id, started_at | 建实时轮（用户气泡用本地暂存文本） |
| delta | text | 流式正文累积 |
| tool_started / tool_completed | tool_call_id, name, input / status, output_preview | 工具卡 |
| todo_updated | items | 任务板卡 |
| subtask_started / subtask_delta / subtask_completed | subtask_id, goal / text / status | SubAgent 卡 + 右栏面板（面板持同一引用原地更新） |
| approval_request / approval_resolved | request_id, tool, input, reason / approved | 审批弹窗 + 留痕卡翻转 |
| token_count | input_tokens, output_tokens | 用量数据源（徽标显示以会话累计为准） |
| compacted | tokens_before, tokens_after | "已压缩"提示条 |
| turn_completed | turn_id, ended_at, active_ms, state | 收口 → 重拉详情换落盘权威值 |
| error | message | 错误卡（重试=regenerate） |

**turn 泵模型**：`run_turn` 事件 → asyncio.Queue → SSE 生成器；断开不杀 turn（刷新回看语义），泵跑完才收口。InteractiveApprover 与路由层**共用同一队列**——双队列错接曾丢审批事件，此为保护约定。

## 7. 前端架构

```
frontend/src/
  main.tsx                   # BrowserRouter 入口
  App.tsx                    # 壳层：路由分发 + ShellContext + 会话列表状态
  ShellContext.tsx           # 左栏开合状态（localStorage ui.sidebar）
  api/client.ts              # fetch + SSE 分帧解析（ApiError 带状态码）
  types.ts                   # 与 SSE/回放契约对应的类型
  hooks/useSessionStream.ts  # SSE → 实时 turn 归约器
  components/                # Sidebar · ChatArea · TurnGroup · MessageItem
                             # Markdown(异步分包) · SubtaskViewer · ApprovalModal
                             # Composer · TokenBadge · TodoCard…
```

| 主题 | 要点 |
|---|---|
| 路由 | `/` 欢迎页；`/session/:sessionId` 会话视图（SessionView 以 `key=sessionId` 重挂载隔离切换）。详情 404 分流：本应用创建的草稿（draftIdsRef）→ 留空对话态；未知/已删 → 回首页（draft 集合随刷新清空） |
| 壳层 | 左栏头部按钮开合（localStorage 记忆，收起宽度归零、瞬时切换）；右栏点 subtask 卡打开 / X 收起 / 再点恢复 |
| TurnGroup 状态机 | running=「工作中 {耗时}」每秒 tick（仅 running 允许用当前时钟）；completed=「已工作 {落盘 active_ms}」完成瞬间自动收起；stopped/failed=「已停止」强制展开 |
| 耗时格式 | codex 阶梯：<60s → `45s`；<1h → `3m 05s`；≥1h → `1h 00m 00s` |
| 聚合组 | 连续只读工具 ≥2 →「探索」组；连续命令 ≥2 →「执行」组 |
| 代码分包 | markdown（react-markdown + rehype-highlight，体积大头）独立模块，经 MessageItem 的 lazy+Suspense 按需加载；主包 ~284kB，低于 Vite 500kB 告警阈值 |
| 视觉 | 浅色单主题：zinc-50 底 + 白面板 + zinc-200 发丝线；近黑主按钮 + 单一强调色；代码/数字/耗时等宽字体；无渐变、无发光、无 emoji；Phosphor 图标；CSS 轻动效并尊重 prefers-reduced-motion；空/加载/错误态齐全 |

## 8. 并发与失败语义

| 场景 | 行为 |
|---|---|
| 同会话第二个 turn | 409 |
| 跨会话并行 | 允许（工具在线程池，不阻塞事件循环） |
| SSE 断开 / 刷新 | turn 后台继续跑完落盘；重开从回放看（不做断点续流） |
| 服务重启 / 崩溃 | 无收口事实的轮回放为 stopped，可重新生成 |
| 刷新时审批未决 | 从回放恢复弹窗，仍可提交 |
| 重新生成 | 按 turn_id 回滚该轮行+事实；用户消息保留改挂新轮 |

## 9. 配置与观测

- 参数全入 `.env`（`backend/.env.example` 逐项中文注释）；数据目录经 `DATA_DIR` 注入（测试指向 `tests/.tmp-data/`）。
- **观测双系统、内容不重复**：

| 系统 | 记什么 | 不记什么 | 组织与降级 |
|---|---|---|---|
| logging（obs.py，默认开） | 工程事件：turn 生命周期、工具调用与耗时、审批请求与决定、压缩发生、错误堆栈 | prompt 与回复正文（属 DB 与 model-io JSONL 职责） | 控制台 + `data/logs/app.log` 滚动（5MB×5）；ERROR=需人处理 / WARNING=可自动恢复 / INFO=生命周期 / DEBUG=默认关 |
| model-io JSONL（modelio.py，默认开） | **逐次 LLM 调用**的完整快照：system prompt、messages、tool 名称、response 文本与 tool_calls、input/output tokens、耗时、错误 | 工程事件（与 logging 分工） | 每会话一个文件 `log/model-io-<session_id>.jsonl`，逐行 JSON 追加（ZCode 同思想）；每会话可独立配置 `MODELIO_DIR`（测试指向 .tmp-data）；写入失败吞异常记 WARNING，不影响主流程；子循环调用同样记录并带 subtask_id |
