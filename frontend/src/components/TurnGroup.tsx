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
import { CaretRight, CheckCircle, CircleHalf, FolderOpen, SquaresFour, StopCircle, TerminalWindow, UsersThree, WarningCircle } from '@phosphor-icons/react'

import { fmtDuration } from '../utils/format'
import {
  ErrorCard,
  MarkdownWithCopy,
  TodoCard,
  ToolCallCard,
  UserBubble,
} from './MessageItem'
import type { SubtaskItem } from './SubtaskViewer'
import type { TurnData, WorkItem } from '../types'

export interface TurnGroupProps {
  turn: TurnData
  /** 点击 subtask 卡片时打开右侧面板（FE-5 联动） */
  onOpenSubtask?: (subtask: Extract<WorkItem, { kind: 'subtask' }>) => void
  /** 重新生成（保留用户消息重跑；FE-5 起在最终回复悬停出现） */
  onRegenerate?: () => void
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

export function TurnGroup({ turn, onOpenSubtask, onRegenerate }: TurnGroupProps) {
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
        : turn.state === 'failed'
          ? `已失败 ${fmtDuration(shownMs)}` // API 异常等失败轮：与"已停止"（用户主动）区分
          : `已停止${shownMs ? ` ${fmtDuration(shownMs)}` : ''}`

  return (
    <div className="animate-enter space-y-5">
      {/* 用户消息（块外、块头上方） */}
      {turn.user_message && <UserBubble text={turn.user_message.text} />}

      {/* 工作块：过程条目 ≥1 条时才渲染 */}
      {turn.work_items.length > 0 && (
        <div className="work-card overflow-hidden">
          {/* 块头：状态 + 耗时 + 折叠开关 */}
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className="flex w-full items-center gap-2.5 rounded-xl px-4 py-3 text-left transition hover:bg-zinc-100/60"
          >
            {running ? <CircleHalf size={17} className="text-accent-700" /> : turn.state === 'success' ? <CheckCircle size={17} className="text-accent-700" /> : turn.state === 'failed' ? <WarningCircle size={17} className="text-red-700" /> : <StopCircle size={17} className="text-amber-700" />}
            <span
              className={`text-[13px] font-medium ${
                running ? 'text-accent-700' : turn.state === 'success' ? 'text-zinc-700' : 'text-amber-700'
              }`}
            >
              {running && (
                <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent-500 align-middle motion-reduce:animate-none" />
              )}
              {headerText}
            </span>
            <span className="ml-auto text-[11px] text-zinc-500">{turn.work_items.length} 项记录</span>
            <CaretRight size={14} className={`text-zinc-500 transition ${open ? 'rotate-90' : ''}`} />
          </button>

          {/* 块内条目：按时间序平铺 */}
          {open && (
            <div className="space-y-3 border-t border-zinc-200/70 px-3 py-3">
              {aggregateItems(turn.work_items).map((entry) =>
                entry.kind === 'group' ? (
                  <GroupCard key={`g-${entry.groupType}-${entry.items[0]?.id}`} group={entry} />
                ) : (
                  <WorkItemRow
                    key={entry.kind + entry.id + entry.time}
                    item={entry}
                    onOpenSubtask={onOpenSubtask}
                    onRetry={onRegenerate}
                  />
                ),
              )}
            </div>
          )}
        </div>
      )}

      {/* 最终回复（块外完整渲染）；停止后保留的部分输出也在这里展示 */}
      {turn.final_text && (
        <div className="group px-1">
          <div className="mb-4 flex items-center gap-2 text-[12px] font-semibold text-zinc-600"><SquaresFour size={16} weight="fill" className="text-accent-700" />个人助手</div>
          <MarkdownWithCopy text={turn.final_text} streaming={running} onRegenerate={onRegenerate} />
          <div className="mt-1 flex items-center gap-2">
            {turn.state === 'stopped' && (
              <p className="text-[11px] text-amber-700">已停止，以上为已生成的部分</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/** 聚合组：连续只读工具 ≥2 → "探索"；连续 bash ≥2 → "执行"（ZCode 桌面端同款） */
interface WorkGroup {
  kind: 'group'
  groupType: 'explore' | 'execute'
  items: Extract<WorkItem, { kind: 'tool_call' }>[]
}

function isReadTool(name: string): boolean {
  return name === 'read_file' || name === 'glob'
}

function aggregateItems(items: WorkItem[]): (WorkItem | WorkGroup)[] {
  const out: (WorkItem | WorkGroup)[] = []
  let i = 0
  while (i < items.length) {
    const it = items[i]
    if (it.kind === 'tool_call') {
      const readRun = isReadTool(it.name)
      const bashRun = it.name === 'bash'
      if (readRun || bashRun) {
        let j = i + 1
        while (j < items.length) {
          const n = items[j]
          if (n.kind !== 'tool_call') break
          if (readRun && isReadTool(n.name)) j += 1
          else if (bashRun && n.name === 'bash') j += 1
          else break
        }
        if (j - i >= 2) {
          out.push({
            kind: 'group',
            groupType: readRun ? 'explore' : 'execute',
            items: items.slice(i, j) as Extract<WorkItem, { kind: 'tool_call' }>[],
          })
          i = j
          continue
        }
      }
    }
    out.push(it)
    i += 1
  }
  return out
}

/** 聚合组卡片：父行表达当前阶段状态，展开看子工具卡 */
function GroupCard({ group }: { group: WorkGroup }) {
  const [open, setOpen] = useState(false)
  const running = group.items.some((x) => x.status === 'running' || x.status === 'pending')
  const failed = group.items.some((x) => x.status === 'failed')
  const label = group.groupType === 'explore' ? (running ? '探索中' : '探索') : running ? '执行中' : '执行'
  return (
    <div className="overflow-hidden rounded-xl border border-zinc-200/80 bg-white">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2.5 px-3 py-3 text-left hover:bg-zinc-50"
        aria-expanded={open}
      >
        {group.groupType === 'explore' ? <FolderOpen size={17} className="text-zinc-500" /> : <TerminalWindow size={17} className="text-zinc-500" />}
        <span className={`text-[13px] font-medium ${running ? 'text-accent-700' : 'text-zinc-700'}`}>{label}</span>
        <span className="flex-1 text-[12px] text-zinc-500">{group.items.length} 项操作</span>
        <CaretRight size={14} className={`text-zinc-500 transition ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <div className="space-y-2 border-t border-zinc-100 px-3 py-2">
          {group.items.map((item) => (
            <ToolCallCard key={item.id} item={item} />
          ))}
        </div>
      )}
      {failed && <p className="px-3 pb-2 text-[11px] text-red-600">· 有失败项</p>}
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
  onOpenSubtask?: (s: SubtaskItem) => void
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
            <p
              className={`text-[13px] ${
                item.status === 'auto'
                  ? 'text-accent-700'
                  : item.status === 'approved'
                    ? 'text-accent-700'
                    : 'text-amber-800'
              }`}
            >
              审批：<span className="font-mono">{item.tool}</span> · {item.reason}
            </p>
            <span
              className={`shrink-0 text-[11px] ${
                item.status === 'auto'
                  ? 'text-accent-700'
                  : item.status === 'approved'
                    ? 'text-accent-700'
                    : item.status === 'denied'
                      ? 'text-red-600'
                      : 'text-amber-700'
              }`}
            >
              {item.status === 'auto'
                ? '自动放行'
                : item.status === 'approved'
                  ? '已批准'
                  : item.status === 'denied'
                    ? '已拒绝'
                    : '等待批准'}
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
          onClick={() => onOpenSubtask?.(item)}
          className="flex w-full items-center gap-2.5 rounded-xl border border-zinc-200/80 bg-white px-3 py-3 text-left transition hover:bg-zinc-50"
        >
          <UsersThree size={17} className="shrink-0 text-accent-700" />
          <span className="text-[12px] font-medium text-accent-700">子助手</span>
          <span className="min-w-0 flex-1 truncate text-[13px] text-zinc-700">{item.goal}</span>
          <span
            className={`shrink-0 text-[11px] ${
              item.status === 'running' ? 'text-accent-700' : item.status === 'failed' ? 'text-red-600' : 'text-accent-700'
            }`}
          >
            {item.status === 'running' ? '执行中' : item.status === 'failed' ? '执行失败' : item.status === 'stopped' ? '已停止' : '已完成'}
          </span>
        </button>
      )
    default:
      return null
  }
}
