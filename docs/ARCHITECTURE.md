# ARCHITECTURE — 后端 Harness

> 九机制参考 [mini_harness.py](../scripts_mini_harness/mini_harness.py)；项目入口见 [README](../README.md)，用户行为见 [PRODUCT.md](PRODUCT.md)，运行纪律见 [RELIABILITY.md](RELIABILITY.md)。

## 1. 九个机制

主链路：用户输入 → 压缩 → LLM → 权限 → 工具 → tool_result → 再调 LLM。

| 机制 | 本项目实现 | 代码文件 |
|---|---|---|
| 1. Agent Loop | run_turn 异步循环，最多40步；无工具且Stop无注入则结束；工具错误回填，模型错误以failed收口 | [agent/loop.py](../backend/app/agent/loop.py)、[agent/llm.py](../backend/app/agent/llm.py)、[agent/model_call.py](../backend/app/agent/model_call.py) |
| 2. Tool Use | TOOLS提供schema，TOOL_HANDLERS分发同步工具；todo/subtask另走异步分支 | [agent/tools.py](../backend/app/agent/tools.py)、[agent/loop.py](../backend/app/agent/loop.py) |
| 3. Permission | evaluate返回allow/deny/ask；build/edit/yolo；硬拒优先，审批决定回填模型 | [agent/permission_service.py](../backend/app/agent/permission_service.py)、[sessions/approvals.py](../backend/app/sessions/approvals.py) |
| 4. Hooks | Pre/Post记录工具日志；Stop可注入续轮；UserPromptSubmit仅预留，权限独立于hook | [agent/hooks.py](../backend/app/agent/hooks.py)、[agent/loop.py](../backend/app/agent/loop.py) |
| 5. Task System | todo_write整板更新，≤20项、最多1项进行中；保存todo part并发事件；未实现“三轮未更新提醒” | [agent/todo.py](../backend/app/agent/todo.py) |
| 6. Subagents | 独立历史、最多30步、串行、防递归；当前7个同步工具，权限同父级，结果回填父轮 | [agent/subtask.py](../backend/app/agent/subtask.py) |
| 7. Context Compact | turn开始执行microcompact → LLM摘要；阈值与恢复边界见§3 | [agent/compact.py](../backend/app/agent/compact.py) |
| 8. Skill | load_skill按目录读SKILL.md全文；有目录枚举函数，但尚未注入系统提示词 | [agent/skills.py](../backend/app/agent/skills.py) |
| 9. Goal Loop | **Web后端未实现**；独立完成度判断器、/goal自动续轮仅在参考脚本中 | [scripts_mini_harness/mini_harness.py](../scripts_mini_harness/mini_harness.py) |

- 工具：bash/read_file/write_file/edit_file/glob/delete_file + todo_write/load_skill/subtask。
  - 子助手仅同步工具：6个基础工具 + load_skill；不含todo/subtask。
  - 文件读/写/改/删经safe_path限制于workspace；PowerShell仅固定cwd，无系统沙箱，glob未过同等路径检查。
- 运行：同会话单turn，跨会话可并行；工具逐个执行；停止在检查点生效，不强杀命令。
- 状态：mode、权限规则全项目共享；事件经同一Queue输出SSE，断线不取消后台turn。
- 复用：发送/重跑共用turn装配；主/子共用模型调用记录与审批等待，循环和工具范围独立。
- Web后端与CLI参考脚本各自使用目录内的依赖配置；根目录仅作导航。上述未接通项为当前源码状态，完整待核对清单见[交接文档](session-handoff.md)。

## 2. 对话数据存储

### 2.1 SQLite：会话事实

位置：backend/data/app.db；SQLAlchemy + SQLite/WAL。实现：sessions/store.py、recorder.py、replay.py。

| 表 | 最小结构与职责 |
|---|---|
| session | id、title、time_created/time_updated/time_archived；归档时间作现有软删标记，不新增归档界面 |
| message | id、session_id、sequence、time_created/time_updated、data；data 仅元信息（role、modelId、parentID、metadata.turnId） |
| part | id、message_id、session_id、sequence、time_created/time_updated、data；data.type 区分正文/工具/子助手/任务板/错误 |
| session_entry | id、session_id、type、time_created/time_updated、data；支持同 ID 更新，轮次标签放 data.metadata.turnId |

