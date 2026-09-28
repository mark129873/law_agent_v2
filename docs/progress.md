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
- 日期：2026-09-28
- 本轮目标：补验两条未验证路径——浏览器实测交互审批完整往返（批准+拒绝）与 subtask 右侧面板实时输出（上轮验证结论：15 项功能 passing、启动路径健康，仅这两条缺 UI 实测）
- 实测结论（黑盒 GUI 测试，截图证据在 tmp/gui-test-screenshots/）：
  - 审批批准路径：后端正确挂起（pending_approval + requested 留痕），刷新恢复弹窗→批准→工具真实执行（note.txt 被删）→turn success，active_ms=5.6s 证明审批等待被正确排除（工具挂起总耗时 344s）
  - 审批拒绝路径：拒绝→denied 留痕→理由喂回模型，模型明确复述"被用户拒绝"未重试并给替代方案
  - subtask 路径：运行中 SubAgent 卡"执行中"+右侧面板实时累积输出（sleep 30 任务抓到运行态双截图），完成后结果回到父轮
  - 发现 3 个 bug：①P1 审批弹窗实时路径失效（approval_request SSE 到达时弹窗不出现，用户无感知，只能靠刷新恢复救回）；②P1 Composer Enter 不发送（3 次复现，必须点发送按钮）；③P3 面板状态标签完成后仍显示"执行中"（重开才变"已完成"）
- 运行过的验证：黑盒 GUI 测试（IAB 浏览器 + 真实 LLM，截图证据 tmp/gui-test-screenshots/ 共 12 张）；修复后回归 `uv run pytest -q`（70 passed）；`npm run build` 通过
- 修复与回归：
  - Bug#1（P1，已修复+回归）：`api/sessions.py::_turn_sse_response` 另建新队列覆盖 deps.queue，而 InteractiveApprover 持旧队列引用直推——approval_request/approval_resolved 写进无人消费的队列丢失，实时弹窗永不出现。修复为复用同一队列；回归实测弹窗"工作中 1s"即出现、批准后"已批准"卡实时翻转、全程无需刷新
  - Bug#3（P3，已修复）：`useSessionStream.subtask_completed` 用 map 替换对象致右栏面板持有旧引用、状态停留"执行中"；改为原地变更（与 subtask_delta 的 output+= 同风格）。机制与已实测的面板文本实时增长相同；"执行中→已完成"翻转瞬间因模型过快未直接观测到
  - Bug#2（撤销）：Enter 不发送为测试假象——fill()+Enter 同瞬间竞态 React 状态批处理；逐字输入（delay 80ms）+Enter 实测正常发送。非产品 bug，无需修复
- 已记录证据：feature_list.json BE-006/BE-008/FE-004 证据更新并附浏览器实测；截图 12 张在 tmp/gui-test-screenshots/（不入库）
- 提交记录：见 git log（fix 双队列+面板引用、docs Session 002 收尾）
- 更新过的文件或工件：backend/app/api/sessions.py、frontend/src/hooks/useSessionStream.ts、docs/progress.md、docs/feature_list.json、docs/session-handoff.md、.gitignore(+tmp/)
- 已知风险或未解决问题：
  - Bug#3 的状态翻转瞬间未直接观测（成本考虑不再烧 LLM 轮次，机制等价已验证）
  - failed 轮回放块头显示"已停止"（上轮已知，未处理）
  - 前端主 JS 529kB 超 Vite 警告阈值（code-split 留打磨）
  - 应用无路由/不恢复上次会话（刷新回首页），属 UX 决策非缺陷，留真实使用反馈
- 下一步最佳动作：真实使用打磨——工具卡 autoOpen、subtask 子循环 token 计入、长会话分页（按 handoff 优先级）



