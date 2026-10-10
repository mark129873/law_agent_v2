# ARCHITECTURE — 后端 Harness 与数据流

> 本文说明当前 Web 实现的模块、状态与执行边界。启动与九机制概览见 [README](../README.md)，用户行为见 [PRODUCT](PRODUCT.md)，测试及运行纪律见 [RELIABILITY](RELIABILITY.md)。

## 1. 系统边界

- 单用户本地 Web 助手：React 界面、FastAPI 后端、SQLite、Anthropic 兼容模型 API。
- 后端按单进程设计：进行中 turn、停止信号、审批等待槽位都在进程内；不能直接扩为多 worker 共享运行态。
- 同会话一次仅运行一个 turn；不同会话可并行。单 turn 内工具逐个执行，subtask 也是串行嵌套调用。
- `scripts_mini_harness/` 是独立 CLI 参考；`.github_ZCode/` 是工程参考，均不参与 Web 运行时导入。
- 无登录、多用户、持久任务调度、SSE 断点续传；PowerShell 的工作目录约束不等于操作系统沙箱。

```text
前端 fetch / SSE
       │
api/sessions.py ── turn_manager（运行登记、停止信号）
       │ 装配 TurnDeps、后台 pump、共享 Queue
       ▼
agent/loop.py ── llm / model_call ── 外部模型 API
       │       ├─ compact（历史预算、摘要）
       │       └─ modelio（调用快照）
       ├─ authorize_tool ── permission_service / approvals
       ├─ tools / todo / subtask
       └─ TurnRecorder ── store ── SQLite 四表
                                      │
                               replay ├─ 界面回放
                                      └─ 模型 messages
```

## 2. 模块职责

| 模块 | 职责 | 主要入口 |
| --- | --- | --- |
| 应用与配置 | 初始化目录、日志、数据库、默认 hooks；读取环境配置 | [main.py](../backend/app/main.py)、[config.py](../backend/app/config.py) |
| 会话 API | 校验、装配依赖、发送/重跑、SSE出口、停止/审批接口 | [api/sessions.py](../backend/app/api/sessions.py) |
| 主循环 | 模型步、工具往返、停止检查、结束状态 | [agent/loop.py](../backend/app/agent/loop.py)：run_turn、_execute_one |
| 模型适配 | SDK流转换为text_delta/tool_use/usage；聚合响应并记调用快照 | [llm.py](../backend/app/agent/llm.py)、[model_call.py](../backend/app/agent/model_call.py) |
| 工具与扩展 | schema、同步工具分发、任务板、子助手、技能、hooks | [tools.py](../backend/app/agent/tools.py)、[todo.py](../backend/app/agent/todo.py)、[subtask.py](../backend/app/agent/subtask.py) |
| 权限与审批 | 策略判定；挂起、接收决定、回填拒绝理由 | [permission_service.py](../backend/app/agent/permission_service.py)、[approvals.py](../backend/app/sessions/approvals.py) |
| 记录器 | 将执行里程碑转换成消息/部件/事实，累计用量与有效工时 | [recorder.py](../backend/app/sessions/recorder.py)：TurnRecorder |
| 存储 | ORM写入、稳定顺序、归属校验、回滚、会话信息投影 | [store.py](../backend/app/sessions/store.py) |
| 读取投影 | 相同存储分别组装界面回放与模型上下文 | [replay.py](../backend/app/sessions/replay.py)：load_replay、load_history |

- `TurnDeps` 是单次执行的依赖集合：client、recorder、system_prompt、history、tools、approver、settings、queue、stop_flag及步数限制。
- `loop` 不直接拼 ORM 行；经 recorder 写入。store 不负责模型调用；replay 不执行工具。
- `db.py` 使用同步 SQLAlchemy Session；HTTP请求与后台turn各自持有独立Session。异步循环不代表异步数据库。

## 3. 一次 turn 的执行

### 3.1 入口与装配

1. 新建会话只返回 draft ID；首次发送才创建 session。
2. `POST /api/sessions/{id}/turn` 校验模型配置和同会话运行态；提交的模式保存到共享执行状态。
3. 创建独立数据库Session、TurnRecorder与停止信号；`load_history`重建此前模型历史，追加当前用户输入。
4. `_turn_sse_response`装配TurnDeps，创建后台pump和Queue；审批与主循环共用该Queue。
5. `begin_turn`保存用户message/text part、context记录，发出turn_started。

### 3.2 模型与工具循环

- 进入循环前执行一次压缩检查。
- `while True`配合显式计数：主循环最多40个模型步，包含Stop注入后的续轮；不是40次失败重试。子助手保留独立30步限制。
- 每步：
  1. 检查步数和停止信号；创建assistant message。
  2. 调用模型；文本delta直接推事件，用量交给recorder累计。
  3. 响应结束时保存整段text part，并为每个tool_use保存pending工具part。
  4. 无工具：加入assistant历史；Stop返回注入内容则续轮，否则成功结束。
  5. 有工具：加入assistant的text/tool_use块，逐个执行工具，将tool_result作为user内容追加，再调模型。
