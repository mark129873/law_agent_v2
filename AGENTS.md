# AGENTS.md

此项目描述:
-这是一个个人助手harness

## 开工流程
写代码前先做这些事：
0. 读取 scripts_mini_harness/mini_harness.py, 作为此项目harness思想的参考
1. 用 `pwd` 确认当前目录。
2. 读取 docs/ARCHITECTURE.md，了解完整架构与数据流定义
3. 读取 docs/PRODUCT.md，了解功能需求与用户侧交互行为
4. 读取 docs/RELIABILITY.md，了解日志、可观测性以及干净环境的相关要求
5. 读取 docs/progress.md，了解最新会话进度日志
6. 读取 docs/feature_list.json，确认当前所有功能的开发进度
7. 读取 docs/session‑handoff.md 获取记录当前会话的交接摘要, 上一轮交接信息
8. 用 `git log --oneline -5` 看最近提交
<!-- 9. 按需执行`init.md`的内容, 确保项目可正常构建或启动、初始化无异常。 -->
<!-- 10. 在开始新功能前，先跑你认为必需的测试与验证(测试前先根据 docs/RELIABILITY.md 进行测试干净环境管理) -->
<!-- 11. 如果9和10的验证一开始就失败，先修基础状态，不要在坏的起点上继续叠新功能。 -->

## 工作规则
- 新增功能时，请**先更新对应文档，再编写代码**。
- 同时只做一个功能, 串行开发。
- 不要因为“代码已经写了”就把功能标记为完成。
- 除非为了消除当前 blocker 的窄范围修复，否则不要扩大到其他功能。
- 实现过程中不要悄悄改弱验证规则。
- 优先依赖仓库里的持久化文件，而不是聊天记录。
- 数据库不做 schema 迁移（产品决策 2026-09-28，现在和将来都不做）：改 models.py 表结构后，删除 backend/data/ 重启即全新建表；测试一律从空库开始（pytest 已每测自动清空 .tmp-data，浏览器手测/E2E 前先清空 backend/data/）。

## 功能完成定义
一个功能只有在以下条件都满足时才算完成：
- 目标行为已经实现
- 你认为必要的验证真的跑过
- docs/feature_list.json 文件内该功能状态标记为 "passing", 并附上验证证据
- docs/ARCHITECTURE.md 和 docs/PRODUCT.md 文档同步更新
- 仓库仍然能按标准启动路径重新开始工作
- 在工作处于安全状态后, 代码已提交到git仓库, 提交信息清晰，符合项目规范。

## 收尾
结束会话前：
- 更新 docs/progress.md
- 更新 docs/feature_list.json
- 更新 docs/session‑handoff.md, 记录仍未解决的风险或 blocker
- 确认 docs/clean‑state‑checklist.md 所有校验项通过。
- 在工作处于安全状态后，用清晰的提交信息提交

## 后端代码规范
- 要求代码必须包含中文注释, 并解释做了什么, 这么做的原因
- 代码要求简洁精炼, 避免使用复杂的语法或模式, 保持代码结构清晰

## 前端代码规范
- 要求代码必须包含详细中文注释, 并解释做了什么, 这么做的原因, 适合0基础开发
- 代码要求简洁精炼, 避免使用复杂的语法或模式, 保持代码结构清晰

## 项目目录
一级目录:
- backend/ — 后端代码
- frontend/ — 前端代码
- docs/ — 项目文档
- scripts_mini_harness/ — harness 思想的参考实现: mini_harness.py 及配套 skills、test
- tmp/ — 临时文件目录(不入库, 可随时清空)

项目开发参考目录(.开头):
- .github_claude-for-legal/ — Claude for Legal 参考仓库
- .github_claude-for-legal-zh-cn/ — 上一仓库的中文翻译版(结构与英文版一致)
- .github_claude-for-legal-ZH/ — Claude for Legal ZH 参考仓库 (基于claude-for-legal适配中国版)
- .github_codex/ — OpenAI Codex CLI 源码仓库克隆, agent harness工程实现参考
- .github_ZCode/ — ZCode 源码仓库克隆, agent harness工程实现参考
- .github_learn-claude-code/ — learn-claude-code 教程仓库克隆, agent harness思想参考

### docs/ -- 文档导航

| 文档 | 内容 | 何时读 |
|---|---|---|
| [PRODUCT.md](PRODUCT.md) | 用户可见行为：界面、交互、展示规则、边界、非目标 | 做任何功能前 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系统实现：数据流、四表存储、agent 循环、SSE 协议、前端结构 | 改动实现前 |
| [RELIABILITY.md](RELIABILITY.md) | 日志与 Langfuse 约定、测试干净环境纪律（强制约束） | 写测试 / 手测前 |
| [init.md](init.md) | 构建与启动验证步骤 | 每次会话开工（AGENTS.md 开工流程） |
| [feature_list.json](feature_list.json) | 功能清单与状态（passing 必附证据，≤200 字） | 报告进度 / 收尾 |
| [progress.md](progress.md) | 会话进度日志（冷热分层：Session 数 ≤ 15，超出沉降 archive/） | 了解最新进展 / 收尾 |
| [session-handoff.md](session-handoff.md) | 会话交接：已验证 / 本轮改动 / 风险与 blocker / 下一步 | 接手会话 / 收尾 |
| [clean-state-checklist.md](clean-state-checklist.md) | 收尾核对清单（启动测试 / 文档同步 / 仓库状态） | 每次收尾 |
| archive/ | 冷存储（旧 progress/feature_list 条目沉降区） | 沉降时 |
