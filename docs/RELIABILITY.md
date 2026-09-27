# 可靠性文档 — 可观测性、测试干净环境管理、基准测试

## 日志管理
xxx

## Langfuse 链路追踪 - 暂时未实现
开关与配置：`LANGFUSE_ENABLED`（默认 true）+ `LANGFUSE_BASE_URL` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`

## 测试干净环境管理 
保证测试从一个已知的空白状态启动，避免历史遗留数据干扰测试结果，引发未知异常。

### 数据重置机制（测试前必须运行）
xxx

## 基准测试
测试执行命令:
