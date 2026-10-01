# docs/ -- 文档导航

| 文档 | 内容 | 何时读 |
|---|---|---|
| [PRODUCT.md](PRODUCT.md) | 用户可见行为：界面、交互、展示规则、边界、非目标 | 做任何功能前 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系统实现：数据流、四表存储、agent 循环、SSE 协议、前端结构 | 改动实现前 |
| [RELIABILITY.md](RELIABILITY.md) | 日志与 Langfuse 约定、测试干净环境纪律（强制约束） | 写测试 / 手测前 |
| [init.md](init.md) | 构建与启动验证步骤 | 每次会话开工（AGENTS.md 开工流程） |
| [feature_list.json](feature_list.json) | 功能清单与状态（passing 必附证据，≤200 字） | 报告进度 / 收尾 |
| [progress.md](progress.md) | 会话进度日志（冷热分层：Session 数 ≤ 15，超出沉降 archive/） | 了解最新进展 / 收尾 |
| [session-handoff.md](session-handoff.md) | 会话交接：已验证 / 本轮改动 / 风险与 blocker / 下一步 | 接手会话 / 收尾 |
| [clean-state-checklist.md](clean-state-checklist.md) | 收尾核对清单（启动测试 / 文档同步 / 仓库状态） | 每次收尾 |
| archive/ | 冷存储（旧 Session 条目沉降区） | 沉降时 |

**文档间分工**：PRODUCT 讲"用户看到什么"，ARCHITECTURE 讲"系统怎么实现"，RELIABILITY 讲"怎么保证可信"，progress/handoff 讲"现在到哪了"。
