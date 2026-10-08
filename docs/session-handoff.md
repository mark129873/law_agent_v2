# 会话交接

## 本轮（2026-10-08，Session 009）

- 分支：远端原仅main e4bc99e；从origin/main建dot，本地提交fbc907c；push因缺GitHub写认证失败，远端dot尚未建立，main保持。
- 已实现：Plan独立UI、请求/状态字段、工具、提示注入、审批特判与归档已删除；保留todo、普通三模式/审批、子助手和聊天历史。
- 验证：假LLM专项12过；全量91过/3既有失败（同环境基线84过/同3失败）；前端build通过361.83kB，lint原有5警告。
- 启动：标准uvicorn 127.0.0.1:8100实测health=ok、空会话列表、mode=build后正常停止。
- 未验：云浏览器首次权限检查dismiss、重试ERR_BLOCKED_BY_CLIENT，原因未确认。没有完成UI实测或截图；BE-014及本轮复核标blocked。
- 既有测试限制：无powershell使echo/批准后Remove-Item失败；Linux将C:/路径视为相对目录，Windows盘符越界断言失败。未扩修、未弱化测试。
- 数据：开始时没有backend/data或log，无旧历史/计划可清理；没有历史兼容/迁移代码；测试全程无真实付费模型调用。
- 证据：progress Session 009；临时测试日志在tmp/plan-removal/，不提交。

## 历史验证

- 真实服务权限E2E为Session 005历史证据；后端pytest为Session 007，前端构建/模拟交互为Session 008。
- 已有能力：会话/回放、工具、普通审批、子助手/任务板/技能、压缩、重新生成、日志/用量。
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

既有限制：长会话全量回放；5条非阻断前端lint警告。本轮新增验收blocker见上文。

## 下一步

1. 安全配置GitHub写认证后push dot并核验；在可访问的云浏览器补模式/审批验收，在Windows复测3项既有失败。
2. 分项修复、补必要验证；既有passing不覆盖本轮发现的缺口。
3. 保留契约：
   - 四表、稳定sequence、单Queue、审批工时、regenerate用量重算。
   - 三栏flex/Provider、子助手面板引用、model-io写失败不阻断对话。
   - 不迁移数据库；测试空库；UI截图验收；日志/密钥不入库。

## 标准命令

| 目录 | 命令 |
|---|---|
| backend/ | uv run uvicorn app.main:app --host 127.0.0.1 --port 8100 |
| frontend/ | npm run dev（默认localhost:5173） |
| backend/ | uv run pytest -q |
| frontend/ | npm run build |

调试：backend/data/logs/app.log；`log/model-io-<session_id>.jsonl`。启动/测试环境见 init.md、RELIABILITY.md。