- 第40步正常结束仍为success；若仍需第41步，发error并以failed结束。
- 工具返回`Error:`作为失败结果回填，模型可继续处理；模型异常直接failed，不增加自动重试。
- 默认hooks只注册PreToolUse/PostToolUse日志；Stop保留注入扩展点，当前默认无注入；UserPromptSubmit尚未接入主链路。

### 3.3 工具执行分支

| 分支 | 调用方式 | 原因 |
| --- | --- | --- |
| 普通工具 | asyncio.to_thread(execute_tool, name, input) | 同步文件/命令操作移出事件循环线程 |
| todo_write | await handle_todo_write(deps, message_id, input) | 校验整板、经recorder写todo part、返回面板文本和todo_updated事件 |
| subtask | async for run_subtask_events(...) | 独立模型历史与循环；事件进入父队列，汇总文本作为父工具结果 |

- 工具状态按同一part推进：pending→running→completed/failed；权限拒绝直接denied。
- todo为全量快照，最多20项、最多1项in_progress；没有独立任务表或自动调度器。
- 子助手仅7个同步工具，不含todo/subtask；权限沿用父级，实时审批不提供fullAccess。
- 子助手内部对话不写独立message序列，只保存subtask卡片及父工具输出；模型调用另写带subtask_id的model-io。

## 4. 数据与状态归属

### 4.1 会话模型

```text
session
 ├─ message（会话内sequence；data.metadata.turnId标记轮次）
 │    └─ part（消息内sequence；正文、工具、任务板等内容）
 └─ session_entry（轮次结果、审批、压缩、模型配置快照）
```

- session是侧栏会话；turn是一次用户请求及其后续执行，不是独立表。
- 一次turn含一条用户message和零到多条assistant message；每次主模型调用创建一条assistant message。
- message保存role/modelId/parentID/metadata.turnId；用户和助手正文均只存text part。
- part通过message_id关联消息；同时存session_id便于会话查询。tool使用callID、tool、state，todo/error为本项目扩展。
- session_entry按type区分turn/approval/compaction/context，轮次标签也在data.metadata.turnId；支持同ID更新，审批请求/决定仍分别追加。
- sequence首次创建时分配，更新不变；同ID跨会话或跨消息写入拒绝。
- session只存身份、标题和时间；模型从context记录读取，用量从turn事实聚合。
- 结构定义见[models.py](../backend/app/models.py)；不迁移旧schema，数据库处理纪律见RELIABILITY。

### 4.2 状态存放位置

| 位置 | 内容 | 生命周期 |
| --- | --- | --- |
| SQLite app.db | 消息、内容部件、会话事实 | 重启后可回放 |
| TurnDeps.history | 本turn实际发送给模型的历史 | turn开始重建，步间追加，结束释放 |
| turn_manager | session→turn映射、停止Event | 进程内，不跨重启恢复 |
| approvals._pending | request_id→等待Event/决定槽位 | 进程内；数据库审计不能重建正在等待的协程 |
| execution_state.json / permission_rules.json | 模式、权限规则 | 全项目共享，持久化 |
| workspace/、workspace/.rubbish/ | 工具文件、删除的可恢复副本 | 不随会话重跑撤销 |
| 前端实时turn | SSE临时展示状态 | 收口后用数据库回放替换 |

### 4.3 写入时机与用量

- 用户message与text part同事务保存；助手正文按模型步保存，非逐token提交。
- 工具运行/结束更新同一part；todo每次写快照；subtask收口更新卡片。
- turn收口保存状态、起止时间、active_ms和用量；active_ms扣除审批等待。
- tokens_used为输出累计，input_tokens为输入累计；缓存读写独立记录。主/子调用均计入父turn。
- context_tokens取recorder最近一次调用input；主循环压缩判断另用deps.last_input_tokens，二者不是同一变量。
- SQLite开启WAL、外键及5秒busy_timeout；它保存执行事实，但不是不可变事件库：工具更新、重跑及microcompact都会改动记录。

## 5. 历史投影与上下文

| 读取目的 | 入口 | 输出 |
| --- | --- | --- |
| 给用户看 | load_replay | turn分组、user_message、work_items、末条正文final_text、未决审批 |
| 给模型看 | load_history | Anthropic messages；text/tool_use与合成tool_result往返 |

- 两条读取路径共用消息/部件查询，但不会把所有界面卡片直接发给模型。
- tool_result由工具part的output重建，紧跟对应assistant tool_use；不是另存一条工具角色message。
- 每次用户发送或重跑先从库重建历史；同一turn内每次模型调用使用内存history并追加新结果。
- 系统提示词在每turn装配时读取；与messages分开传给模型。

### 压缩与恢复

