# init.md -- 开始工作前，请验证项目可以正常无报错构建。

## 后端（backend/，uv 管理）

1. 首次准备：`cd backend && cp .env.example .env` 并填入 `ANTHROPIC_API_KEY` 等
2. 安装依赖：`cd backend && uv sync`
3. 启动验证：`cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`，然后访问 `http://127.0.0.1:8000/api/health` 应返回正常；验证完成记得关闭
4. 测试：`cd backend && uv run pytest -q`（测试使用 `tests/.tmp-data/`，跑完自动清理）

## 前端（frontend/）

1. 安装依赖：`cd frontend && npm install`
2. 构建验证：`cd frontend && npm run build`
3. 启动：`cd frontend && npm run dev`（http://localhost:5173，/api 代理到后端 8000；联调需先启动后端）

## 注意

- 后端无鉴权且仅监听 127.0.0.1，不要改为 0.0.0.0 暴露到局域网
- `backend/data/`（SQLite/工作区/日志）与 `backend/tests/.tmp-data/` 均不入库
