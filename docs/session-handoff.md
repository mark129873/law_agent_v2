# 会话交接

## 本轮（2026-10-08，Session 006）

| 项 | 结果 |
|---|---|
| 目标 | 按用户进一步要求，将ARCHITECTURE收敛为后端九机制、对话存储、上下文压缩 |
| 范围 | 仅文档；未改代码、配置或运行数据 |
| 核对 | README九机制与后端源码对应；model-io存储说明由RELIABILITY移入ARCHITECTURE |
| 机制差异 | Web未实现GoalLoop、任务板三轮提醒；技能目录函数未注入系统提示词 |
| 验证 | 仅静态一致性/文档检查；按用户要求未启动、构建、pytest或E2E |
| 功能清单 | 保留20项passing、1项deprecated及历史证据；新增文档复核说明 |

## 历史验证（不代表本轮重跑）

- 上轮记录：pytest **83 passed**、前端build、权限专项E2E。
- 已有能力：会话/回放、工具、审批/计划、子助手/任务板/技能、压缩、重新生成、日志/用量。
- 原始证据见 feature_list.json 与 progress.md Session 005。

## 未解决项

以下由源码静态发现，**未运行复现、未修复**：

| 优先核对 | 问题 | 位置 |
|---|---|---|
| 审批停止 | 等待不监听stop；弹窗无停止按钮 | approvals.py、turn_manager.py、ApprovalModal.tsx |
| 运行态回放 | 路由传session id，回放按turn id判断 | api/sessions.py、replay.py |
| 审批恢复 | 子助手fullAccess限制未持久化；重启可能弹出失效请求 | approvals.py、replay.py |
| 工具呈现 | tool_started在执行结束后才发；自动放行无审批事实 | loop.py |
| 压缩接线 | 入口未继承上一轮usage；无专用超长重试；摘要调用未记model-io/用量 | api/sessions.py、loop.py、compact.py |
| 规则/路径 | ask桶未加载；PowerShell无系统沙箱，glob未过safe_path | execution_state.py、permission_service.py、tools.py |

既有限制：长会话全量回放；本机IAB点击自动化不稳定。当前文档工作无blocker。

## 下一步

1. 开发时优先复现审批等待中的停止，再确认运行态/审批回放。
2. 分项修复、补必要验证；既有passing不覆盖本轮发现的缺口。
3. 保留契约：
   - 四表、稳定sequence、单Queue、审批工时、regenerate用量重算。
   - 三栏flex/Provider、子助手面板引用、model-io写失败不阻断对话。
   - 不迁移数据库；测试空库；UI截图验收；日志/密钥不入库。

## 标准命令（本轮未执行）

| 目录 | 命令 |
|---|---|
| backend/ | uv run uvicorn app.main:app --host 127.0.0.1 --port 8100 |
| frontend/ | npm run dev（默认localhost:5173） |
| backend/ | uv run pytest -q |
| frontend/ | npm run build |

调试：backend/data/logs/app.log；`log/model-io-<session_id>.jsonl`。启动/测试环境见 init.md、RELIABILITY.md。
