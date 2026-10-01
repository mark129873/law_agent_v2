/**
 * Markdown 渲染（独立分包）：react-markdown + rehype-highlight 是前端体积大头，
 * 单独放一个模块并经 MessageItem 的 lazy 包装按需加载——首屏主包不引入
 * highlight 的全部语法库（docs/ARCHITECTURE.md §7 代码分包）。
 */

import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'

/** 最终回复 / 中间文本：Markdown 渲染 + 悬停复制按钮（default 导出供 lazy 引用） */
export default function MarkdownWithCopy({
  text,
  streaming = false,
}: {
  text: string
  streaming?: boolean
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
    <div className="group relative">
      {/* 正文：markdown 渲染；生成中尾部加光标 */}
      <div className={`text-[15px] leading-relaxed ${streaming ? 'stream-cursor' : ''}`}>
        <ReactMarkdown rehypePlugins={[rehypeHighlight]}>{text}</ReactMarkdown>
      </div>
      {/* 复制按钮：悬停出现 */}
      <button
        type="button"
        onClick={handleCopy}
        className="absolute -top-1 right-0 hidden rounded border border-zinc-200 bg-white px-1.5 py-0.5 text-[11px] text-zinc-500 transition hover:text-zinc-800 group-hover:block"
        aria-label="复制"
      >
        {copied ? '已复制' : '复制'}
      </button>
    </div>
  )
}
