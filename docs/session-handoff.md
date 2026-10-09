# 会话交接

## 本轮（2026-10-09，Session 012）

- 基准：main 84da92c；参考 .github_ZCode 29628c9。
- 已实现：最小四表结构、用户/助手统一 text part、消息角色与轮次 JSON 元信息、tool state、session_entry upsert；保持 API/SSE 契约；同步压缩/回放/重跑/用量。修复运行态回放 ID 错配。
- 验证：新10例过；专项61过；全量101过/3既有平台失败；前端 build/type通过，lint 3既有警告。云浏览器真实前后端、假模型测试通过，详见 storage-alignment.md。
- 数据：此前无实际旧库，已建空四表；测试沙箱清理、服务全部停止；不迁移，不清其他工作区文件。
- 交付：用户要求 ZIP 先发，再经 GitHub 插件将 dot 快进基于最新 main 并提交；不得把计划写成已发布。

## 历史验证

- 真实服务权限E2E为Session 005历史证据；后端pytest为Session 007，前端构建/模拟交互为Session 008。
- 已有能力：会话/回放、工具、普通审批、子助手/任务板/技能、压缩、重新生成、日志/用量。
- 原始证据见 feature_list.json 与 progress.md Session 005。
- Plan移除在Session 009/010完成代码与离线验证，但云浏览器扩展屏蔽127.0.0.1导致ERR_BLOCKED_BY_CLIENT，UI仍未验；当时宿主进程清理未完全确认，详见progress。本轮未重新验证或消除这些限制。

## 未解决项

以下由源码静态发现，**未运行复现、未修复**：

| 优先核对 | 问题 | 位置 |
|---|---|---|
| 审批停止 | 等待不监听stop；弹窗无停止按钮 | approvals.py、turn_manager.py、ApprovalModal.tsx |

| 审批恢复 | 子助手fullAccess限制未持久化；重启可能弹出失效请求 | approvals.py、replay.py |
| 工具呈现 | tool_started在执行结束后才发；自动放行无审批事实 | loop.py |
| 压缩接线 | 入口未继承上一轮usage；无专用超长重试；摘要调用未记model-io/用量 | api/sessions.py、loop.py、compact.py |
| 规则/路径 | ask桶未加载；PowerShell无系统沙箱，glob未过safe_path | execution_state.py、permission_service.py、tools.py |

既有限制：长会话全量回放；3条非阻断前端lint警告；Linux缺PowerShell导致2项测试失败，Windows盘符路径断言另有1项失败。既有UI验收blocker见历史验证。

## 下一步

1. 在可访问的云浏览器补模式/审批验收，在Windows复测3项既有失败。
2. 分项修复、补必要验证；既有passing不覆盖本轮发现的缺口。
   前端进一步精简可从发送/重跑共用生命周期、审批/子助手状态入手；本轮没有提前实施。
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

本轮修复：运行中刷新回放使用 running_turn_ids；其余历史问题未在本轮扩修。
