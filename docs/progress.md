# progress.md -- 会话进度日志

## 格式示例:
### Session xxx
- 日期：
- 本轮目标：
- 技术决策：
- 已完成：
- 运行过的验证：
- 已记录证据：
- 提交记录：
- 更新过的文件或工件：
- 已知风险或未解决问题：
- 下一步最佳动作：

## 下面为真实进度:

历史记录：[Session 001–003](archive/progress-sessions-001-003.md)。

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

### Session 005
- 日期：2026-10-01
- 本轮目标：删除 Langfuse，按 ZCode 思想改为本地 model-io JSONL 逐调用记录（项目根 log/，不入库）
- 技术决策：单用户本地项目不值得为逐调用观测自部署外部服务；ZCode 的 model-io JSONL（本地文件、一 session 一文件、逐调用全量快照）定位完全一致且零依赖
- 已完成：
  - 新增 backend/app/modelio.py：record_llm_call 逐行追加 JSONL（system 全文/messages 快照/tool 名称/response 文本+tool_calls/input+output tokens/耗时/错误/subtask_id）；写失败吞异常记 WARNING
  - 移除 Langfuse 全部接线：obs.py（init_langfuse/record_llm_call/_langfuse_client）、main.py 启动调用、config 4 字段、pyproject 依赖（uv remove）、.env.example 4 项、test_obs.py 整文件（6 用例）
  - loop.py 与 subtask.py 的 LLM 步改为调用 modelio（finally 中记录，成功与失败都记；subtask 带 subtask_id）
  - config 新增 MODELIO_DIR（默认 BACKEND_ROOT.parent/log，相对路径基于 backend/ 解析）；conftest 指向 .tmp-data/modelio 随测试清理；.gitignore 加 /log/
  - 测试：删 test_obs.py（6 例），新增 test_modelio.py（成功全字段/工具调用步/失败记录 3 例）；test_subtask_tokens_counted 改走 JSONL 断言
- 运行过的验证：`uv run pytest -q` **68 passed**（71-6 Langfuse+3 modelio）；清空 backend/data 真实启动 health/空列表正常、启动日志无 langfuse 残留；grep 全仓无 langfuse 引用
- 已记录证据：feature_list.json BE-010 → deprecated、BE-012 → passing 附证据
- 提交记录：见 git log（feat: 删除 Langfuse 改为 model-io JSONL 逐调用记录）
- 已知风险或未解决问题：
  - model-io 文件无轮转/上限（一 session 一个文件无限追加），超长会话文件会大——真实使用中按需再加轮转
  - modelio 含对话正文，目录已 gitignore 但需注意勿外传
  - 沿用 Session 004 其余风险清单（见 session-handoff.md）
- 下一步最佳动作：真实使用打磨；可选——把 loop.py usage 事件的 input_tokens 喂给 deps.last_input_tokens 激活 compact"真实 usage 优先"（当前该字段无人赋值，永远字符估算）
- 追加（同日）：存储三分工（SQLite 事实 / model-io JSONL / app.log）写入 ARCHITECTURE §3.0；用量聚合设计稿落 ARCHITECTURE §3.7
- 追加（同日，实施）：**用量聚合已实现**（§3.7 转已实现）——turn 事实 +input_tokens/context_tokens、session +input_tokens 列（已删库重建）、recorder.add_usage 取代 add_tokens、主循环喂 last_input_tokens 激活 compact 真实预算、**顺带修复潜伏 bug**（recalc_tokens_used 原是孤儿函数从未接线，regenerate 被删轮 token 双算；重写为 recalc_session_usage 并接线）、TokenBadge 改真实占用口径；pytest **70 passed**（+test_usage_aggregation 2 例，subtask 计入扩断言）、npm build 通过、浏览器实测徽标 1.8k/1.0M·0%（截图 v4-usage-badge.png）；feature_list +BE-013 passing
- 追加（同日，E2E 全量回归）：**mock LLM 服务**（tmp/mock_llm_server.py，本地 Anthropic 兼容 SSE，零成本全链路）驱动真实前后端跑完 **17 项 E2E**：流式/markdown 渲染、工具真实执行+二级折叠、任务板、subtask 实时面板（含失败注入与错误喂回）、审批批准+**拒绝**双路径（拒绝后文件未删）、错误卡+重试、停止保留部分输出、同会话 409、生成中刷新恢复、跨会话并行、删除会话、Enter/Shift+Enter、复制按钮、model-io 逐调用落库（子代理带 subtask_id、错误行）、用量聚合数值与 mock 脚本吻合。**新发现并修复 1 个缺陷**：回放审批留痕双卡（requested+denied 两条事实各渲染一张，出现"等待批准"幽灵卡）——replay 按 request_id 去重渲染最终状态，与实时流"单卡翻转"一致；DB 仍双写事实，测试语义同步修正并注明理由。E2E 截图 tmp/gui-verify/e2e-*.png
- 追加（同日，遗留问题清理）：**三项修复 + 一项观测补证**——① model-io 大小轮转（MODELIO_MAX_MB 默认 10MB，纳秒后缀防碰撞，记录零丢失有专测）；② cache 计量采集贯通（llm.py 捕获 cache_read/creation → recorder 三级事实 + turn 事实 data 键 + model-io usage，端点不支持自动为 0）；③ failed 轮块头改「已失败 {耗时}」（原误显示"已停止"，与用户主动停止区分；PRODUCT 状态机补行）；④ subtask"执行中→已完成"翻转瞬间已用 mock 延迟直接观测（卡片+面板双截图 v5-subtask-running.png），销掉 Session 002 以来的未观测风险项。pytest **71 passed**（+轮转测试），npm build 通过，E2E 截图 v5-failed-header.png
- 追加（同日，UI 质感升级 design-taste pass）：**preserve 模式视觉重设计**（不换主题身份、不动 IA）——① token 层：tabular-nums、细滚动条、selection/focus-visible 强调色、消息入场 animate-enter（reduced-motion 尊重）；② Composer 重设计为一体化浮动输入卡（rounded-2xl 容器+focus-within ring+圆形发送钮，生成中方形停止钮）；③ 欢迎页排版重排（标题层级+四项能力内联图标行+hairline+"数据仅保存在本机"脚注）；④ 侧栏选中态 sky 竖条、空状态弱化；⑤ 工作块头 hover、审批卡 em-dash 改 ·；⑥ 审批弹窗 backdrop-blur+Warning 图标+入场动画；⑦ 圆角体系写入文档（按钮 lg/卡片 xl/输入卡 2xl）。**视觉验收抓出并修复 1 个路由回归**：侧栏新建草稿未登记 draftIdsRef，草稿页 404 被当未知会话弹回首页（欢迎页路径有登记故 E2E 未暴露）——统一收敛到 handleDraftCreated。截图 ui-1~4 + ui-3-draft-fixed.png。build 通过
- 追加（同日，权限体系复刻 5 轮 ZCode 源码深研后实施，分 4 commit）：**ZCode PermissionService 全套复刻**——阶段1 权限内核（permission_service.py：11 工具能力声明/bash 只读命令运行时降级/评估顺序硬拒→plan进出→yolo(plan失效)→deny规则→ask规则→plan只读其余DENY→allow规则→edit→build；execution_state.py 持久化 mode/planEnabled+规则集；permissions.py 退役，删除类词表由 build high-risk ask 替代）；阶段2 审批选项流（动态选项 allowOnce/fullAccess/allowAlways/deny+freeText 反馈喂回；"总是允许"落盘规则文件，高危根命令退化整条精确；fullAccess 一键切 yolo；子代理抑制 fullAccess）；阶段3 计划模式（enter/exit_plan_mode 工具+计划文件 data/plans/+计划审批对话 markdown 渲染+系统提示词计划段注入+Composer 模式切换器随提交生效）；阶段4 E2E（P1 计划全流程/P2 edit 自动编辑/P3 完全访问一键+真实执行/P4 总是允许规则落盘 echo:*）。pytest **83 passed**（+12 权限相关），npm build 通过，截图 pm-1~5。**测试纪律**：自动化全程无高危操作（ask 触发用无害 echo 重定向）；高危真实删除/越界/恢复项列清单留用户手测。环境事故两起（前后端进程静默死亡+mock 计数器串场）已重启排除，非产品问题

