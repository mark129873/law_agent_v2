# RELIABILITY — 运行与验证纪律

> 实现：[ARCHITECTURE.md](ARCHITECTURE.md)；行为：[PRODUCT.md](PRODUCT.md)。

## 1. 日志

| 通道 | 内容 | 轮转 |
|---|---|---|
| 控制台 + backend/data/logs/app.log | 工程事件/id：轮次、工具、审批、压缩、异常；不记对话正文 | 5MB，保留5份备份 |

- 级别：ERROR异常堆栈；WARNING可恢复异常；INFO生命周期；DEBUG默认关闭。
- httpx/httpcore/urllib3/anthropic 降至WARNING；Langfuse已移除。
- 模型调用快照的格式、存储与轮转统一见[ARCHITECTURE.md](ARCHITECTURE.md) §2.2。

## 2. 干净环境

| 场景 | 强制纪律 |
|---|---|
| pytest | 导入app前注入DATA_DIR到backend/tests/.tmp-data/；MODELIO_DIR到其下modelio/ |
| 每测 | 空目录/空库；结束释放SQLite/日志句柄，再删.tmp-data/ |
| 手测/E2E | 使用隔离空库或清理已批准的数据库；仅构造测试数据 |
| 纯UI验收 | 可用独立内存API/假SSE；不连接真实后端、数据库或模型；截图明确为模拟数据 |
| 收尾 | .tmp-data/无残留；pytest不触碰真实data/workspace/log |

**不迁移数据库**：改 models.py 后停后端，只删除已批准的 app.db 及配套 -wal/-shm，再重启建表；工作区、规则和其他文件保留。

### 高风险测试

1. **模型**：自动化一律假LLM，禁止真实付费API；真实调用仅限人工手动验证。
2. **目录**：pytest只用backend/tests/.tmp-data/；禁止触碰真实数据、workspace、用户文件。
   - 手测/E2E 使用上述隔离空库，不能与 pytest 同时占用同一目录。
3. **命令**：仅白名单安全命令，如echo；禁止真实删除、格式化、危险网络命令。
   - 删除逻辑用mock或专用测试沙箱。
4. **破坏性逻辑**：压缩/软删必须验证数据可回放/可恢复，仅用测试目录构造数据。
   - 核对摘要/当前请求、软删标记、回放过滤已删会话；不得削弱断言。

## 3. 验证与收尾

| 改动 | 检查 |
|---|---|
| 后端 | backend/：uv run pytest -q |
| 前端 | frontend/：npm run build；主包<500kB告警阈值 |
| UI/交互 | 浏览器实测 + 截图视觉验收 |
| 纯文档 | 核对源码/链接/差异；明确未跑运行验证 |

- 标准启动见[init.md](init.md)：单后端进程、仅127.0.0.1:8100；PowerShell无系统沙箱。
- 证据见[progress.md](progress.md)、[feature_list.json](feature_list.json)；静态缺口见[session-handoff.md](session-handoff.md)，未复现不得宣称通过。
- 收尾同步progress/feature_list/session-handoff；核对clean-state-checklist并提交；密钥、运行数据、构建产物不入库。
- 性能基准待补：压缩/全量回放耗时。
