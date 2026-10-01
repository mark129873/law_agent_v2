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
import { ApiError, createSession, getSessionDetail, stopTurn } from './api/client'
import { ShellContext, useShell } from './ShellContext'
import { useSessionStream } from './hooks/useSessionStream'
import { SidebarSimple } from '@phosphor-icons/react'
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
      return localStorage.getItem(SIDEBAR_STORAGE_KEY) !== 'collapsed'
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

  /** 导航进入指定会话（选中列表项与新建草稿共用同一动作） */
  const openSession = useCallback(
    (id: string) => navigate(`/session/${id}`),
    [navigate],
  )

  /** 新建会话：拿 draft id 后进入会话视图（首条消息发出才落库） */
  const handleNewSession = useCallback(async () => {
    const { id } = await createSession()
    draftIdsRef.current.add(id)
    openSession(id)
  }, [openSession])

  return (
    /* 三栏布局的 flex 容器：侧栏与路由出口（中/右栏）必须并排，
       不能换成 Fragment——容器一旦消失，侧栏与主区会垂直堆叠（实测踩过） */
    <ShellContext.Provider value={{ sidebarOpen, toggleSidebar }}>
      <div className="flex h-full">
        {/* 左栏：会话列表 */}
        <Sidebar
          sessions={sessions}
          currentId={currentId}
          onSelect={openSession}
          onSessionsChange={setSessions}
          onDraftCreated={openSession}
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
function WelcomePage({ onNewSession }: { onNewSession: () => void }) {
  const { sidebarOpen, toggleSidebar } = useShell()
  return (
    <main className="relative flex min-w-0 flex-1 flex-col items-center justify-center gap-3 bg-zinc-50">
      {/* 左栏开合按钮：常驻左上角（侧栏收起后仍可找回） */}
      <div className="absolute left-3 top-3">
        <SidebarToggle sidebarOpen={sidebarOpen} onToggle={toggleSidebar} />
      </div>
      <h1 className="text-2xl font-semibold tracking-tight text-zinc-800">个人助手</h1>
      <p className="max-w-[28rem] text-center text-sm leading-relaxed text-zinc-500">
        能读文件、跑命令、管任务、派子助手的本地工作伙伴。左侧新建一个会话，开始对话。
      </p>
      <button
        type="button"
        onClick={onNewSession}
        className="rounded-xl bg-zinc-900 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-zinc-700 active:translate-y-px"
      >
        新建会话
      </button>
    </main>
  )
}

/** 左栏开合图标按钮（Phosphor SidebarSimple，两态高亮区分） */
function SidebarToggle({ sidebarOpen, onToggle }: { sidebarOpen: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`rounded-md p-1.5 transition hover:bg-zinc-100 ${
        sidebarOpen ? 'text-zinc-700' : 'text-zinc-400'
      }`}
      aria-label={sidebarOpen ? '收起侧栏' : '展开侧栏'}
      aria-pressed={sidebarOpen}
      title={sidebarOpen ? '收起侧栏' : '展开侧栏'}
    >
      <SidebarSimple size={16} weight="regular" />
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

  /** turn 收口后：重新拉详情（耗时等切换为落盘权威值）并清掉实时轮次 */
  const refreshAfterTurn = useCallback(async () => {
    try {
      setDetail(await getSessionDetail(sessionId))
    } catch {
      /* draft 会话首条消息失败时详情仍不存在，忽略 */
    }
    clearTurn()
  }, [sessionId, clearTurn])

  /** 发送消息：流结束后刷新详情与列表 */
  const handleSend = useCallback(
    (text: string) => {
      void send(text, () => {
        void refreshAfterTurn()
      })
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