### Session 006

- 日期：2026-10-08
- 目标：分析项目，简洁重写 ARCHITECTURE / PRODUCT / RELIABILITY；仅文档。
- 完成：
  - 用表格与分级列表整理职责、四表/回放、循环/权限、压缩、API/SSE、前端与验证纪律。
  - 按源码校准React19、JSONL替代Langfuse、子助手工具/审批、压缩事实与用量边界。
  - 静态发现审批停止、运行态/审批恢复、工具事件、压缩接线缺口；未复现、未修复，已入交接。
- 验证：源码与文档静态核对、链接/JSON/差异检查；用户明确免启动验证，未运行启动、build、pytest或E2E。
- 功能清单：20项passing、1项deprecated；保留状态、历史证据与testedAt，仅校正过时描述、追加复核记录。
- 收尾：按clean-state-checklist核对文档/仓库卫生；Session数6、passing数20；无代码/运行数据/敏感文件改动，启动测试项本轮不适用。
- 提交：docs: 精简架构、产品与可靠性文档并校准实现边界。
- 下一步：优先复现审批停止与回放运行态问题，再按单功能规则修复。
- 追加（同日，按用户进一步聚焦）：
  - ARCHITECTURE由232行收敛至98行，仅九机制对照、对话/model-io存储、上下文压缩；删去前端、API清单、完整风险表。
  - model-io格式/目录/写入/轮转/失败语义从RELIABILITY移入；可靠性文档保留工程日志、测试纪律与指向链接。
  - 明确Web未实现GoalLoop、任务板三轮提醒、技能目录注入；完整静态缺口留handoff；未改代码或运行数据。
  - 核对文档链接/JSON/差异、历史状态与证据；不运行启动、构建或测试；Session仍6、passing仍20。
  - 提交：docs: 聚焦后端Harness九机制、对话存储与上下文压缩。
- 追加（同日，文件导航）：九机制表13处文件引用改为相对Markdown链接，逐一核对目标存在；仅文档，不运行启动或测试；提交：docs: 为九机制源码文件添加相对跳转链接。

### Session 007

- 日期：2026-10-08
- 目标：后端简化；保留九机制现有行为与未接通入口。
- 范围：未使用代码、turn装配、消息/部件查询、主/子模型调用记录和审批等待；不变更schema、前端或工具注册方式。
- 完成：发送/重跑共用turn装配，回放/模型历史共用查询，主/子共用响应聚合、model-io记录与审批工时；清理未用常量/导入/参数。
- 验证：隔离路径预检；基线83 passed → 全量87 passed（新增4例）；Stop续轮、子助手独立历史/权限反馈/预算、失败调用快照、观测失败不阻断均通过；未启动真实服务。
- 收尾：.tmp-data无残留；文档/JSON/差异核对；Session数7、passing功能数20，历史证据保留；仅提交代码、测试与文档。
- 提交：refactor: 收拢后端Harness重复编排并保留机制边界。
- 下一步：按用户指定design-taste-frontend技能重构前端视觉，串行开展。

### Session 008

