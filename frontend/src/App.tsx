/**
 * 应用根组件：三栏布局（左侧会话列表 / 中间对话流 / 右侧 subtask 查看面板）。
 *
 * 路由结构（react-router-dom）：
 * - `/` 欢迎页（未选会话）；
 * - `/session/:sessionId` 会话视图（SessionView）——点击列表项、新建草稿都导航到该地址，
 *   刷新/重开浏览器后按地址恢复会话回放；404（草稿无落盘内容、未知或已删 id）回首页。
 *
 * 状态职责划分：
 * - AppShell 持有会话列表（侧栏轮询数据源）与"新建/打开会话"导航动作；
 * - SessionView 持有单个会话的详情与实时 turn，key=sessionId 重挂载天然隔离会话切换；
 * - useSessionStream 负责 SSE 事件 → 实时 turn；turn 收口后重新拉详情以落盘事实替换。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Route, Routes, matchPath, useLocation, useNavigate, useParams } from 'react-router-dom'

import { ChatArea } from './components/ChatArea'
import { Sidebar } from './components/Sidebar'
import { SubtaskViewer, type SubtaskItem } from './components/SubtaskViewer'
import { ApiError, createSession, getPermissionState, getSessionDetail, stopTurn, type ExecutionDraft, type PermissionState } from './api/client'
import { ShellContext, useShell } from './ShellContext'
import { useSessionStream } from './hooks/useSessionStream'
import { ArrowRight, FileText, ListChecks, SidebarSimple, SquaresFour, TerminalWindow, UsersThree } from '@phosphor-icons/react'
import './workspace.css'
import type { SessionDetail, SessionItem } from './types'

/** 左栏开合的本地记忆键：ZCode 同语义（isSidebarVisible 持久化） */
const SIDEBAR_STORAGE_KEY = 'ui.sidebar'

export default function App() {
  // BrowserRouter 已在 main.tsx 提供，这里只做布局与路由分发
  return <AppShell />
}

/** 布局壳：地址解析出当前会话 id（侧栏高亮用），按路由切换中/右栏内容 */
function AppShell() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  // 当前会话 id 直接从地址派生（/session/:id），刷新后仍成立
  const currentId = matchPath('/session/:sessionId', pathname)?.params.sessionId ?? null
  const [sessions, setSessions] = useState<SessionItem[]>([])
  // 本应用本次运行中创建的 draft id：详情 404 时用来区分"自家草稿"与
  // "未知/已删会话"——前者留在空对话态等首条消息落库，后者才回首页
  const draftIdsRef = useRef<Set<string>>(new Set())

  // 左栏开合：初始值读本地记忆，切换时写回（ZCode 工作台同款持久化）
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try {
      const saved = localStorage.getItem(SIDEBAR_STORAGE_KEY)
      // 首次在窄窗口打开时先展示正文；已有开合偏好仍优先使用。
      return saved ? saved !== 'collapsed' : !window.matchMedia('(max-width: 760px)').matches
    } catch {
      return true // localStorage 不可用（隐私模式等）：默认展开
    }
  })
  const toggleSidebar = useCallback(() => {
    setSidebarOpen((open) => {
      const next = !open
      try {
        localStorage.setItem(SIDEBAR_STORAGE_KEY, next ? 'open' : 'collapsed')
      } catch {
        /* 写不进去就只当次会话生效 */
      }
      return next
    })
  }, [])

  /** 导航进入指定会话（选中列表项共用） */
  const openSession = useCallback(
    (id: string) => navigate(`/session/${id}`),
    [navigate],
  )

  /**
   * 侧栏/欢迎页新建草稿的统一出口：先把 id 登记进 draftIdsRef 再导航。
   * 漏登记会让草稿页的 404 被当成未知会话弹回首页（实测踩过的坑）。
   */
  const handleDraftCreated = useCallback(
    (id: string) => {
      draftIdsRef.current.add(id)
      openSession(id)
    },
    [openSession],
  )

  /** 新建会话：拿 draft id 后进入会话视图（首条消息发出才落库） */
  const handleNewSession = useCallback(async () => {
    const { id } = await createSession()
    handleDraftCreated(id)
  }, [handleDraftCreated])

  return (
    /* 三栏布局的 flex 容器：侧栏与路由出口（中/右栏）必须并排，
       不能换成 Fragment——容器一旦消失，侧栏与主区会垂直堆叠（实测踩过） */
    <ShellContext.Provider value={{ sidebarOpen, toggleSidebar }}>
      <div className="app-shell flex">
      {/* 左栏：会话列表 */}
      <Sidebar
        sessions={sessions}
        currentId={currentId}
        onSelect={openSession}
        onSessionsChange={setSessions}
        onDraftCreated={handleDraftCreated}
      />

        <Routes>
          {/* 欢迎页 + 会话视图；其余任意地址兜底回欢迎页 */}
          <Route path="/" element={<WelcomePage onNewSession={handleNewSession} />} />
          <Route
            path="/session/:sessionId"
            element={<SessionRoute draftIdsRef={draftIdsRef} />}
          />
          <Route path="*" element={<WelcomePage onNewSession={handleNewSession} />} />
        </Routes>
      </div>
    </ShellContext.Provider>
  )
}

