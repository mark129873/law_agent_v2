# 个人助手 Harness

单用户本地 Web 助手：对话、读写工作区文件、执行命令、管理任务和派出子助手。
会话与日志保存在本机，对话按配置发送至模型 API。

## 项目入口

| 目录 | 用途 |
|---|---|
| [backend/](backend/) | FastAPI + SQLite 后端，使用自己的 Python 配置与锁文件 |
| [frontend/](frontend/) | React + TypeScript + Vite 前端 |
| [scripts_mini_harness/](scripts_mini_harness/) | 独立 CLI 参考实现，使用自己的 Python 配置与锁文件 |
| [docs/](docs/) | 产品、架构、验证纪律与会话交接 |

根目录只提供项目导航；安装依赖、运行和测试都在对应子目录进行。

## 运行 Web 助手

需要 Python 3.12+、uv 和 Node.js。首次运行按 [启动说明](docs/init.md) 安装依赖、
配置 `backend/.env`，然后在两个终端分别启动：

```bash
cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8100
```

```bash
cd frontend
npm run dev
```

打开 Vite 输出的本地地址。后端命令工具使用 PowerShell，需要运行环境提供该命令。

## CLI 参考实现

[mini_harness.py](scripts_mini_harness/mini_harness.py) 用于理解九个 Harness 机制，
与 Web 后端各自独立；`/goal` 只在此参考实现中提供。
先复制 `scripts_mini_harness/.env.example` 为同目录的 `.env` 并填写模型配置，再运行：

```bash
cd scripts_mini_harness
uv sync --locked
uv run python mini_harness.py
```

## 文档

- [产品行为](docs/PRODUCT.md)
- [架构与九机制实现](docs/ARCHITECTURE.md)
- [运行与测试纪律](docs/RELIABILITY.md)
- [当前交接与未解决项](docs/session-handoff.md)