- 日期：2026-10-08
- 目标：商业产品质感的助手工作台；使用design-taste-frontend，按用户最终选择采用暖金黄。
- 设计：Fluent产品界面原则；白色/微暖灰/石墨文字/暖金黄；变化6、动效3、密度4。保留品牌、路由、三栏职责和权限选项。
- 完成：
  - 重做欢迎页、会话导航、输入卡、工作记录与子助手面板；统一色彩、间距、焦点和圆角。
  - Markdown补GFM表格/列表、代码排版；复制与重跑同一行，键盘也可访问。
  - 去除嵌套按钮；审批焦点循环、数字反馈防误触、提交防重复/失败提示；列表/新建失败可见。
  - 窄窗口导航抽屉与子面板覆盖；减少动态效果时禁用动画/过渡；品牌favicon替换Vite默认图标。
  - 新增remark-gfm；source-map-js仅补丁升级1.2.1→1.2.2，修复审计报告。
- 验证：
  - build通过，主包366.14kB、Markdown异步包326.57kB；lint 0错误/5条警告，与重构前一致；audit 0漏洞。
  - 浏览器使用内存API/假SSE：发送、Enter/Shift+Enter、停止保留正文、重跑、复制、模式与计划、实时/恢复审批、数字反馈不误批、Tab循环、计划全文、工具/任务板/子面板、侧栏记忆、删除确认取消、404/草稿刷新、请求失败均通过。
  - 桌面与390px窗口截图审视；390px页面无横向溢出；减少动态效果下animation=none、transition=0s；暖黄按钮文字对比9.33:1。
  - 技能逐项预检：适用项通过；营销布局/图片/深色模式/GSAP等不适用于本工作台，记录于tmp/ui-redesign/preflight.md。
- 证据：tmp/ui-redesign/；暖黄最终图welcome-warm.jpg、chat-warm.jpg；frontend_review独立记录，保留历史功能状态与证据。
- 收尾：clean-state-checklist逐项通过；真实backend/data、workspace和log未改；.tmp-data无残留；Session数8、passing功能数20；本轮预览服务已停止、临时视口已恢复；临时脚本/截图、依赖与构建产物不入库。
- 提交：feat: 重构暖黄助手工作台并完善交互可访问性。
- 边界：本轮为UI模拟验收，未重复真实后端E2E；后端简化87项测试见Session 007；既有Harness缺口未扩修。
- 下一步：按交接清单逐项核对后端审批等待、回放运行态与压缩接线。



### Session 009

- 日期：2026-10-08
- 目标：按用户要求移除独立Plan模式；云电脑dot分支开发并推送，main不改。
- 过程：先读AGENTS、mini_harness与项目文档，参考ZCode执行状态；先更新PRODUCT/ARCHITECTURE，再串行实现。
- 完成：删除Plan开关、请求/执行状态字段、进出工具、系统提示注入、专用审批与计划归档；保留todo、build/edit/yolo、普通审批、子助手与聊天历史。未新增历史兼容或迁移。
- 数据：开始时此checkout没有backend/data或log，无旧历史/计划可清理；只生成离线测试数据，未读取或修改凭据。
- 验证：
  - 锁文件安装依赖；假模型/隔离空库。origin/main同环境复测84过/3失败；本轮全量91过/同3失败，新增执行模式专项12过。
  - 既有失败：缺powershell使echo与批准后Remove-Item失败；Linux把C:/路径视为工作区内相对目录，Windows盘符越界断言失败。没有跳过、改弱断言或扩修跨平台。
  - build通过，主包361.83kB；lint 0错误/原有5警告；生产源码无Plan标志/工具/审批残留。
  - 标准uvicorn 127.0.0.1:8100启动成功；health=ok、sessions=[]、permission/state仅mode=build；正常停止。
  - 云浏览器两次尝试未完成：首次权限检查dismiss，随后ERR_BLOCKED_BY_CLIENT，原因未确认；未改路绕过，没有截图或UI通过结论。
- 收尾：文档/JSON/差异核对；.tmp-data无残留；临时服务停止；测试/依赖/构建产物不入库。功能复核标blocked，保留浏览器验收缺口，不宣称全部完成。
- 提交：fbc907c（refactor: 移除独立Plan模式并保留普通权限流程）；已通过既有认证的云终端推送dot并核验远端；exec上下文缺凭据的临时阻塞已解决，main未改。
- 下一步：可访问测试页面后补浏览器模式切换/普通审批验收；Windows环境复测既有PowerShell与路径断言。

### Session 010

- 日期：2026-10-08
- 目标：继续诊断并补Plan移除后的云浏览器验收。
- 新证据：云桌面截图显示127.0.0.1:5173“此页面已被某个扩展程序屏蔽”，ERR_BLOCKED_BY_CLIENT；正常云浏览器API同错。确认拦截发生在客户端，具体扩展/规则未确认，不能推断应用有错。截图见本轮工具记录，未落盘。
- 边界：错误页绑定另报URL policy/protocol not allowed；停止该路径，未禁用安全设置、换地址或浏览器绕过。官方浏览器排障文档未提供适用的授权本地预览入口。三模式/todo/审批/历史回放及取消/返回/重复输入的本轮UI验收仍未完成。
- 服务：此前测试data移入tmp后重新启动既有假LLM脚本，启动日志正常；exec随后executor key changed，无法恢复该会话停止进程。后续查询未见serve_fake/vite，但不能证明宿主完整状态；原生终端输入报window does not expose a Paste action。不能将未确认清理写成已完成。
- 收尾：仅更新验收文档，不改源码；既有12专项/91过3既有失败/build证据不变，未重新全量运行、无真实付费模型调用。JSON/差异检查通过；功能状态维持blocked，宿主服务清理待确认。

### Session 011

- 日期：2026-10-09
- 目标：先将dot快进合并并推送main，再按用户要求清理无用代码与重复入口；保留参考脚本，删除参考测试。
- 完成：
  - main已快进到dot的6d8b8b6并推送，随后在main进行本轮清理。
  - 删除未消费的前端token状态、未使用接口/setter导出、无效果JSON replacer；组件内部常量与函数收为私有。
  - 删除后端未用布尔配置函数、默认规则常量；技能handler直接注册已有实现。
  - 删除根目录重复pyproject/uv.lock、Vite素材与前端模板README；根README改为Web/CLI项目导航，校准启动命令和文档链接。
  - 按用户明确要求删除scripts_mini_harness/test/test_harness.py及pytest开发依赖；保留mini_harness.py、配套技能和独立运行依赖，锁文件仅移除7个测试相关包。
- 验证：
  - 标准backend/ uv run pytest -q：91 passed / 3既有平台失败，与本轮合并后基线一致；没有跳过、修改测试断言或扩修跨平台行为。
  - frontend/ npm run build通过（主包361.65kB，Markdown包326.57kB）；npm run lint无错误，既有警告由5降至3。
  - CLI uv sync --locked与锁文件一致性检查通过；uv run python mini_harness.py输入q正常启动并退出，无模型调用；保留运行依赖的完整锁记录未变。
  - backend/data与log的文件元数据快照一致；pytest临时目录已清理。JSON、文档链接、差异与仓库卫生核对通过；本轮无用户可见交互或视觉改动，未重复浏览器E2E。
- 收尾：cleanup_review单独记录清理验收，不覆盖19项passing、1项deprecated、1项blocked的历史状态；既有Harness和UI验收缺口保留。临时记录、依赖与构建产物不入库。
- 提交：refactor: 清理无用代码与重复项目入口；按用户要求推送main。
- 下一步：按需收拢前端发送/重跑生命周期与审批/子助手状态；另行处理既有平台测试和UI验收缺口。


