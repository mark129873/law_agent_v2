/**
 * 右侧 subtask 查看面板：显示子助手目标与实时/历史输出。
 *
 * 点击会话流中的 subtask 卡片打开；数据是卡片对应的 work item 对象
 * （useSessionStream 里流式 delta 会原地追加 output），运行中每 500ms
 * 强制重渲染以跟随增长；结束后保留可回看。
 */

import { useEffect, useState } from 'react'

import type { WorkItem } from '../types'

export type SubtaskItem = Extract<WorkItem, { kind: 'subtask' }>

export interface SubtaskViewerProps {
  subtask: SubtaskItem
  onClose: () => void
}

export function SubtaskViewer({ subtask, onClose }: SubtaskViewerProps) {
  const running = subtask.status === 'running'
  // 运行中轻量 tick：output 由流式事件原地追加，tick 强制读取最新值
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setTick((t) => t + 1), 500)
    return () => clearInterval(timer)
  }, [running])

  return (
    <aside className="flex w-96 shrink-0 flex-col border-l border-zinc-200 bg-white">
      {/* 头部：目标 + 状态 + 关闭 */}
      <div className="border-b border-zinc-200 px-4 py-3">
        <div className="flex items-start justify-between gap-2">
          <p className="text-[13px] font-medium leading-snug text-zinc-800">{subtask.goal}</p>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 text-xs text-zinc-400 transition hover:text-zinc-600"
            aria-label="关闭面板"
          >
            关闭
          </button>
        </div>
        <p
          className={`mt-1 text-[11px] ${
            running ? 'text-sky-600' : subtask.status === 'failed' ? 'text-red-600' : 'text-emerald-600'
          }`}
        >
          {running ? '执行中' : subtask.status === 'failed' ? '执行失败' : '已完成'}
        </p>
      </div>

      {/* 输出区：流式增量（历史卡片为最终输出全文） */}
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {subtask.output ? (
          <pre className="whitespace-pre-wrap break-words font-num text-[12px] leading-relaxed text-zinc-700">
            {subtask.output}
          </pre>
        ) : (
          <p className="text-[13px] text-zinc-400">等待子助手输出…</p>
        )}
      </div>
    </aside>
  )
}
