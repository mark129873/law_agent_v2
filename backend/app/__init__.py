"""个人助手 harness 后端包。

模块划分（见 docs/ARCHITECTURE.md）：
- config:  .env 配置加载
- obs:     日志与观测初始化
- db:      SQLite 引擎与会话工厂
- models:  四张实体表（session/message/part/session_entry）
- main:    FastAPI 应用入口
- api/:    路由
- agent/:  agent 核心循环（后续功能）
- sessions/: 会话存储与回放（后续功能）
"""
