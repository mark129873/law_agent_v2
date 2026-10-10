# 个人助手 Harness

单用户本地 Web 助手：对话、读写工作区文件、执行命令、管理任务和派出子助手。

# 项目运行

需要 Python 3.12+、uv 和 Node.js。首次运行需配置 `backend/.env`，然后在两个终端分别启动：

```bash
uv run --no-project start.py
# 或者分开启动前后端
cd backend
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8100
cd frontend
npm run dev
```

# 项目实现

## 1. 九个机制

主链路：用户输入 → 压缩 → LLM → 权限 → 工具 → tool\_result → 再调 LLM。

| 机制                 | 本项目实现                                                                                                        | 代码文件                                                                                                                                              |
| ------------------ | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. Agent Loop      | run\_turn 进行While True循环；无工具则结束；工具错误回填，模型错误以failed收口                                                         | [agent/loop.py](./backend/app/agent/loop.py)、[agent/llm.py](./backend/app/agent/llm.py)、[agent/model\_call.py](./backend/app/agent/model_call.py) |
| 2. Tool Use        | TOOLS 定义工具及参数 schema；普通工具由 TOOL\_HANDLERS 分发；todo\_write 和 subtask 由主循环单独处理。                                 | [agent/tools.py](./backend/app/agent/tools.py)、[agent/loop.py](./backend/app/agent/loop.py)                                                       |
| 3. Permission      | 主循环check\_permission根据权限模式build/edit/yolo, 返回allow/deny/ask；命中危险命令清单或越界时deny，批准后把工具结果给模型；拒绝后把拒绝原因给模型，让它决定下一步 | [agent/permission\_service.py](./backend/app/agent/permission_service.py)、[sessions/approvals.py](./backend/app/sessions/approvals.py)            |
| 4. Hooks           | Pre/Post记录工具日志；Stop可注入续轮；UserPromptSubmit仅预留，权限独立于hook                                                       | [agent/hooks.py](./backend/app/agent/hooks.py)、[agent/loop.py](./backend/app/agent/loop.py)                                                       |
| 5. Task System     | todo\_write整板更新，≤20项、最多1项进行中；保存todo part并发事件；未实现“三轮未更新提醒”                                                    | [agent/todo.py](./backend/app/agent/todo.py)                                                                                                      |
| 6. Subagents       | 独立历史、最多30步、串行、防递归；当前7个同步工具，权限同父级，结果回填父轮                                                                      | [agent/subtask.py](./backend/app/agent/subtask.py)                                                                                                |
| 7. Context Compact | turn开始执行microcompact → LLM摘要；阈值与恢复边界见§3                                                                      | [agent/compact.py](./backend/app/agent/compact.py)                                                                                                |
| 8. Skill           | load\_skill按目录读SKILL.md全文；有目录枚举函数，但尚未注入系统提示词                                                                 | [agent/skills.py](./backend/app/agent/skills.py)                                                                                                  |
| 9. Goal Loop       | **Web后端未实现**；独立完成度判断器、/goal自动续轮仅在参考脚本中                                                                       | [scripts\_mini\_harness/mini\_harness.py](./scripts_mini_harness/mini_harness.py)                                                                 |

- 工具：bash/read\_file/write\_file/edit\_file/glob/delete\_file + todo\_write/load\_skill/subtask。
  - 子助手仅同步工具：6个基础工具 + load\_skill；不含todo/subtask。
  - 文件读/写/改/删经safe\_path限制于workspace
- check\_permission权限判断:

| 判断结果  | 含义                      |
| ----- | ----------------------- |
| allow | 允许，直接执行工具               |
| deny  | 拒绝，不执行，将拒绝原因回填模型        |
| ask   | 等待用户审批；批准后执行，拒绝则将原因回填模型 |

| 权限模式        | build 变更前确认 | edit 自动编辑 | yolo 完全访问 |
| ----------- | ----------- | --------- | --------- |
| 读取工作区文件     | allow       | allow     | allow     |
| 写入、编辑工作区文件  | ask         | allow     | allow     |
| 删除文件、执行一般命令 | ask         | ask       | allow     |
| 命中硬拒规则      | deny        | deny      | deny      |