/** `/` 欢迎页：未选会话时的空状态（原来在 ChatArea 里的未选中分支） */
function WelcomePage({ onNewSession }: { onNewSession: () => Promise<void> }) {
  const { sidebarOpen, toggleSidebar } = useShell()
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 欢迎页与侧栏都可建草稿；这里显示失败原因，避免点击后无响应。
  const start = async () => {
    if (creating) return
    setCreating(true)
    setError(null)
    try { await onNewSession() }
    catch (e) { setError(e instanceof Error ? e.message : '新建会话失败，请重试') }
    finally { setCreating(false) }
  }
  const capabilities = [
    { icon: FileText, title: '文件工作', text: '阅读、整理与修改工作区文件' },
    { icon: TerminalWindow, title: '命令执行', text: '按你的授权，在本地运行命令' },
    { icon: ListChecks, title: '任务计划', text: '拆分目标，记录每一步工作进展' },
    { icon: UsersThree, title: '子助手协作', text: '将独立工作交给专注的子助手' },
  ]
  return (
    <main className="flex min-w-0 flex-1 flex-col bg-white">
      <header className="workspace-header">
        <SidebarToggle sidebarOpen={sidebarOpen} onToggle={toggleSidebar} />
        <span className="text-[13px] font-medium text-zinc-600">工作台</span>
      </header>
      <div className="flex flex-1 overflow-y-auto">
        <div className="welcome-content animate-enter">
          <div className="mb-7 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent-50 text-accent-700">
            <SquaresFour size={28} weight="duotone" />
          </div>
          <h1 className="welcome-title font-semibold text-zinc-900">从一个目标开始。</h1>
          <p className="mt-4 max-w-md text-[15px] leading-7 text-zinc-600">
            告诉助手你想完成什么。探索文件、安排任务，<br className="hidden sm:block" />
            一起把下一步工作推进下去。
          </p>
          <button type="button" onClick={() => void start()} disabled={creating} className="primary-button mt-7">
            {creating ? '正在新建…' : '新建会话'} <ArrowRight size={17} weight="bold" />
          </button>
          {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
          {/* 能力是说明文字，不伪装成尚未实现的入口。 */}
          <div className="capability-grid mt-14 border-t border-zinc-200/80 pt-8">
            {capabilities.map(({ icon: Icon, title, text }) => (
              <div key={title} className="flex items-start gap-3.5">
                <Icon size={22} weight="duotone" className="mt-0.5 shrink-0 text-accent-700" />
                <div>
                  <h2 className="text-[14px] font-semibold text-zinc-800">{title}</h2>
                  <p className="mt-1.5 text-[13px] leading-6 text-zinc-600">{text}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
      <p className="shrink-0 px-6 pb-6 text-center text-[12px] text-zinc-500">会话保存在本机 · 对话按配置发送至模型 API</p>
    </main>
  )
}

/** 左栏开合图标按钮（Phosphor SidebarSimple，两态高亮区分） */
function SidebarToggle({ sidebarOpen, onToggle }: { sidebarOpen: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="icon-button"
      aria-label={sidebarOpen ? '收起侧栏' : '展开侧栏'}
      aria-pressed={sidebarOpen}
      title={sidebarOpen ? '收起侧栏' : '展开侧栏'}
    >
      <SidebarSimple size={20} weight="regular" />
    </button>
  )
}

/** 路由参数 → 会话视图。key=sessionId：切换会话时整组件重挂载，局部状态不串台 */
function SessionRoute({
  draftIdsRef,
}: {
  draftIdsRef: React.RefObject<Set<string>>
}) {
  const { sessionId } = useParams()
  if (!sessionId) return null
  return <SessionView key={sessionId} sessionId={sessionId} draftIdsRef={draftIdsRef} />
}

/** 单个会话的视图：详情回放 + 实时 turn + subtask 面板 */
function SessionView({
  sessionId,
  draftIdsRef,
}: {
  sessionId: string
  draftIdsRef: React.RefObject<Set<string>>
}) {
  const navigate = useNavigate()
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  // 右栏 subtask 面板：点击卡片打开（持有同一 work item 引用，流式更新可见）
  const [activeSubtask, setActiveSubtask] = useState<SubtaskItem | null>(null)
  // 执行状态（模式/计划标志）：初值来自服务端，turn 收口后刷新（完全访问/计划工具会改它）
  const [permission, setPermission] = useState<PermissionState>({ mode: 'build', plan_enabled: false })

  const refreshPermission = useCallback(() => {
    getPermissionState().then(setPermission).catch(() => {})
  }, [])

  // 挂载时拉一次执行状态
  useEffect(() => {
    refreshPermission()
  }, [refreshPermission])

  // SSE 流式状态（只在本会话上生效）；liveTurn 即实时轮次
  const {
    turn: liveTurn,
    isStreaming,
    error: streamError,
    send,
    regenerate,
    clearTurn,
    pendingApproval,
  } = useSessionStream(sessionId)

  // 挂载（含刷新/直链进入）：拉全量回放。
  // 404 分两种：本应用创建的草稿（还没有落盘内容，等首条消息）留在空对话态；
  // 其余（手输未知地址、已删除会话）回首页，不允许凭空"复活"会话 id
  useEffect(() => {
    let cancelled = false
    getSessionDetail(sessionId)
      .then((d) => {
        if (!cancelled) setDetail(d)
      })
      .catch((e) => {
        if (cancelled) return
        const knownDraft = e instanceof ApiError && e.status === 404
          && draftIdsRef.current?.has(sessionId)
        if (!knownDraft) navigate('/', { replace: true })
      })
    return () => {
      cancelled = true
    }
  }, [sessionId, navigate, draftIdsRef])

  /** turn 收口后：重新拉详情（耗时等切换为落盘权威值）并清掉实时轮次；同步执行状态 */
  const refreshAfterTurn = useCallback(async () => {
    try {
      setDetail(await getSessionDetail(sessionId))
    } catch {
      /* draft 会话首条消息失败时详情仍不存在，忽略 */
    }
    clearTurn()
    refreshPermission()
  }, [sessionId, clearTurn, refreshPermission])

  /** 发送消息：模式随提交生效；流结束后刷新详情与列表 */
  const handleSend = useCallback(
    (text: string, execution: ExecutionDraft) => {
      void send(text, () => {
        void refreshAfterTurn()
      }, execution)
    },
    [send, refreshAfterTurn],
  )

  /** 停止当前生成（后端在下一个检查点优雅收口） */
  const handleStop = useCallback(() => {
    void stopTurn(sessionId)
  }, [sessionId])

  /** 错误重试 = 重新生成最后一轮（保留用户消息重跑） */
  const handleRetry = useCallback(() => {
    void regenerate(() => {
      void refreshAfterTurn()
    })
  }, [regenerate, refreshAfterTurn])

  return (
    <>
      {/* 中栏：对话流 */}
      <ChatArea
        detail={detail}
        liveTurn={liveTurn}
        isStreaming={isStreaming}
        currentId={sessionId}
        streamError={streamError}
        livePendingApproval={pendingApproval}
        permission={permission}
        onSend={handleSend}
        onStop={handleStop}
        onRegenerate={handleRetry}
        onApprovalResolved={() => void refreshAfterTurn()}
        onOpenSubtask={setActiveSubtask}
      />

      {/* 右栏：subtask 查看面板（点击卡片才出现，实时跟随输出增长） */}
      {activeSubtask && (
        <SubtaskViewer subtask={activeSubtask} onClose={() => setActiveSubtask(null)} />
      )}
    </>
  )
}
