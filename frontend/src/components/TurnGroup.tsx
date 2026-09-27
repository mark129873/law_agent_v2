/**
 * TurnGroup：一轮对话的展示 = 用户气泡 + 工作块（可折叠）+ 块外最终回复。
 *
 * 块头状态机（ZCode 桌面端同款，docs/PRODUCT.md §3.2）：
 * - running：  "工作中 {耗时}"（每秒跳动的本地时钟，仅 running 态允许用当前时钟）
 * - success：  "已工作 {耗时}"（取落盘的 active_ms 权威值，完成后自动收起）
 * - stopped/failed："已停止 {耗时}"（强制展开，回看上下文）
 * 归属切分：每轮最后一条 assistant 正文（final_text）在块外完整渲染，
 * 其余过程条目全部收在块内。
 */

import { useEffect, useRef, useState } from 'react'

import { fmtDuration } from '../utils/format'
import {
  ErrorCard,
  MarkdownWithCopy,
  TodoCard,
  ToolCallCard,
  UserBubble,
} from './MessageItem'
import type { TurnData, WorkItem } from '../types'

export interface TurnGroupProps {
  turn: TurnData
  /** 点击 subtask 卡片时打开右侧面板（FE-5 联动） */
  onOpenSubtask?: (subtask: { id: string; goal: string }) => void
  /** 错误重试（FE-4 接 /regenerate） */
  onRetry?: () => void
}

/** 运行中的本地秒表：仅 running 态每秒跳，其余状态返回 0（不用当前时钟） */
function useLiveElapsed(startedAt: number, running: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running, startedAt])
  return running ? Math.max(0, now - startedAt) : 0
}

export function TurnGroup({ turn, onOpenSubtask, onRetry }: TurnGroupProps) {
  const running = turn.state === 'running'
  const liveMs = useLiveElapsed(turn.started_at, running)
  // 展示耗时：运行中用本地秒表；结束态用落盘的权威工时（不随时间增长）
  const shownMs = running ? liveMs : turn.active_ms

  // 默认展开规则：运行中展开；完成收起；停止/失败强制展开
  const defaultOpen = running || turn.state === 'stopped' || turn.state === 'failed'
  const [open, setOpen] = useState(defaultOpen)
  const prevDefaultOpen = useRef(defaultOpen)

  // 状态翻转（如 running→success）时跟随默认值：完成瞬间自动收起
  useEffect(() => {
    if (prevDefaultOpen.current !== defaultOpen) {
      prevDefaultOpen.current = defaultOpen
      setOpen(defaultOpen)
    }
  }, [defaultOpen])

  const headerText =
    turn.state === 'running'
      ? `工作中 ${fmtDuration(shownMs)}`
      : turn.state === 'success'
        ? `已工作 ${fmtDuration(shownMs)}`
        : `已停止${shownMs ? ` ${fmtDuration(shownMs)}` : ''}`

  return (
    <div className="space-y-2">
      {/* 用户消息（块外、块头上方） */}
      {turn.user_message && <UserBubble text={turn.user_message.text} />}

      {/* 工作块：过程条目 ≥1 条时才渲染 */}
      {turn.work_items.length > 0 && (
        <div className="rounded-xl border border-zinc-200 bg-white">
          {/* 块头：状态 + 耗时 + 折叠开关 */}
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left"
          >
            <span
              className={`text-[13px] font-medium ${
                running ? 'text-sky-600' : turn.state === 'success' ? 'text-zinc-700' : 'text-amber-600'
              }`}
            >
              {running && (
                <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500 align-middle motion-reduce:animate-none" />
              )}
              {headerText}
            </span>
            <span className={`ml-auto text-zinc-400 transition ${open ? 'rotate-90' : ''}`}>›</span>
          </button>

          {/* 块内条目：按时间序平铺 */}
          {open && (
            <div className="space-y-2 border-t border-zinc-100 px-3.5 py-3">
              {turn.work_items.map((item) => (
                <WorkItemRow
                  key={item.kind + item.id + item.time}
                  item={item}
                  onOpenSubtask={onOpenSubtask}
                  onRetry={onRetry}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* 最终回复（块外完整渲染）；停止后保留的部分输出也在这里展示 */}
      {turn.final_text && (
        <div className="px-1">
          <MarkdownWithCopy text={turn.final_text} streaming={running} />
          {turn.state === 'stopped' && (
            <p className="mt-1 text-[11px] text-amber-600">已停止，以上为已生成的部分</p>
          )}
        </div>
      )}
    </div>
  )
}

/** 块内条目分发渲染 */
function WorkItemRow({
  item,
  onOpenSubtask,
  onRetry,
}: {
  item: WorkItem
  onOpenSubtask?: (s: { id: string; goal: string }) => void
  onRetry?: () => void
}) {
  switch (item.kind) {
    case 'tool_call':
      return <ToolCallCard item={item} />
    case 'todo':
      return <TodoCard items={item.items} />
    case 'error':
      return <ErrorCard message={item.message} onRetry={onRetry} />
    case 'text':
      // 过程文本（"边想边做"）：弱化样式
      return (
        <div className="px-1 text-[13px] leading-relaxed text-zinc-500">
          <MarkdownWithCopy text={item.text} />
        </div>
      )
    case 'approval':
      return (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
          <div className="flex items-center justify-between">
            <p className="text-[13px] text-amber-800">
              审批：<span className="font-mono">{item.tool}</span> — {item.reason}
            </p>
            <span
              className={`text-[11px] ${
                item.status === 'approved'
                  ? 'text-emerald-600'
                  : item.status === 'denied'
                    ? 'text-red-600'
                    : 'text-amber-600'
              }`}
            >
              {item.status === 'approved' ? '已批准' : item.status === 'denied' ? '已拒绝' : '等待批准'}
            </span>
          </div>
        </div>
      )
    case 'compaction':
      return (
        <div className="rounded-lg bg-zinc-50 px-3 py-1.5 text-[12px] text-zinc-500">
          已压缩上下文
          {item.tokens_before != null && item.tokens_after != null
            ? `（${item.tokens_before} → ${item.tokens_after} tokens）`
            : ''}
        </div>
      )
    case 'subtask':
      return (
        <button
          type="button"
          onClick={() => onOpenSubtask?.({ id: item.id, goal: item.goal })}
          className="flex w-full items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 py-2 text-left transition hover:bg-zinc-50"
        >
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-white">SubAgent</span>
          <span className="min-w-0 flex-1 truncate text-[13px] text-zinc-700">{item.goal}</span>
          <span
            className={`shrink-0 text-[11px] ${
              item.status === 'running' ? 'text-sky-600' : item.status === 'failed' ? 'text-red-600' : 'text-emerald-600'
            }`}
          >
            {item.status === 'running' ? '执行中' : item.status === 'failed' ? '执行失败' : '已完成'}
          </span>
        </button>
      )
    default:
      return null
  }
}