### Session 012
- 日期：2026-10-09
- 目标：按 ZCode 29628c9 对齐四表组织，仅保留现有功能所需字段；基准 main 84da92c。
- 实现：message 元信息与统一 text part；tool 的 type/callID/tool/state；轮次 metadata.turnId；session_entry 时间与 upsert；会话用量事实聚合；历史/压缩/审批/重跑适配。修复刷新运行态误用 session ID。
- 验证：新增存储回归10过，受影响专项61过，全量101过/3既有平台失败；前端 build/type 通过361.65kB，lint 0错误/3既有警告。基线另有7项缺测试配置/误建真实客户端问题，由隔离假客户端夹具修正；不改变生产模型接入。
- 浏览器：云电脑真实前后端+假模型+隔离SQLite，发送/任务板/刷新/重跑/审批通过与拒绝/刷新恢复审批/停止保留部分正文/错误重试通过；用户5条无重试重复，助手8条，无message正文副本，外键检查空、完整性ok。截图 tmp/storage-ui-smoke.png（不入库）。
- 数据：原 checkout 无旧 app.db，无删除用户数据；已建立 backend/data/app.db 空四表（每表0行）。测试服务全部停止，tests/.tmp-data 已清理。
- 边界：PowerShell缺失2项、Windows路径1项既有失败未扩修；不用真实模型。ZCode未用字段/功能不复制；todo/error等现有类型保留，四表之外不扩展。
- 交付：源码 ZIP 与补丁；随后按授权经 GitHub 插件提交 dot，远端结果另行核对。无终端push、无合并或部署。

### Session 013
- 日期：2026-10-10
- 目标：更新README四表说明；ARCHITECTURE聚焦后端Harness；主循环仅改while True写法，保留40模型步上限及结束/停止/错误语义。
- 实现：文档覆盖模块边界、turn执行、三种历史投影、状态归属、共享Queue/SSE、审批恢复与扩展入口；修正RELIABILITY章节引用及README交接链接。
- 验证：新增4项步数边界通过（0/1/40步用尽、末步正常结束）；全量105通过/3既有平台失败；以原循环复测同3项失败（PowerShell缺失2项、Windows路径1项）。文档链接、JSON及git diff --check通过；测试临时库无残留。
- 边界：不新增模型失败重试、不改变子助手30步限制、不修权限/前端既有缺口；无真实模型调用，无UI变更，未启动服务/浏览器验收。
- 交付：用户授权通过GitHub插件提交main；远端提交结果以实际核对为准。


### Session 014
- 日期：2026-10-10
- 本轮目标：仅 local_setting 的 permission 存储；按用户最新指令收窄范围。
- 技术决策：对齐 ZCode 八字段/四项联合主键/双索引；scope=project、namespace=permission，mode/ruleset 分行 JSON；规范工作区路径 SHA-256 为当前项目标识。
- 已完成：原读写入口改 SQLite，同进程规则追加加锁；默认 build/空规则，旧 JSON 保留但不读取/迁移；AGENTS 末尾注明 README 仅用户手动修改。四张会话表/审批UI/会话授权均未改。
- 验证：新增10例过；全量115过/3既有平台失败（2项PowerShell缺失，1项Windows路径）；真实服务跨进程读取edit、会话列表200。无真实模型/用户数据访问。
- 收尾：隔离空库清理，README diff为空，JSON/diff检查通过；通过GitHub插件提交并核验远端，提交号见git log。
- 边界：当前仍单工作区，未增多项目UI；移动工作区视作新项目；现有四表已有旧schema问题不在本次自动迁移。审批三选项和会话级授权留待后续。


### Session 015
- 日期：2026-10-10
- 完成：存储改为 ZCode 必要六表子集；字段、JSON、序号与语义对齐，移除 session_entry。
  - todo 独立表；turn_usage 记录总耗时/用量；审批仅内存；压缩边界使用消息 ID。
- 验证：pytest 121 过；build 通过；lint 0 错误/3 既有警告。云浏览器实测任务板、刷新审批、允许/拒绝、子助手、错误重试、停止、重启恢复通过；全程假模型。
- 收尾：README 未改；无旧数据库；隔离测试服务停止，临时产物不入库。对齐决定见 storage-subset.md。
- 交付：通过 GitHub 插件提交 main；提交号以远端核验为准。

