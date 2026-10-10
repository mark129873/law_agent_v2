# ZCode 存储子集

- 基准：law2 `413997f`；ZCode `29628c9`。
- 范围：仅现有功能；README 由用户维护；不迁移测试旧库。

| 表 | 用途 | 取舍 |
| --- | --- | --- |
| session | 会话与项目归属 | 7 字段 |
| message | 消息元信息 | 6 字段；anchor.turnId |
| part | 正文、工具、压缩 | 7 字段；不存自定义 todo/error/subtask 运行态 |
| local_setting | 项目权限 | 8 字段；toolName/ruleContent |
| todo | 当前任务板 | 6 字段；省略未使用 priority |
| turn_usage | 轮次状态和用量 | 11 字段；duration_ms 含审批等待 |

## 实现决定

- 不保留 session_entry、tool_usage、model_usage。
  - 当前没有会话级模型选择；配置仍来自 MODEL_ID。
  - 审批事件仅进程内保存；刷新可恢复，重启不恢复旧审批卡片，工具结果仍持久化。
- 保留现有 API 投影；数据库仅采用 ZCode 字段。
  - 子任务卡片从工具输入/结果恢复，不重复持久化运行态部件。
  - 模型异常写 message.error；上下文占用读最近主模型消息 tokens.input。
  - 当前 todo 事务全量替换；重跑从保留的成功 todo 工具输入恢复。
  - 压缩摘要使用隐藏合成 user message、text part、compaction part；边界是消息 ID。
- 使用目录 slug 的 ZCode 项目标识规则；不提供跨工具直接打开数据库兼容性。
- 所有保留列按源码类型/默认值/约束；sequence 从 0 开始，重复保存不改序号。

## 验证

- pytest 121 通过；前端 build 通过，lint 0 错误/3 既有警告。
- 上游 SQL 对照：列类型、空值、默认值、主键、外键一致。
- 云浏览器＋真实服务＋假模型：任务板、刷新、审批允许/拒绝、子助手、错误重试、停止、重启恢复通过。
- README 未改；无真实模型调用。