- 对齐 ZCode 29628c9 的表组织；省略未用字段，不复制其全部功能。用户和助手正文都只存 text part。
- message 每模型步一行；part.sequence 在消息内递增，更新不改变顺序；跨归属同 ID 写入拒绝。
- 工具 part 使用 type=tool、callID、tool、state（input/status/output）；todo/error 保留为本项目扩展。
- session 模型从 context 记录读取，用量从 turn 记录聚合，不再存独立累计列。
- 用户消息与 text part 同事务保存；regenerate 保留用户 message/part，替换 metadata.turnId，删除旧轮助手与事实。
- 回放/API/SSE 对外格式保持，存储结构由适配层转换；压缩、审批、工具往返和用量语义保留。
- session_entry 可 upsert；审批审计仍追加请求/决定两条记录，回放按 request_id 取最新。配置更新可选择不触碰会话活动时间。
- 不做旧 schema 迁移；停服务后只处理 app.db 及配套 WAL/SHM，保留 workspace、规则和其他文件，重启建空库。

### 2.2 model-io：模型调用快照

实现：[modelio.py](../backend/app/modelio.py)；主/子循环共用model_call记录每次调用，成功/失败均写入。

| 项 | 保存方式 |
|---|---|
| 文件 | 项目根`log/model-io-<session_id>.jsonl`；每会话一个文件，一调用一行JSON追加 |
| 标识 | time、session_id、turn_id、subtask_id、model |
| request | system/messages全文、tool_names（不保存工具schema） |
| response | text、tool_calls（id/name/input） |
| usage | input/output tokens、cache_read/cache_creation tokens |
| 诊断 | duration_ms、error |
| 目录/轮转 | MODELIO_DIR可覆盖；MODELIO_MAX_MB默认10；追加前检查，超限以纳秒后缀归档 |
| 用途/失败 | 调试审计，不参与回放；写失败吞异常、记WARNING，不阻断对话 |

- **含对话正文和工具参数，不入库、不外传。**
- SQLite保存会话事实；model-io保存调用现场，两者不互相替代。
- 摘要调用暂未写model-io，也未计入用量。

### 2.3 其他运行文件

| backend/data/下 | 用途 |
|---|---|
| workspace/、workspace/.rubbish/ | 工具工作区、delete_file删除文件的可恢复副本 |
| execution_state.json、permission_rules.json | 共享模式、allow/deny规则 |
| logs/app.log | 工程事件；纪律见RELIABILITY |

## 3. 上下文压缩

实现：agent/compact.py；恢复：sessions/replay.py。

| 阶段 | 方法 |
|---|---|
| 触发 | 仅turn开始；历史token > 窗口 − 输出预留 − 安全buffer；默认预留32K + 13K |
| microcompact | 直接将库内旧工具输出改为占位符；默认保留最近5条完整输出，重载历史后再判断预算 |
| compact | 仍超预算则LLM摘要此前历史；当前用户请求保留原文，不参与摘要 |
| 摘要内容 | 任务、已完成、关键决定、文件、待办、失败教训、用户约束 |
| 落盘/继续 | compaction事实保存summary_text + before_sequence；内存变为“摘要 + 当前请求”，后续仅加载边界后历史 |
| 失败 | 摘要超时120s；连续失败3次后跳过摘要；计数在内存，重启清零 |

**当前边界：**

- 预算函数支持真实usage优先；路由未继承上一轮计量，入口通常按字符/4估算。
- 仅摘要替代模型历史，旧消息仍在库中；microcompact会覆盖旧工具输出，不另行归档。
- 尚无prompt_too_long专用压缩重试；摘要调用记录/用量缺口见§2.2。

前端仅消费会话回放与SSE：React组件保留三栏职责；视觉由[index.css](../frontend/src/index.css)、[workspace.css](../frontend/src/workspace.css)统一，行为与暖黄视觉规范见[PRODUCT](PRODUCT.md)。