### Session 016
- 日期：2026-10-10
- 完成：拆分审批回放函数；权限草稿/折叠状态按输入变化同步；秒表仅定时回调更新，3 条 lint 警告清零。
- README：用户本次授权仅更新 2.1 六表说明，其他章节逐字核对未变。
- 验证：lint 0 警告/0 错误，build 通过；pytest 121 过；云浏览器模拟组件验证权限回显、输入保留、草稿发送、计时、折叠及成功/失败/停止状态。
- 收尾：测试服务停止，临时页移入 tmp；通过 GitHub 插件提交 main。


### Session 017
- 日期：2026-10-12；基准：本地/远端 main 4d2e7a3，开工工作树干净。
- 范围：只修六项，串行开发；README、schema及其他静态缺口不改。
- 完成：保守命令白名单；主/子审批可停止且迟到批准失效；发送/重跑装配失败释放资源；空摘要拒绝提交；每步预算与一次超限恢复；前端审批刷新不清流，旧GET按代数/请求号丢弃，迟到审批应答仍能收口已完成快照。
- 预算边界：只摘要旧轮，保留当前用户原文和工具配对；当前轮自身过大明确失败，不重跑已执行工具；40主模型步包含失败请求。真实用量高于估算时仍摘要；模型记录器进入失败也能收口。
- 验证：后端220过（基线121，新增99）；前端6项App/Hook调度回归、测试页typecheck、lint/build通过，主包362.52kB。隔离uvicorn health=ok、sessions=[]，退出后清理测试库。
- 浏览器：云浏览器真实React/App＋内存API/假SSE；批准后续流/工具/下一审批/拒绝/旧GET延迟到新轮/最终回放均通过。截图tmp/priority-ui/{approval-next,approval-stream-continued,stale-get-new-turn,final-replay}.png；全部模拟数据，无模型调用。
- 环境：exec网络隔离导致预览连接拒绝；云桌面终端＋共享依赖启动后通过。服务已停止，临时数据/依赖/截图不入库。
- 收尾：JSON/文档链接/diff检查；测试目录无残留；Session数17，原功能passing数19。README差异为空，未提交或push，待父级审核和用户发布确认。
- 保留：PowerShell非系统沙箱、Windows实机未验；当前轮超大工具输出不自动归档；审批弹窗无停止入口；未扩修其他审查项。


### Session 018
- 日期：2026-10-12；基准main 9166452，开工工作树干净。
- 完成：审批固定allowOnce/allowSession/deny；移除永久规则/全访问入口及执行逻辑。项目模式、已有ruleset及yolo判定顺序不变。
- 范围：会话内存授权绑定工具＋规范文件路径；命令/其他工具精确匹配完整参数，未知字段不放大权限。跨turn/刷新有效，重启/删除清空；重跑保留会话范围。
- 安全：API核验session/选项/待决状态；迟到、重放及未知旧选项失败。等待方在停止/取消/策略与路径复检后保存内存授权；硬拒及现有deny结果优先，不写local_setting。
- 验证：后端266项通过；前端11项回归、测试页typecheck、lint/build通过，主包359.12kB；隔离启动health=ok、sessions=[]、mode=build，结束清理。
- 浏览器：云浏览器真实App＋内存API/假SSE，三项中文/数字键/反馈不误批准、会话允许后续流/下一审批/拒绝、旧GET不盖新turn、刷新回放后allowSession/allowOnce通过；截图tmp/session-approval-ui，全部模拟数据，无模型调用。
- 收尾：按AGENTS将旧Session 001–003原文归档，热日志15轮。README/schema不改；测试目录和服务清理；diff/JSON/文档链接检查通过。代码冻结，未commit/push，由父级审核后按授权经GitHub插件发布。
- 边界：仍单进程；Windows实机未验，PowerShell非系统沙箱；审批弹窗无停止入口为既有项。浏览器验收为模拟API，非真实后端全链路。
