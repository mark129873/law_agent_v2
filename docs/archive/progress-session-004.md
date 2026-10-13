# 历史会话：Session 004

### Session 004
- 日期：2026-10-01
- 本轮目标：全量验证项目现状 → 修复验证发现的问题与三项遗留打磨项（路由恢复、subtask token 计入、bundle 分包），外加 ZCode 式可收起三栏壳层
- 验证结论（首轮）：pytest 70 passed、npm build 通过、清库真实启动健康、UI 冒烟正常、仓库卫生干净；发现 docs/clean-state-checklist.md 顶部是 v1 遗留核对结果（新问题）
- 已完成：
  - 重写 clean-state-checklist.md（移除 v1 的 Session 065/276测试/Milvus/RAG 残留，按 v2 路径与命令校准）
  - FE-006 前端路由：/ + /session/:id，刷新按地址恢复回放；404 分流（自家草稿留空态、未知/已删回首页，draft 集合随刷新清空）
  - BE-011 subtask token 计入：子循环 usage 与主循环同口径（add_tokens/token_count/Langfuse 带 subtask_id）
  - FE-007 代码分包：markdown 拆异步分包，主包 568→284kB，消除 Vite 告警
  - 壳层开合（并入 FE-006）：ShellContext + localStorage，左栏头部按钮开合、右栏点卡/X 收起（ZCode 工作台 isSidebarVisible/isSidePaneCollapsed 同语义）
  - docs 主文档结构化重排（表格/列表/ASCII 图），修正四表列漏 turn_id、死事件 turn_aborted、前端目录树失真
- 踩坑与修复：路由重构时把三栏的 flex 容器写成 Fragment 导致布局堆叠回归——DOM 断言全绿但截图一眼看穿；教训：UI 改动必须截图做视觉验收（visual-judge 不可用时人工审截图）
- 运行过的验证：`uv run pytest -q` 71 passed（含新增 test_subtask_tokens_counted）；`npm run build` 无告警；浏览器实测（直链/刷新恢复、草稿/未知地址分流、侧栏收起+刷新持久化、右栏面板开合），截图证据 tmp/gui-verify/；fixture 会话经 store 层直造（tmp/make_session_fixture.py，不烧 LLM）
- 已记录证据：feature_list.json 新增 BE-011/FE-006/FE-007 三条 passing 附证据
- 提交记录：4cef1f6(checklist) → a0bfbd2(路由) → 717a3aa(token) → 1e35fc7(分包) → bf9ff60(壳层) → 0af2866(docs 重排)
- 已知风险或未解决问题：
  - subtask"执行中→已完成"翻转瞬间仍未直接观测（机制等价已验证，沿用 Session 002 结论）
  - 历史消息全量加载，超长会话可能卡（分页/虚拟化留 v2）
  - failed 轮回放块头显示"已停止"（语义可再分）
  - 应用内自动化的 Playwright 点击在本机 IAB 环境频繁 actionability 超时（产品无碍，GUI 回归需靠 evaluate 点击或人工）
- 下一步最佳动作：真实使用打磨（工具卡 autoOpen、长会话分页按 handoff 优先级）
