/**
 * 侧栏：会话列表 + 新建 + 删除（确认）+ 生成中呼吸点。
 *
 * 数据流：
 * - 列表由本组件拉取（挂载时一次 + 每 5 秒轮询：呼吸点与 token 数轻量刷新），
 *   通过 onSessionsChange 上报给 App 统一持有；
 * - 点击列表项 → onSelect(id)（App 拉取详情，即 resume）；
 * - 新建 → createSession（draft，不落库）→ onDraftCreated(id)（App 进入空对话态）；
 * - 删除 → 两步确认（先点删除，再点确认）→ deleteSession → 刷新列表。
 */

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

/** 时间显示：今天的会话显示 HH:mm，更早的显示 MM-DD */
function formatTime(ms: number): string {
  const date = new Date(ms)
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  const hhmm = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  return sameDay
    ? hhmm
    : `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

/** 生成中呼吸点：外圈 ping 扩散 + 内圈实心（尊重 prefers-reduced-motion） */
function BreathingDot() {
  return (
    <span className="relative flex h-2 w-2 shrink-0">
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400 opacity-75 motion-reduce:animate-none" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-500" />
    </span>
  )
}

export function Sidebar(props: SidebarProps) {
  const { sessions, currentId, onSelect, onSessionsChange, onDraftCreated } = props
  const { sidebarOpen } = useShell()
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const refresh = useCallback(async () => {
    try {
      onSessionsChange(await listSessions())
    } catch {
      // 后端未启动等场景：侧栏静默保持现状，不打断使用
    }
  }, [onSessionsChange])

  // 挂载时拉一次 + 每 5 秒轮询（呼吸点/时间/token 轻量更新）
  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => clearInterval(timer)
  }, [refresh])

  const handleNew = useCallback(async () => {
    if (creating) return
    setCreating(true)
    try {
      const { id } = await createSession()
      onDraftCreated(id)
      // draft 不在列表里，无需刷新；标题等首条消息后再出现
    } finally {
      setCreating(false)
    }
  }, [creating, onDraftCreated])

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await deleteSession(id)
      } catch {
        // 409（生成中）等失败：不弹系统错误，留痕下一次轮询自然恢复
      }
      setConfirmingId(null)
      refresh()
    },
    [refresh],
  )

  return (
    /* 收起态：宽度归零并去边框（瞬时切换，不做 width 动画）；
       内层固定 w-64，避免收起过程中内容被挤压变形 */
    <aside
      className={`shrink-0 overflow-hidden bg-white ${
        sidebarOpen ? 'w-64 border-r border-zinc-200' : 'w-0'
      }`}
    >
      <div className="flex h-full w-64 flex-col">
        {/* 新建按钮（主操作：近黑） */}
        <div className="p-3">
          <button
            type="button"
            onClick={handleNew}
            disabled={creating}
            className="w-full rounded-lg bg-zinc-900 px-3 py-2 text-sm font-medium text-white transition hover:bg-zinc-700 active:translate-y-px disabled:opacity-50"
          >
            新建会话
          </button>
        </div>

      {/* 会话列表 */}
      <nav className="flex-1 overflow-y-auto px-2 pb-3" aria-label="会话列表">
        {sessions.length === 0 && (
          <p className="px-3 py-6 text-center text-xs leading-relaxed text-zinc-400">
            还没有会话
            <br />
            点击"新建会话"开始
          </p>
        )}
        <ul className="space-y-0.5">
          {sessions.map((s) => {
            const selected = s.id === currentId
            return (
              <li key={s.id} className="relative">
                <button
                  type="button"
                  onClick={() => onSelect(s.id)}
                  className={`w-full rounded-lg px-3 py-2 text-left transition ${
                    selected ? 'bg-zinc-100' : 'hover:bg-zinc-50'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    {/* 生成中呼吸点（切走后仍可见后台进度） */}
                    {s.running ? <BreathingDot /> : null}
                    <span
                      className={`min-w-0 flex-1 truncate text-sm ${
                        selected ? 'font-medium text-zinc-900' : 'text-zinc-700'
                      }`}
                      title={s.title}
                    >
                      {s.title || '新会话'}
                    </span>
                    <span className="font-num shrink-0 text-[11px] text-zinc-400">
                      {formatTime(s.updated_at)}
                    </span>
                  </div>
                  <div className="mt-0.5 flex items-center justify-between pl-4">
                    <span className="font-num text-[11px] text-zinc-400">
                      {s.tokens_used > 0 ? `${s.tokens_used} tok` : ''}
                    </span>
                    {/* 删除：两步确认 */}
                    {confirmingId === s.id ? (
                      <span className="flex gap-1.5 text-[11px]">
                        <button
                          type="button"
                          className="rounded bg-red-600 px-1.5 py-0.5 text-white hover:bg-red-500"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleDelete(s.id)
                          }}
                        >
                          确认删除
                        </button>
                        <button
                          type="button"
                          className="rounded px-1.5 py-0.5 text-zinc-500 hover:bg-zinc-100"
                          onClick={(e) => {
                            e.stopPropagation()
                            setConfirmingId(null)
                          }}
                        >
                          取消
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="text-[11px] text-zinc-300 transition hover:text-red-500"
                        onClick={(e) => {
                          e.stopPropagation()
                          setConfirmingId(s.id)
                        }}
                        aria-label={`删除会话 ${s.title}`}
                      >
                        删除
                      </button>
                    )}
                  </div>
                </button>
              </li>
            )
          })}
        </ul>
      </nav>
      </div>
    </aside>
  )
}