| ask选项                                        | 含义                       | 是否保存规则                    |
| -------------------------------------------- | ------------------------ | ------------------------- |
| Allow once（允许一次）                             | 只批准当前这次工具调用              | 不保存长期规则                   |
| Always allow in this conversation（在此对话中始终允许） | 批准当前调用，后续在当前会话中匹配的操作不再询问 | 只保存在内存，不写入项目规则；重启或新建会话不保留 |
| Deny（拒绝）                                     | 不执行当前调用，拒绝原因回填模型         | 不添加永久拒绝规则                 |

## 2. 对话数据存储

### 2.1 SQLite：会话事实

位置：backend/data/app.db；SQLAlchemy + SQLite/WAL。实现：sessions/store.py、recorder.py、replay.py；采用 ZCode 必要六表子集。

| 表 | 保存内容 |
| --- | --- |
| session | 项目、目录、标题、时间、归档标记 |
| message | 用户/助手元信息；data 内 role、anchor.turnId、modelId、tokens、error；sequence 排序 |
| part | 正文、工具参数/状态/结果、压缩信息；归属 message |
| local_setting | 项目权限模式与规则；scope/scope_id/namespace/key 定位，value 存 JSON |
| todo | 当前任务的内容、状态、顺序、时间 |
| turn_usage | turn 状态、起止时间、总耗时、输入/输出及缓存 token |

1. **写入**
   - 首条请求创建会话并保存用户消息；每模型步创建 assistant，正文写 text part，流式 delta 不逐字落库。
   - 工具在同一 part 更新状态；sequence 从 0 开始，同归属更新保留顺序。
   - todo 全量替换；turn_usage 汇总主/子模型用量，duration_ms 包含审批等待。
   - 审批事件仅存内存：刷新可恢复，重启清空；工具结果仍落库。
2. **读取与重跑**
   - load_replay 按 anchor.turnId 分组，末条正文为最终回复；任务板、子助手卡片从工具记录恢复。
   - load_history 重建模型 messages 和工具往返；压缩摘要存隐藏合成 user 消息，compaction.tail_start_id 标记已摘要边界消息。
   - regenerate 保留最后用户消息，删除该轮旧回复/用量/摘要，恢复任务板后重跑；不撤销文件或命令操作。

**不迁移 schema**：停后端 → 删除已确认的测试 app.db 及配套 -wal/-shm → 重启建表；不清工作区。

### 2.2 model-io：模型调用快照

实现：[modelio.py](./backend/app/modelio.py)；主/子循环共用model\_call记录每次调用，成功/失败均写入。

| 项        | 保存方式                                                     |
| -------- | -------------------------------------------------------- |
| 文件       | 项目根`log/model-io-<session_id>.jsonl`；每会话一个文件，一调用一行JSON追加 |
| 标识       | time、session\_id、turn\_id、subtask\_id、model              |
| request  | system/messages全文、tool\_names（不保存工具schema）               |
| response | text、tool\_calls（id/name/input）                          |
| usage    | input/output tokens、cache\_read/cache\_creation tokens   |
| 诊断       | duration\_ms、error                                       |
| 目录/轮转    | MODELIO\_DIR可覆盖；MODELIO\_MAX\_MB默认10；追加前检查，超限以纳秒后缀归档     |
| 用途/失败    | 调试审计，不参与回放；写失败吞异常、记WARNING，不阻断对话                         |

- **含对话正文和工具参数，不入库、不外传。**
- SQLite保存会话事实；model-io保存调用现场，两者不互相替代。
- 摘要调用暂未写model-io，也未计入用量。

### 2.3 其他运行文件

| backend/data/下                               | 用途                           |
| -------------------------------------------- | ---------------------------- |
| workspace/、workspace/.rubbish/               | 工具工作区、delete\_file删除文件的可恢复副本 |
| execution\_state.json、permission\_rules.json | 共享模式、allow/deny规则            |
| logs/app.log                                 | 工程事件；纪律见RELIABILITY          |

## 3. 上下文压缩

实现：agent/compact.py；恢复：sessions/replay.py。

