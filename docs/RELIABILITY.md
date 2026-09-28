# 可靠性文档 — 可观测性、测试干净环境管理、基准测试

## 日志管理

- 使用标准 `logging`，由 `backend/app/obs.py` 统一配置：控制台（人读格式）+ 滚动文件 `backend/data/logs/app.log`（按大小滚动，保留 N 份）。
- 记录**工程事件**：turn 开始/结束/停止（含 turn_id、工时）、工具调用与耗时（工具名 + tool_call_id，不记参数全文）、审批请求与结果（request_id + 决定）、压缩发生（tokens_before/after）、错误堆栈。
- **不记录**用户输入与模型回复正文（正文属于数据库与 Langfuse 的职责），保证双系统不重复。
- 级别约定：ERROR=需要人处理的异常；WARNING=可自动恢复的异常（如反应式压缩重试、Langfuse 上报失败）；INFO=生命周期事件；DEBUG=开发期细节，默认关闭。

## Langfuse 链路追踪

- 开关与配置：`LANGFUSE_ENABLED`（**默认 false**）+ `LANGFUSE_BASE_URL` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`，见 `backend/.env.example`。
- 开启时记录 **LLM 交互细节**：system prompt、messages、completion、token 用量、模型名、耗时；以 session_id 为 trace、turn_id 为 span 组织，与 logging 的工程事件互不重复。
- 关闭时零开销：不初始化 SDK、不发任何网络请求。
- Langfuse 不可用（网络失败）不得影响对话主流程：初始化与上报全部吞异常并记 WARNING。

## 测试干净环境管理 
保证测试从一个已知的空白状态启动，避免历史遗留数据干扰测试结果，引发未知异常。

### 数据重置机制（测试前必须运行）
- 测试专用数据目录：`backend/tests/.tmp-data/`（项目内实际目录，不用系统临时目录）。conftest.py 的 fixture 在**每个测试前清空重建**该目录，**测试后必须清理**（teardown 删除），保证仓库干净。
- 被测代码的数据目录必须可通过配置注入（DATA_DIR 环境变量），测试永远指向 `.tmp-data/`，**绝不读写真实 `backend/data/`**。
- 浏览器手测/E2E 与 pytest 同纪律：启动真实后端前先清空 `backend/data/`。生产语义即"改表/发版 = 清库重建"（产品决策 2026-09-28：不做 schema 迁移）。

### 测试高风险项目（强制约束）
1. **假 LLM**：单元/集成测试一律使用假 LLM（mock 客户端 + 预录的假流式序列），禁止真实调用付费 API；真实调用仅允许人工手动验证。
2. **测试目录**：只用 `backend/tests/.tmp-data/`，测试后清理；禁止测试触碰真实数据目录、workspace、用户文件。
3. **执行类测试**：仅跑白名单安全命令（如 `echo`）；禁止测试真实的删除、格式化、网络危险命令；删除逻辑用 mock 或专用测试沙箱验证。
4. **破坏性操作**：压缩、软删等测试必须验证"数据可回放/可恢复"（软删标记正确、回放不出现已删会话），且使用测试目录内构造的数据，不碰真实数据。

## 基准测试
测试执行命令:
```bash
cd backend && uv run pytest -q
```
（后续补充性能基准：compact 耗时、全量回放耗时。）
