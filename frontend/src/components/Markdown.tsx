/**
 * Markdown 渲染（独立分包）：react-markdown + rehype-highlight 是前端体积大头，
 * 单独放一个模块并经 MessageItem 的 lazy 包装按需加载——首屏主包不引入
 * highlight 的全部语法库（docs/ARCHITECTURE.md §7 代码分包）。
 */

import { useState } from 'react'
import { ArrowClockwise, Check, Copy } from '@phosphor-icons/react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

/** 最终回复 / 中间文本：Markdown 渲染 + 悬停复制按钮（default 导出供 lazy 引用） */
export default function MarkdownWithCopy({
  text,
  streaming = false,
  onRegenerate,
}: {
  text: string
  streaming?: boolean
  onRegenerate?: () => void
}) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      /* 剪贴板不可用时静默 */
    }
  }

  return (
    <div className="group relative min-w-0">
      {/* 正文：markdown 渲染；生成中尾部加光标 */}
      <div className={`markdown-body text-[15px] ${streaming ? 'stream-cursor' : ''}`}>
        {/* GFM 负责表格与任务列表；横向滚动容器防止宽表格撑破对话区。 */}
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={{
          table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
        }}>{text}</ReactMarkdown>
      </div>
      {/* 正文操作放在同一行；键盘聚焦时同样可见，生成中不提供重跑。 */}
      <div className="mt-3 flex items-center gap-1 opacity-100 transition focus-within:opacity-100 sm:opacity-0 sm:group-hover:opacity-100">
      <button
        type="button"
        onClick={handleCopy}
        className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] text-zinc-500 transition hover:bg-zinc-100 hover:text-zinc-800"
        aria-label={copied ? '已复制' : '复制回复'}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}{copied ? '已复制' : '复制'}
      </button>
      {!streaming && onRegenerate && <button type="button" onClick={onRegenerate} className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] text-zinc-500 transition hover:bg-zinc-100 hover:text-zinc-800"><ArrowClockwise size={14} />重新生成</button>}
      </div>
    </div>
  )
}
