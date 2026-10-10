# 会话交接

## 最新（2026-10-10，Session 016）

- 三条前端 lint 警告已修复；用户授权本次仅更新 README 2.1，其他章节未改。
- lint 零警告/零错误；build、pytest 121 项通过；云浏览器模拟组件回归通过。
- 测试服务停止，临时页移入 tmp；通过 GitHub 插件提交 main。

## 历史（2026-10-10，Session 015）

- 基准 main 413997f；ZCode 29628c9；必要六表子集已实现，详见 storage-subset.md。
- 新结构：session、message、part、local_setting、todo、turn_usage；不迁移旧库。
- 审批内存保存，刷新恢复、重启清空；工具结果持久化。总耗时包含审批等待。
- pytest 121 过；build 通过；lint 3 既有警告。云浏览器真实服务＋假模型交互及重启恢复通过。
- README 未改；测试服务停止，临时数据不入库。通过 GitHub 插件提交 main，SHA 见 git log。
- 后续仅处理下列既有缺口；历史记录中的四表/平台测试结果不代表当前结构。

## 历史（2026-10-10，Session 014）

- 基准 main 76970df；本轮仅新增 local_setting permission 存储，原四张会话表与审批选项不变。
- 模式/规则读写改 SQLite；八字段/联合主键/索引对齐 ZCode。当前项目 ID 为规范工作区路径 SHA-256；无多项目界面。
- 旧 JSON 原样保留但不读取/迁移，首次默认 build/空规则；当前四表结构已有库只会新增配置表，无需删除用户数据。
- 新增10测试通过；全量115过/3既有平台失败；独立进程真实HTTP模式/会话列表通过。测试隔离目录已清理，无模型调用。
- README 未修改；AGENTS 已明确仅用户手动修改 README。按授权经 GitHub 插件提交 main 并核验。
- 后续：审批三选项/仅内存会话工具授权、四表进一步对齐均未实施。

## 本轮（2026-10-10，Session 013）

- 基准main 26501e0；README四表说明更新，ARCHITECTURE重写为后端Harness结构与数据流，前端仅保留事件消费边界。
- 主循环改while True+显式计数；40模型步上限、末步成功、Stop续轮、停止及模型异常语义不变，无新增重试。
- 验证：新增4项边界通过；全量105过/3既有平台失败；原循环复测同3项失败。文档链接/JSON/diff检查通过，隔离测试库已清理；未调用真实模型、未做UI验收。
- 交付：经GitHub插件提交main并核对远端；不使用终端push。未扩修下述既有问题。

## 上轮（2026-10-09，Session 012）

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

| 工具呈现 | tool_started在执行结束后才发；自动放行无审批事实 | loop.py |
| 压缩接线 | 入口未继承上一轮usage；无专用超长重试；摘要调用未记model-io/用量 | api/sessions.py、loop.py、compact.py |
| 路径 | PowerShell无系统沙箱，glob未过safe_path | permission_service.py、tools.py |

既有限制：长会话全量回放；PowerShell 无系统沙箱。

## 下一步

1. 按需补 Windows 实机验收。
2. 分项修复、补必要验证；既有passing不覆盖本轮发现的缺口。
   前端进一步精简可从发送/重跑共用生命周期、审批/子助手状态入手；本轮没有提前实施。
3. 保留契约：
   - 六表、稳定sequence、单Queue、轮次总耗时、regenerate用量重算。
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
