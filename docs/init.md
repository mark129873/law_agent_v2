# 此文档内容暂时不执行

# init.md -- 开始工作前，请验证项目可以正常无报错构建。

1. 如果此次更新后端项目, 则验证后端项目可以正常构建与运行, 验证完成记得关闭:
  - 启动：`cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`

2. 如果此次更新前端项目, 则验证前端项目可以正常构建与运行, 验证完成记得关闭:
  - 构建：`cd frontend && npm install && npm run build`
  - 启动：`cd frontend && npm run dev`（http://localhost:5173，/api 代理到后端 8000；联调需先启动后端）

