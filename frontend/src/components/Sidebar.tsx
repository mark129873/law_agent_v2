/**
 * 会话导航：列表由后端事实驱动，每 5 秒刷新；新建仍只生成草稿 id。
 * 选中按钮与删除按钮是同级元素，避免嵌套按钮让键盘、读屏和点击行为混乱。
 */
import { ChatTeardrop, HardDrives, Plus, SidebarSimple, SquaresFour, Trash } from '@phosphor-icons/react'
import { useCallback, useEffect, useState } from 'react'
import { createSession, deleteSession, listSessions } from '../api/client'
import { useShell } from '../ShellContext'
import type { SessionItem } from '../types'

export interface SidebarProps {
  sessions: SessionItem[]
  currentId: string | null
  onSelect: (id: string) => void
  onSessionsChange: (items: SessionItem[]) => void
  onDraftCreated: (id: string) => void
}

/** 今天显示时分，较早的会话显示月日，避免每一行都堆完整日期。 */
function formatTime(ms: number): string {
  const date = new Date(ms)
  return date.toDateString() === new Date().toDateString()
    ? `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
    : `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export function Sidebar({ sessions, currentId, onSelect, onSessionsChange, onDraftCreated }: SidebarProps) {
  const { sidebarOpen, toggleSidebar } = useShell()
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const refresh = useCallback(async () => {
    try {
      onSessionsChange(await listSessions())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : '会话列表暂时无法加载')
    } finally { setLoading(false) }
  }, [onSessionsChange])

  useEffect(() => {
    // 首次读取也异步触发；effect 只管理调度，卸载时一起清理，避免同步派发界面状态。
    const first = setTimeout(() => void refresh(), 0)
    const timer = setInterval(() => void refresh(), 5000)
    return () => { clearTimeout(first); clearInterval(timer) }
  }, [refresh])

  /** 抽屉里选完会话就露出正文；桌面仍保持原来的三栏结构。 */
  const closeDrawer = () => {
    if (window.matchMedia('(max-width: 760px)').matches && sidebarOpen) toggleSidebar()
  }
  const handleNew = async () => {
    if (creating) return
    setCreating(true)
    setError(null)
    try {
      const { id } = await createSession()
      onDraftCreated(id)
      closeDrawer()
    } catch (e) { setError(e instanceof Error ? e.message : '新建会话失败，请重试') }
    finally { setCreating(false) }
  }
  const handleDelete = async (id: string) => {
    try {
      await deleteSession(id)
      setConfirmingId(null)
      await refresh()
    } catch (e) {
      // 删除失败时保留列表与确认入口，明确显示原因，用户可以稍后重试。
      setError(e instanceof Error ? e.message : '删除失败，请重试')
    }
  }

  return (
    <>
      {sidebarOpen && <button className="sidebar-scrim" aria-label="关闭会话导航" onClick={toggleSidebar} />}
      <aside aria-label="会话导航" aria-hidden={!sidebarOpen} className={`sidebar-panel shrink-0 overflow-hidden ${sidebarOpen ? 'w-64 border-r border-zinc-200/70' : 'hidden'}`}>
        <div className="flex h-full w-64 flex-col">
          <div className="flex h-[68px] shrink-0 items-center gap-3 px-5">
            <span className="brand-mark"><SquaresFour size={20} weight="fill" /></span>
            <span className="flex-1 text-[15px] font-semibold tracking-tight text-zinc-900">个人助手</span>
            <button type="button" onClick={toggleSidebar} className="icon-button" aria-label="收起侧栏" title="收起侧栏"><SidebarSimple size={18} /></button>
          </div>
          <div className="px-4 pb-6 pt-3">
            <button type="button" onClick={() => void handleNew()} disabled={creating} className="primary-button w-full">
              <Plus size={17} weight="bold" /> {creating ? '正在新建…' : '新建会话'}
            </button>
          </div>
          <div className="flex items-center justify-between px-6 pb-3 text-[12px] font-medium text-zinc-600">
            <span>最近会话</span>{!loading && !error && <span className="font-num">{sessions.length}</span>}
          </div>
          {error && <div role="alert" className="mx-4 mb-3 rounded-lg bg-red-50 p-3 text-[12px] leading-5 text-red-700"><p>{error}</p><button type="button" onClick={() => void refresh()} className="mt-1 rounded px-1 py-0.5 font-medium underline underline-offset-2">重试加载</button></div>}
          <nav className="min-h-0 flex-1 overflow-y-auto px-3 pb-4" aria-label="会话列表">
            {sessions.length === 0 && !error && <p role="status" className="px-3 py-8 text-center text-[13px] leading-6 text-zinc-600">{loading ? '正在加载会话…' : '还没有会话，开始新的工作吧。'}</p>}
            <ul className="space-y-1.5">
              {sessions.map((s) => (
                <li key={s.id} className={`session-row group relative ${s.id === currentId ? 'is-selected' : ''}`}>
                  <button type="button" onClick={() => { onSelect(s.id); closeDrawer() }} aria-current={s.id === currentId ? 'page' : undefined} className="flex w-full items-start gap-2.5 rounded-xl px-3 pb-3 pt-3 text-left">
                    <ChatTeardrop size={17} className={`mt-0.5 shrink-0 ${s.id === currentId ? 'text-accent-700' : 'text-zinc-500'}`} />
                    <span className="min-w-0 flex-1">
                      <span title={s.title} className={`block truncate pr-4 text-[13px] leading-5 ${s.id === currentId ? 'font-semibold text-zinc-900' : 'text-zinc-700'}`}>{s.title || '新会话'}</span>
                      <span className="mt-1.5 flex items-center gap-2 text-[11px] text-zinc-600">
                        <span className="font-num">{formatTime(s.updated_at)}</span>
                        {s.tokens_used > 0 && <span className="font-num">{s.tokens_used.toLocaleString()} tok</span>}
                        {s.running && <span className="flex items-center gap-1.5 text-accent-700"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-600 motion-reduce:animate-none" />生成中</span>}
                      </span>
                    </span>
                  </button>
                  {/* 删除独立于导航按钮，仍需第二次明确确认；不把 hover 当作唯一入口。 */}
                  {confirmingId === s.id ? (
                    <div className="flex items-center justify-end gap-2 px-3 pb-3">
                      <button type="button" onClick={() => setConfirmingId(null)} className="rounded-lg px-2.5 py-1.5 text-[12px] text-zinc-600 hover:bg-zinc-200/60">取消</button>
                      <button type="button" onClick={() => void handleDelete(s.id)} className="rounded-lg bg-red-600 px-2.5 py-1.5 text-[12px] font-medium text-white hover:bg-red-700">确认删除</button>
                    </div>
                  ) : (
                    <button type="button" onClick={() => setConfirmingId(s.id)} aria-label={`删除会话 ${s.title}`} title="删除会话" className="session-actions absolute right-1.5 top-2.5 flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--workspace-rail)] text-zinc-500 hover:bg-red-50 hover:text-red-700"><Trash size={14} /></button>
                  )}
                </li>
              ))}
            </ul>
          </nav>
          <div className="mx-5 flex shrink-0 items-center gap-2.5 border-t border-zinc-200/80 py-5 text-[12px] text-zinc-600"><HardDrives size={17} />会话保存在本机</div>
        </div>
      </aside>
    </>
  )
}
