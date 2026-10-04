/**
 * useSessionStream：把 SSE 事件流并成"实时 turn"状态。
 *
 * 设计（对应 ZCode 桌面端的工作块归属）：
 * - 实时 turn 与回放 turn 用同一种数据形状（TurnData），渲染组件完全复用；
 * - delta 文本累积为 final_text 的"生成中草稿"（最后一条 assistant 正文在块外）；
 * - 工具/审批/任务板/子助手事件按序追加进 work_items（块内条目）；
 * - 事件里出现 turn_completed 后，调用方应重新拉取会话详情，
 *   以落盘事实为准替换实时 turn（耗时等切换为权威值）。
 */

import { useCallback, useRef, useState } from 'react'

import { streamRegenerate, streamTurn, type ExecutionDraft } from '../api/client'
import type { SseEvent, ToolStatus, TurnData, WorkItem } from '../types'

export interface LiveTurnState {
  turn: TurnData | null
  isStreaming: boolean
  error: string | null
  tokenCount: { input: number; output: number } | null
}

/** 实时流中的未决审批（弹窗数据源；刷新恢复走回放的 pending_approval，形状一致） */
export interface LivePendingApproval {
  request_id: string
  tool: string
  input: unknown
  reason: string
  options: { option_id: string; label: string; content?: string }[]
  full_access: boolean
}

