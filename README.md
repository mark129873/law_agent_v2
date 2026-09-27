# 法律助手 Harness

初版，开发中
目前已完成基础 Harness 的开发：`scripts_mini_harness/mini_harness.py`
```text
用户输入 -> 压缩 -> LLM -> 有 tool_use ? -> 权限 hooks -> 工具 -> 回填
                                | 否: /goal 模式过目标闸门,否则结束

Tool:
bash / read_file / write_file / edit_file / glob /
delete_file(移入 rubbish/) / todo_write / load_skill / subtask(子助手)
```

## 九个机制各自的实现(括号 = 代码所在分区)

| 机制 | 实现 | 补充说明 |
|---|---|---|
| **1. Agent Loop** | `agent_loop` 的 `while True`：调模型，无 `tool_use` 即停；有则执行工具并把 `tool_result` 回填继续 | 错误也作为 `tool_result` 喂回，不抛异常打断循环（§10） |
| **2. Tool Use** | `TOOLS` 存给模型看的 schema，`TOOL_HANDLERS` 是名字 → 函数的 dispatch map；`execute_tool` 统一拦截 → 分发 → 兜异常（§2） | 文件操作全部先过 `safe_path` 沙箱 |
| **3. Permission** | `permission_hook` 三道闸门：禁止清单硬拒 / 越界与 shell 删除硬拒 / 高危命令 `[y/N]` 确认；拒绝原因作为 `tool_result` 喂回模型（§4） | 词表 = `sudo` 等；删除词；`chmod 777` 等 5 个确认词 |
| **4. Hooks** | `UserPromptSubmit / PreToolUse / PostToolUse / Stop` 四个事件点挂回调，`trigger_hooks` 里第一个返回非 `None` 的回调生效（§3） | `Pre` = 权限 + 日志；`Post` = 大输出告警；`Stop` = 计数；`Submit` 无注册 |
| **5. Task System** | `TodoManager` 内存任务板，`todo_write` 全量替换；3 轮未更新就在工具结果里注入提醒（§5） | 渲染 `[ ] / [>] / [x]` 面板；单 `in_progress`；上限 20 条 |
| **6. Subagents** | `subtask(prompt)` 用全新 `messages` 跑 30 轮独立循环，最终文本作 `tool_result` 返回父级；同一响应里的多个 `subtask` 严格串行（§7） | 子助手仅 6 个基础工具，无 `subtask`，防递归 |
| **7. Context Compact** | `ContextCompactor` 四级压缩：新结果落盘留预览 → 中间历史归档 → 旧结果缩短 → LLM 摘要重写；真实 token 计量，占窗口 80% 触发，压到 60%（§8） | 归档 `.transcripts/` 与 `.task_outputs/`；重试 1 次 |
| **8. Skill** | 启动扫描 `skills/*/SKILL.md`，system prompt 只放“名称 + 描述”目录，`load_skill` 按需取全文（§6） | frontmatter 只取 `name / description` 两字段 |
| **9. Goal Loop** | `/goal` 后模型每次想停，由无工具的独立判断器裁定；JSON `{ok, reason, impossible}`；未达成注入理由自动续轮，连续 8 次未放行收口交还用户（§9） | `max_tokens=512`；连续 8 次收口；error 不计数 |

## 硬性约束

1. 所有文件操作必须在项目根目录（启动目录）内，越界一律拒绝；
2. 删除命令统一将目标移动到 `rubbish/`，而不是销毁。

## 快速开始

1. 复制 `.env.example` 为 `.env`，填写：

   ```env
   ANTHROPIC_API_KEY=
   ANHROPIC_BASE_URL=
   MODEL_ID=
   ```

2. 安装依赖：

   ```bash
   uv sync
   ```

   如无需指定依赖安装目录，此步骤可省，步骤 3 会自动安装。

3. 启动：

   ```bash
   uv run harness.py
   ```

   交互 REPL；输入任务直接执行：

   ```text
   /goal <条件>
   ```

   进入目标模式。