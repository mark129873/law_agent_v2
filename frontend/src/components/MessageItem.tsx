/**
 * 消息渲染：Markdown + 代码高亮 + 悬停复制。
 * 用于：工作块外的最终回复、中间过程文本、用户气泡（纯文本变体）。
 *
 * 分包说明：react-markdown + rehype-highlight 体积大，真实实现移到
 * Markdown.tsx，这里用 lazy+Suspense 包装转发——引用方（TurnGroup）
 * 无感知，首次渲染 markdown 时才加载异步分包。
 */

import { Suspense, lazy, useState } from 'react'
import { CaretRight, CheckCircle, Circle, CircleHalf, FileText, ListChecks, TerminalWindow } from '@phosphor-icons/react'

import type { TodoItem, WorkItem } from '../types'

// 懒加载 markdown 分包（default 导出 = MarkdownWithCopy）
const LazyMarkdown = lazy(() => import('./Markdown'))

/** 悬停复制的 Markdown 渲染：异步分包的转发壳（props 与原实现一致） */
export function MarkdownWithCopy({ text, streaming = false, onRegenerate }: { text: string; streaming?: boolean; onRegenerate?: () => void }) {
  return (
    /* fallback 用等高的占位行，避免流式首帧高度跳动 */
    <Suspense fallback={<div role="status" className="text-[13px] leading-relaxed text-zinc-500">正在加载正文…</div>}>
      <LazyMarkdown text={text} streaming={streaming} onRegenerate={onRegenerate} />
    </Suspense>
  )
}

// 状态词映射（产品文档 §3.3）
const TOOL_STATUS_WORD: Record<string, string> = {
  pending: '等待中',
  running: '执行中',
  completed: '已执行',
  failed: '执行失败',
  denied: '已拒绝',
  stopped: '已停止',
}

/** 工具输入摘要：一行内的参数预览 */
function inputSummary(input: unknown): string {
  if (input == null) return ''
  if (typeof input === 'string') return input
  try {
    // JSON.stringify 原生保留中文，不需要额外的逐字段转换函数。
    return JSON.stringify(input)
  } catch {
    return String(input)
  }
}

/** 用户消息气泡：右对齐浅灰底纯文本 */
export function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[88%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md border border-accent-100/70 bg-accent-50 px-5 py-3.5 text-[15px] leading-7 text-zinc-800">
        {text}
      </div>
    </div>
  )
}

/** 任务板卡片：[ ] 待办 / [>] 进行中 / [x] 已完成（实时覆盖刷新） */
export function TodoCard({ items }: { items: TodoItem[] }) {
  const icons = { pending: Circle, in_progress: CircleHalf, completed: CheckCircle }
  const words = { pending: '待办', in_progress: '进行中', completed: '已完成' }
  const color = {
    pending: 'text-zinc-500',
    in_progress: 'text-accent-700',
    completed: 'text-accent-700',
  } as const
  return (
    <div className="rounded-xl border border-zinc-200/80 bg-white px-4 py-3">
      <p className="mb-3 flex items-center gap-2 text-[12px] font-semibold text-zinc-600"><ListChecks size={16} />任务板<span className="font-num ml-auto font-normal">{items.filter(x => x.status === 'completed').length}/{items.length}</span></p>
      <ul className="space-y-2.5">
        {items.map((it, i) => {
          const Icon = icons[it.status]
          return <li key={i} className="flex items-start gap-2.5 text-[13px] leading-5">
            <Icon size={17} className={`mt-0.5 shrink-0 ${color[it.status]}`} aria-label={words[it.status]} />
            <span className={it.status === 'completed' ? 'text-zinc-500 line-through' : 'text-zinc-700'}>
              {it.content}
            </span>
          </li>
        })}
      </ul>
    </div>
  )
}

/** 错误卡片：红色内联 + 重试按钮（onRetry 由上层接 /regenerate，FE-4 接线） */
export function ErrorCard({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2">
      <p className="text-[13px] text-red-700">出错了：{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-1.5 rounded border border-red-300 px-2 py-0.5 text-xs text-red-700 transition hover:bg-red-100"
        >
          重试
        </button>
      )}
    </div>
  )
}

/** 工具调用卡片：二级折叠（默认收起），头部=名称+状态词，展开=参数与输出 */
export function ToolCallCard({
  item,
}: {
  item: Extract<WorkItem, { kind: 'tool_call' }>
}) {
  const [open, setOpen] = useState(false)
  const running = item.status === 'running' || item.status === 'pending'
  const statusWord = TOOL_STATUS_WORD[item.status] ?? item.status

  return (
    <div className="overflow-hidden rounded-xl border border-zinc-200/80 bg-white">
      {/* 头部：名称 + 输入摘要 + 状态词；点击展开/收起 */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2.5 px-3 py-3 text-left hover:bg-zinc-50"
        aria-expanded={open}
      >
        {item.name === 'bash' ? <TerminalWindow size={17} className="shrink-0 text-zinc-500" /> : <FileText size={17} className="shrink-0 text-zinc-500" />}
        <span className={`font-mono text-[12px] font-medium ${running ? 'text-accent-700' : 'text-zinc-700'}`}>
          {item.name}
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px] text-zinc-500">
          {inputSummary(item.input)}
        </span>
        <span
          className={`shrink-0 text-[11px] ${
            item.status === 'failed'
              ? 'text-red-600'
              : item.status === 'denied'
                ? 'text-amber-700'
                : running
                  ? 'text-accent-700'
                  : 'text-accent-700'
          }`}
        >
          {statusWord}
        </span>
        <CaretRight size={14} className={`shrink-0 text-zinc-500 transition ${open ? 'rotate-90' : ''}`} />
      </button>
      {/* 展开区：参数与输出 */}
      {open && (
        <div className="border-t border-zinc-100 px-3 py-2">
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-zinc-50 p-2 text-[12px] text-zinc-600">
            {JSON.stringify(item.input ?? {}, null, 2)}
          </pre>
          {item.output && (
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-zinc-50 p-3 text-[12px] leading-6 text-zinc-700">
              {item.output}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