export function useSessionStream(sessionId: string | null) {
  const [turn, setTurn] = useState<TurnData | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tokenCount, setTokenCount] = useState<{ input: number; output: number } | null>(null)
  // 未决审批（FE-4 弹窗数据源）：approval_request 置入，approval_resolved 清除
  const [pendingApproval, setPendingApproval] = useState<LivePendingApproval | null>(null)

  // 用 ref 持有正在构建的 turn，避免闭包读到旧状态
  const turnRef = useRef<TurnData | null>(null)
  // 本轮发送的文本：turn_started 到达时用于立即显示用户气泡
  const sentTextRef = useRef('')
  // 未决审批的镜像 ref（事件归约器内读取，避免闭包旧值）
  const pendingApprovalRef = useRef<{ request_id: string } | null>(null)

  const appendItem = useCallback((item: WorkItem) => {
    if (turnRef.current) {
      turnRef.current.work_items = [...turnRef.current.work_items, item]
      setTurn({ ...turnRef.current })
    }
  }, [])

  /** 事件归约器：SSE 事件 → 实时 turn 状态 */
  const applyEvent = useCallback(
    (event: SseEvent) => {
      const cur = turnRef.current
      switch (event.type) {
        case 'turn_started':
          turnRef.current = {
            turn_id: event.turn_id,
            state: 'running',
            started_at: event.started_at,
            ended_at: null,
            active_ms: null,
            user_message: { id: 'local', text: sentTextRef.current, time: Date.now() },
            work_items: [],
            final_text: null,
          }
          setTurn({ ...turnRef.current })
          break

        case 'delta':
          if (cur) {
            cur.final_text = (cur.final_text ?? '') + event.text
            setTurn({ ...cur })
          }
          break

        case 'tool_started':
          appendItem({
            kind: 'tool_call',
            id: event.tool_call_id,
            name: event.name,
            input: event.input,
            status: 'running' as ToolStatus,
            output: '',
            time: Date.now(),
          })
          break

        case 'tool_completed':
          if (cur) {
            // 同一 tool_call_id 可能先有 tool_started 卡，就地更新；否则补一张结果卡
            const idx = cur.work_items.findIndex(
              (x) => x.kind === 'tool_call' && x.id === event.tool_call_id,
            )
            const item: WorkItem = {
              kind: 'tool_call',
              id: event.tool_call_id,
              name: event.name,
              input: undefined,
              status: event.status,
              output: event.output_preview,
              time: Date.now(),
            }
            cur.work_items =
              idx >= 0
                ? cur.work_items.map((x, i) => (i === idx ? item : x))
                : [...cur.work_items, item]
            setTurn({ ...cur })
          }
          break

        case 'todo_updated':
          appendItem({
            kind: 'todo',
            id: `todo-${Date.now()}`,
            items: event.items,
            time: Date.now(),
          })
          break

        case 'subtask_started':
          appendItem({
            kind: 'subtask',
            id: event.subtask_id,
            goal: event.goal,
            status: 'running',
            output: '',
            time: Date.now(),
          })
          break

        case 'subtask_delta':
          if (cur) {
            const idx = [...cur.work_items].reverse().findIndex(
              (x) => x.kind === 'subtask' && x.id === event.subtask_id,
            )
            if (idx >= 0) {
              const real = cur.work_items.length - 1 - idx
              const sub = cur.work_items[real] as Extract<WorkItem, { kind: 'subtask' }>
              sub.output += event.text
              setTurn({ ...cur })
            }
          }
          break

        case 'subtask_completed':
          if (cur) {
            // 原地改状态而非 map 替换对象：右栏面板持有同一 work item 引用，
            // 替换会让面板里的状态停留在"执行中"（引用与数组脱钩）
            const sub = cur.work_items.find(
              (x) => x.kind === 'subtask' && x.id === event.subtask_id,
            )
            if (sub && sub.kind === 'subtask') sub.status = event.status
            setTurn({ ...cur })
          }
          break

        case 'approval_request':
          appendItem({
            kind: 'approval',
            id: event.request_id,
            tool: event.tool,
            input: event.input,
            reason: event.reason,
            status: 'requested',
            time: Date.now(),
          })
          setPendingApproval({
            request_id: event.request_id,
            tool: event.tool,
            input: event.input,
            reason: event.reason,
            options: event.options,
            full_access: event.full_access,
          })
          pendingApprovalRef.current = { request_id: event.request_id }
          break

        case 'approval_resolved':
          if (cur) {
            cur.work_items = cur.work_items.map((x) =>
              x.kind === 'approval' && x.id === event.request_id
                ? { ...x, status: event.approved ? 'approved' : 'denied' }
                : x,
            )
            setTurn({ ...cur })
          }
          if (pendingApprovalRef.current?.request_id === event.request_id) setPendingApproval(null)
          break

        case 'token_count':
          setTokenCount({ input: event.input_tokens, output: event.output_tokens })
          break

        case 'compacted':
          appendItem({
            kind: 'compaction',
            id: `compact-${Date.now()}`,
            tokens_before: event.tokens_before,
            tokens_after: event.tokens_after,
            time: Date.now(),
          })
          break

        case 'error':
          setError(event.message)
          break

        case 'turn_completed':
          if (turnRef.current) {
            turnRef.current.state = event.state
            turnRef.current.ended_at = event.ended_at
            turnRef.current.active_ms = event.active_ms
            setTurn({ ...turnRef.current })
          }
          break

        default:
          break
      }
    },
    [appendItem],
  )

  /** 发送用户消息：立即本地显示用户气泡，随后消费 SSE 流（execution 随提交生效） */
  const send = useCallback(
    async (text: string, onFinished?: () => void, execution?: ExecutionDraft) => {
      if (!sessionId || isStreaming) return
      setError(null)
      setPendingApproval(null)
      setIsStreaming(true)
      setTokenCount(null)
      sentTextRef.current = text
      turnRef.current = null

      try {
        await streamTurn(sessionId, text, applyEvent, execution)
      } catch (e) {
        // 连接中断：turn 在后端继续跑，界面提示可刷新回看
        setError(e instanceof Error ? e.message : '连接中断，可稍后刷新回看进度')
      } finally {
        setIsStreaming(false)
        onFinished?.()
      }
    },
    [sessionId, isStreaming, applyEvent],
  )

  /** 清空实时轮次（切换会话/详情刷新后由调用方触发） */
  const clearTurn = useCallback(() => {
    turnRef.current = null
    setTurn(null)
    setPendingApproval(null)
    pendingApprovalRef.current = null
  }, [])

  /** 重新生成（错误重试同机制）：回滚最后一轮回复并重跑，事件走同一归约器 */
  const regenerate = useCallback(
    async (onFinished?: () => void) => {
      if (!sessionId || isStreaming) return
      setError(null)
      setIsStreaming(true)
      turnRef.current = null

      try {
        await streamRegenerate(sessionId, applyEvent)
      } catch (e) {
        setError(e instanceof Error ? e.message : '连接中断，可稍后刷新回看进度')
      } finally {
        setIsStreaming(false)
        onFinished?.()
      }
    },
    [sessionId, isStreaming, applyEvent],
  )

  return { turn, isStreaming, error, tokenCount, send, regenerate, setError, clearTurn, pendingApproval, setPendingApproval }
}
