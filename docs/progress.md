# progress.md -- 会话进度日志

## 格式示例:
### Session xxx
- 日期：
- 本轮目标：
- 技术决策：
- 已完成：
- 运行过的验证：
- 已记录证据：
- 提交记录：
- 更新过的文件或工件：
- 已知风险或未解决问题：
- 下一步最佳动作：

## 下面为真实进度:

### Session 001
- 日期：2026-09-27 ~ 2026-09-28
- 本轮目标：个人助手 harness 全量开发——后端 10 项（BE-1~BE-10）+ 前端 5 项（FE-1~FE-5）全部落地并联调
- 技术决策（经 11 轮访谈确认，详见 git 历史与 docs/）：
  - 会话存储：**ZCode 式单库 SQLite 四表**（session/message/part/session_entry，data JSON 列 + sequence 序），里程碑式实时 upsert；turnHeader 工时作为事实落盘（session_entry.type='turn'，active_ms 排除审批等待）
  - 删除：用户视角永久删除（无归档），DB 软删（deleted_at）
  - 压缩：纯 ZCode compact（阈值=窗口−32K−13K，真实 usage 优先，3 次失败熔断）+ microcompact（旧工具输出占位，保留最近 5 条）
  - 工作块 UI：ZCode 桌面端 packages/ui/src/v4 同款状态机（工作中/已工作/已停止），codex 阶梯耗时格式
  - 其余：全量移植 mini_harness、Anthropic 兼容流式 API、PowerShell、固定 workspace 沙箱、交互审批、无鉴权 127.0.0.1:8100
- 已完成：
  - 后端全部 10 功能（70 个 pytest 通过）：骨架/存储层/CRUD/agent 核心/SSE 流式/交互审批/压缩/subtask-skills-todo/重新生成/Langfuse 观测
  - 前端全部 5 功能（npm build 通过）：脚手架/侧栏/TurnGroup 工作块+流式聊天/审批弹窗+错误重试/重新生成+subtask 面板+聚合组
  - E2E 浏览器联调通过：新建→流式对话→工具写文件→刷新回看→错误重试→resume 续聊→删除
- 运行过的验证：`uv run pytest -q`（70 passed）；`npm run build`；真实 uvicorn 启动 + 浏览器全流程操作
- 已记录证据：feature_list.json 15 项全部 passing（含逐项证据）
- 提交记录：2ffa382(docs) → 6223c1e(BE-1) → 3ba9f13(BE-2) → 52b219f(BE-3) → be0aa8b(BE-4) → 7c0d93b(BE-5) → 8d2ae44(BE-6) → 56d0c74(BE-7) → a759b40(BE-8) → f066c01(BE-9) → cf7f777(BE-10) → 2ffa382..4fd0e1b(FE-1~5) → fix(replay tool_result)
- 已知风险或未解决问题：
  - E2E 联调发现并修复：load_history 在 assistant 紧跟 assistant 时漏插合成 tool_result（已修复+回归）
  - 旧库 schema 演进无版本化迁移（本次靠清 data/ 解决）；后续加列需引入迁移
  - 端口 8000 被本机 Godot MCP 占用，默认改 8100（文档已同步）
- 下一步最佳动作：真实使用中打磨（工具卡 autoOpen、subtask 子循环 token 计入、长会话分页）

### Session 002



