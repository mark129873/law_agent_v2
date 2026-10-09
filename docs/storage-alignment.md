# 四表存储对齐交付说明

基准：`84da92c53ea41a070ef34c9818d7e4389ba4e186`（main）。参考 ZCode：`29628c9`。

## 改动

- session：只保留身份、标题和时间；模型从 context 事实读取，用量从 turn 事实聚合。
- message：关系、顺序、时间独立列；角色、模型、父消息、轮次放 data JSON；不存正文。
- part：正文统一 `type=text`；工具保存 `type=tool/callID/tool/state`；内容通过 message_id 关联。
- session_entry：增加更新时间；支持同 ID 更新；轮次放 `data.metadata.turnId`。审批审计仍追加，回放取最新决定。
- 用户 message/text part 原子写入；重试同 ID 幂等。跨会话/跨消息复用 ID 拒绝，防止内容归属混淆。
- 重跑保留用户及其正文、替换轮次标签；旧助手内容级联清理。用量由保留事实重算展示，避免双计。
- 模型历史、压缩、任务板、子助手、审批与 API/SSE 对外行为保留。修复刷新运行态错误传入 session ID。

这是表组织和存储职责对齐，不是整个 ZCode 的完整复制。没有加入不需要的项目/工作区/分享字段；todo、error 和现有审批/压缩事实类型继续服务当前产品。前端源代码无需改动，服务端将新存储投影为原有契约。

## 验证

| 检查 | 结果 |
|---|---|
| 后端全量 `uv run pytest -q` | 101 通过，3 项既有平台失败 |
| 受影响模块专项 | 61 通过，含新增存储回归 10 项 |
| 前端 `npm run build` | TypeScript 与 Vite 通过；主包 361.65 kB |
| 前端 `npm run lint` | 0 错误，3 条既有警告 |
| 云浏览器 + 真实前后端 + 隔离空库 + 假模型 | 发送、任务板、刷新回放、重新生成、审批通过/拒绝/刷新恢复、停止、错误重试通过 |
| SQLite 核验 | message 无正文副本；外键检查无错误；integrity_check=ok |
| 空库启动 | 四表均 0 行，完整性通过 |

3 项既有失败：`test_approval_approved_executes`、`test_bash_echo_whitelist`（Linux 无 PowerShell）；`test_safe_path_rejects_escape`（Windows 盘符路径在 Linux 的判断）。不跳过或削弱原断言，不宣称全量通过。未调用真实付费模型，也未在 Windows 复测。测试夹具显式注入假配置/假客户端，移除对开发者 .env 的隐式依赖。

## 使用

1. ZIP 解压到新目录，先不要覆盖未提交的个人改动；依赖与凭据需自行配置。
2. 旧版本数据库与本次结构不兼容，不迁移。停后端后，确认不再需要旧会话，再清理 `backend/data/app.db` 及其 `-wal`、`-shm`；保留 workspace、规则及其他文件。
3. 按 `docs/init.md` 安装与启动；首次启动自动建空表。
4. 附加补丁以上述 main 提交为基准，先执行 `git apply --check`，通过后再应用。

ZIP 不含 .git、.env、数据库、依赖、构建产物、测试运行数据或 .github_ZCode。模板 .env.example 保留。