1. 仅turn开始检查预算：窗口−输出预留−安全buffer，默认预留32K+13K。
2. microcompact将较旧的完成/失败工具输出改成占位符，默认保留最近5条；随后重载历史。
3. 仍超预算则摘要此前历史，保留当前用户请求原文；compaction记录summary_text和before_sequence。
4. 本轮内存变为“摘要+当前请求”；下次load_history读取摘要及边界后的消息。

- 摘要不会删除旧消息；microcompact会覆盖旧工具输出，没有额外归档副本。
- 摘要超时120秒；连续失败3次后跳过摘要，失败计数只在内存中。
- 入口未继承上一turn真实usage，通常按字符/4估算；尚无prompt_too_long专项重试，摘要调用未计入model-io/用量。

## 6. 事件出口与界面边界

- run_turn通过异步生成器yield事件，后台pump写Queue；审批和部分子助手事件直接写同一Queue。
- SSE只消费该Queue并转成帧，每15秒心跳；不是广播总线或持久事件日志。
- [api/client.ts](../frontend/src/api/client.ts)解析POST响应流；[useSessionStream.ts](../frontend/src/hooks/useSessionStream.ts)归约为实时TurnData。
- 事件按生命周期、文本、工具、todo/subtask、审批、用量/压缩分组；契约见[types.ts](../frontend/src/types.ts)。
- 流中工具输出只给400字符预览；流结束后重新GET详情，以数据库回放替换临时状态。
- 刷新/断线不取消后台turn，但新页面只回放已保存内容，不重新接入原SSE；其他标签同样通过查询回看。
- 当前tool_started在执行结束后才发；subtask_delta按子模型步聚合发送，不能写成逐token实时推送。

## 7. 权限、停止与恢复

### 权限与审批

- evaluate读取共享模式/规则，返回allow/deny/ask。
- 判定顺序：硬拒→yolo→deny规则→ask规则→allow规则→edit/build默认策略；yolo跳过普通规则，但不跳过硬拒。
- ask时保存审批请求，发approval_request，等待进程内Event；POST approval唤醒后保存决定并发approval_resolved。
- 批准后执行工具；拒绝理由作为Error工具结果回填；审批等待暂停工时累计。
- 普通文件工具经safe_path限制于workspace；glob未使用同等检查，PowerShell仅固定cwd，没有系统级隔离。

### 结束与恢复

| 场景 | 当前行为 |
| --- | --- |
| 正常完成 | 无工具且无Stop注入→success |
| 请求停止 | 检查点发现停止→stopped；主循环流中已收到的文本随后保存 |
| 工具失败/拒绝 | 结果回填模型，允许继续 |
| 模型异常/步数用尽 | error事件，failed收口 |
| 页面断线 | 后台继续，已提交记录可回看 |
| 后端重启 | 不恢复执行；无收口事实且无运行登记的轮次回放为stopped |
| regenerate | 保留末条用户message/text part、改挂新turn；删除旧轮助手及事实，再装配执行 |

- 停止是协作式信号，不强杀已启动命令；审批等待尚未接停止信号，子助手流中也不逐delta检查停止。
- regenerate不撤销文件/命令副作用；删除旧turn事实后，用量聚合自然排除旧轮。
- 重启后未决审批可能仍能回放，但原等待槽已不存在；子助手fullAccess选项限制未完整持久化。

## 8. 观测与扩展

### 模型调用记录

- [modelio.py](../backend/app/modelio.py)：项目根`log/model-io-<session_id>.jsonl`，主/子模型每调用一行，成功和失败均记录。
- 字段：时间、session_id/turn_id/subtask_id/model、system/messages、tool_names、response文本/tool_calls、usage、duration_ms、error；不存工具schema。
- MODELIO_DIR可覆盖路径，MODELIO_MAX_MB默认10；超限追加前轮转。写失败记WARNING，不阻断对话。
- 含正文和工具参数，不提交版本库；不用于界面回放。工程事件日志与测试纪律见RELIABILITY。

### 扩展入口

| 扩展 | 需要同步的边界 |
| --- | --- |
| 同步工具 | TOOLS schema、TOOL_HANDLERS、权限能力定义与测试 |
| 会话相关异步工具 | _execute_one分支、recorder持久化、事件及模型结果回填 |
| 新part/会话事实 | models/store适配、recorder、load_replay/load_history的处理策略 |
| 新事件 | 后端事件生产、types.ts、useSessionStream及必要界面消费 |
| 模型接入 | 保持stream(system,messages,tools)事件协议；核对工具往返和usage语义 |

- Goal Loop仅在CLI参考中；技能目录枚举未注入Web系统提示词；todo无三轮未更新提醒。
- ask规则桶未从权限文件完整加载；长会话无分页；其余待核对事项见[session-handoff](session-handoff.md)。
- 本文描述实现，不代表全部场景已运行验收；验证证据见[feature_list.json](feature_list.json)与[progress](progress.md)。