| 阶段           | 方法                                                                        |
| ------------ | ------------------------------------------------------------------------- |
| 触发           | 仅turn开始；历史token > 窗口 − 输出预留 − 安全buffer；默认预留32K + 13K                      |
| microcompact | 直接将库内旧工具输出改为占位符；默认保留最近5条完整输出，重载历史后再判断预算                                   |
| compact      | 仍超预算则LLM摘要此前历史；当前用户请求保留原文，不参与摘要                                           |
| 摘要内容         | 任务、已完成、关键决定、文件、待办、失败教训、用户约束                                               |
| 落盘/继续        | compaction事实保存summary\_text + before\_sequence；内存变为“摘要 + 当前请求”，后续仅加载边界后历史 |
| 失败           | 摘要超时120s；连续失败3次后跳过摘要；计数在内存，重启清零                                           |

# 初版最小harness实现 `scripts_mini_harness/mini_harness.py`

```text
用户输入 -> 压缩 -> LLM -> 有 tool_use ? -> 权限 hooks -> 工具 -> 回填
                                | 否: /goal 模式过目标闸门,否则结束

Tool:
bash / read_file / write_file / edit_file / glob /
delete_file(移入 rubbish/) / todo_write / load_skill / subtask(子助手)
```

### 九个机制各自的实现(括号 = 代码所在分区)

| 机制                     | 实现                                                                                                     | 补充说明                                                    |
| ---------------------- | ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------- |
| **1. Agent Loop**      | `agent_loop` 的 `while True`：调模型，无 `tool_use` 即停；有则执行工具并把 `tool_result` 回填继续                            | 错误也作为 `tool_result` 喂回，不抛异常打断循环（§10）                    |
| **2. Tool Use**        | `TOOLS` 存给模型看的 schema，`TOOL_HANDLERS` 是名字 → 函数的 dispatch map；`execute_tool` 统一拦截 → 分发 → 兜异常（§2）        | 文件操作全部先过 `safe_path` 沙箱                                 |
| **3. Permission**      | `permission_hook` 三道闸门：禁止清单硬拒 / 越界与 shell 删除硬拒 / 高危命令 `[y/N]` 确认；拒绝原因作为 `tool_result` 喂回模型（§4）         | 词表 = `sudo` 等；删除词；`chmod 777` 等 5 个确认词                  |
| **4. Hooks**           | `UserPromptSubmit / PreToolUse / PostToolUse / Stop` 四个事件点挂回调，`trigger_hooks` 里第一个返回非 `None` 的回调生效（§3） | `Pre` = 权限 + 日志；`Post` = 大输出告警；`Stop` = 计数；`Submit` 无注册 |
| **5. Task System**     | `TodoManager` 内存任务板，`todo_write` 全量替换；3 轮未更新就在工具结果里注入提醒（§5）                                            | 渲染 `[ ] / [>] / [x]` 面板；单 `in_progress`；上限 20 条         |
| **6. Subagents**       | `subtask(prompt)` 用全新 `messages` 跑 30 轮独立循环，最终文本作 `tool_result` 返回父级；同一响应里的多个 `subtask` 严格串行（§7）       | 子助手仅 6 个基础工具，无 `subtask`，防递归                            |
| **7. Context Compact** | `ContextCompactor` 四级压缩：新结果落盘留预览 → 中间历史归档 → 旧结果缩短 → LLM 摘要重写；真实 token 计量，占窗口 80% 触发，压到 60%（§8）         | 归档 `.transcripts/` 与 `.task_outputs/`；重试 1 次            |
| **8. Skill**           | 启动扫描 `skills/*/SKILL.md`，system prompt 只放“名称 + 描述”目录，`load_skill` 按需取全文（§6）                            | frontmatter 只取 `name / description` 两字段                 |
| **9. Goal Loop**       | `/goal` 后模型每次想停，由无工具的独立判断器裁定；JSON `{ok, reason, impossible}`；未达成注入理由自动续轮，连续 8 次未放行收口交还用户（§9）           | `max_tokens=512`；连续 8 次收口；error 不计数                     |

### 硬性约束

1. 所有文件操作必须在项目根目录（启动目录）内，越界一律拒绝；
2. 删除命令统一将目标移动到 `rubbish/`，而不是销毁。

### 快速开始

配置 `scripts_mini_harness/.env`，然后启动：

```bash
uv sync
uv run harness.py
```

