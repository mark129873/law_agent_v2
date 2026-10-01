# RELIABILITY.md -- 可观测性与测试纪律

> 分工：日志/Langfuse/测试干净环境的**约定与强制约束**；系统实现见 `ARCHITECTURE.md` §9。

## 1. 日志（logging 双通道）

| 通道 | 说明 |
|---|---|
| 控制台 | 人读格式 |
| 滚动文件 | `backend/data/logs/app.log`（单文件 5MB，保留 5 份） |

| 级别 | 语义 | 示例 |
|---|---|---|
| ERROR | 需要人处理的异常 | 错误堆栈 |
| WARNING | 可自动恢复的异常 | 反应式压缩重试、Langfuse 上报失败 |
| INFO | 生命周期事件 | turn 开始/收口（turn_id+工时）、工具调用与耗时、审批请求与结果、压缩发生 |
| DEBUG | 开发期细节 | **默认关闭** |

- 记录内容：工程事件 + id；**不记录**用户输入与模型回复正文（正文属于数据库与 model-io JSONL 的职责，双系统不重复）。
- httpx / anthropic 等三方库降噪至 WARNING。

## 2. model-io JSONL（逐调用 LLM 快照）

| 项 | 约定 |
|---|---|
| 位置 | 项目根 `log/model-io-<session_id>.jsonl`（不入库；`MODELIO_DIR` 可覆盖，测试指向 `.tmp-data/`） |
| 粒度 | **一次 LLM 调用一行 JSON**（追加写）：完整 system prompt、messages 快照、tool 名称、response 文本与 tool_calls、input/output tokens、耗时、错误 |
| 范围 | 主循环与 subtask 子循环都记（子循环行带 subtask_id） |
| 失败语义 | 写入失败吞异常记 WARNING，**永不影响对话主流程** |
| 纪律 | 该目录属运行时产物，绝不提交；含对话正文，勿外传 |

## 3. 测试干净环境管理

> 目标：测试从已知空白状态启动，杜绝历史遗留数据干扰。

| 场景 | 纪律 |
|---|---|
| pytest | 数据目录经 `DATA_DIR` 环境变量注入（conftest 在导入 app 前设置），指向 `backend/tests/.tmp-data/`；model-io 目录同理经 `MODELIO_DIR` 注入；**每个测试前清空重建，测试后必须删除**（teardown） |
| 浏览器手测 / E2E | 启动真实后端前先清空 `backend/data/`（生产语义即"改表/发版 = 清库重建"，产品决策 2026-09-28：不做 schema 迁移） |
| 测试后核对 | `.tmp-data/` 已删除（Windows 文件锁时先释放 SQLite/日志句柄）；真实 `backend/data/` 未被测试触碰 |

### 高风险测试强制约束

1. **假 LLM**：单元/集成测试一律使用假 LLM（mock 客户端 + 预录假流式序列），禁止真实调用付费 API；真实调用仅允许人工手动验证。
2. **测试目录**：只用 `backend/tests/.tmp-data/`；禁止测试触碰真实数据目录、workspace、用户文件。
3. **执行类测试**：仅跑白名单安全命令（如 `echo`）；禁止测试真实的删除、格式化、网络危险命令；删除逻辑用 mock 或专用测试沙箱验证。
4. **破坏性操作**：压缩、软删等测试必须验证"数据可回放/可恢复"（软删标记正确、回放不出现已删会话），且使用测试目录内构造的数据。

## 4. 基准测试

```bash
cd backend && uv run pytest -q    # 全量基线（当前通过数见 docs/progress.md 最新 Session）
cd frontend && npm run build      # 前端类型检查 + 构建（主包应低于 500kB 告警阈值）
```

（性能基准待补：compact 耗时、全量回放耗时。）
