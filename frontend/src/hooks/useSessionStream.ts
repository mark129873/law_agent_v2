/**
 * useSessionStream：把 SSE 事件流并成"实时 turn"状态。
 *
 * 设计（对应 ZCode 桌面端的工作块归属）：
 * - 实时 turn 与回放 turn 用同一种数据形状（TurnData），渲染组件完全复用；
 * - delta 按 message_id 分步，末条正文为 final_text，前面各步的正文放回工作块；
 * - 工具/审批/任务板/子助手事件按序追加进 work_items（块内条目）；
 * - 事件里出现 turn_completed 后，调用方应重新拉取会话详情，
 *   以落盘事实为准替换实时 turn（耗时等切换为权威值）。
 */

import { useCallback, useRef, useState } from 'react'

import { streamRegenerate, streamTurn, type ExecutionDraft } from '../api/client'
import type { ApprovalOption, SseEvent, TurnData, WorkItem } from '../types'

/** 实时流中的未决审批（弹窗数据源；刷新恢复走回放的 pending_approval，形状一致） */
export interface LivePendingApproval {
  request_id: string
  tool: string
  input: unknown
  reason: string
  options: ApprovalOption[]
}

/** 流结束时的身份快照；异步GET只能收尾这个版本、这个turn。 */
export interface StreamCompletion {
  generation: number
  turnId: string | null
}

export function useSessionStream(sessionId: string | null) {
  // 用量徽标读取收口后的回放，只维护界面实际消费的实时状态。
  const [turn, setTurn] = useState<TurnData | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 未决审批（FE-4 弹窗数据源）：approval_request 置入，approval_resolved 清除
  const [pendingApproval, setPendingApproval] = useState<LivePendingApproval | null>(null)
  const [replacedTurnId, setReplacedTurnId] = useState<string | null>(null)

  // 用 ref 持有正在构建的 turn，避免闭包读到旧状态
  const turnRef = useRef<TurnData | null>(null)
  // 保留用户消息身份：重跑仍属于原消息，界面才能用新轮替换旧回复。
  const sentUserRef = useRef<TurnData['user_message']>(null)
  // 记住末段正文原来的位置；新模型步产生文字时，把上一段放回工具之前。
  const textStepRef = useRef<{ id: string; index: number; time: number } | null>(null)
  // 重跑可能发生在回放尚未返回时，原用户ID仍为local，另存旧轮ID作替换依据。
  const replacementRef = useRef<string | null>(null)
  // ref 在发送当下立即更新；React尚未重渲染时也能拒绝重复发送，并让旧GET失效。
  const streamingRef = useRef(false)
  const generationRef = useRef(0)
  const getStreamGeneration = useCallback(() => generationRef.current, [])
  const hasActiveStream = useCallback(() => streamingRef.current, [])
  const getCompletion = useCallback((generation = generationRef.current): StreamCompletion => (
    { generation, turnId: turnRef.current?.turn_id ?? null }
  ), [])
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
            user_message: sentUserRef.current,
            work_items: [],
            final_text: null,
          }
          textStepRef.current = null
          setReplacedTurnId(replacementRef.current)
          setTurn({ ...turnRef.current })
          break

        case 'delta':
          if (cur && event.text) {
            const stepId = event.message_id ?? 'legacy'
            const previous = textStepRef.current
            if (previous?.id !== stepId) {
              // 与回放一致：只有出现下一条正文，上一条才成为过程说明。
              // 下一步无文字或失败时，上一条仍保留为最终/部分输出。
              if (previous && cur.final_text) {
                const item: WorkItem = { kind: 'text', id: previous.id, text: cur.final_text, time: previous.time }
                cur.work_items = [...cur.work_items.slice(0, previous.index), item, ...cur.work_items.slice(previous.index)]
              }
              cur.final_text = null
              textStepRef.current = { id: stepId, index: cur.work_items.length, time: Date.now() }
            }
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
            status: 'running',
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
            const previous = cur.work_items[idx]
            const item: WorkItem = {
              kind: 'tool_call',
              id: event.tool_call_id,
              name: event.name,
              // 完成事件只带输出预览，不能抹掉开始时已有的参数和顺序时间。
              input: previous?.kind === 'tool_call' ? previous.input : undefined,
              status: event.status,
              output: event.output_preview,
              time: previous?.time ?? Date.now(),
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
          if (pendingApprovalRef.current?.request_id === event.request_id) {
            pendingApprovalRef.current = null
            setPendingApproval(null)
          }
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
          appendItem({ kind: 'error', id: `error-${Date.now()}`, message: event.message, time: Date.now() })
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

  /** 发送用户消息：确认建轮后显示用户气泡并清稿（execution 随提交生效）。 */
  const send = useCallback(
    async (text: string, onFinished?: (completion: StreamCompletion) => void, execution?: ExecutionDraft, onAccepted?: () => void) => {
      if (!sessionId || streamingRef.current) return
      streamingRef.current = true
      const generation = ++generationRef.current
      setError(null)
      setPendingApproval(null)
      pendingApprovalRef.current = null
      setIsStreaming(true)
      sentUserRef.current = { id: 'local', text, time: Date.now() }
      replacementRef.current = null
      // 收到新turn_started前保留旧轮身份；409等拒绝不能让断线旧轮失去回放收尾依据。

      try {
        await streamTurn(sessionId, text, (event) => {
          applyEvent(event)
          // 建轮确认前保留草稿，网络/HTTP失败时用户可以直接重发。
          if (event.type === 'turn_started') onAccepted?.()
        }, execution)
      } catch (e) {
        // 连接中断：turn 在后端继续跑，界面提示可刷新回看
        setError(e instanceof Error ? e.message : '连接中断，可稍后刷新回看进度')
      } finally {
        streamingRef.current = false
        setIsStreaming(false)
        onFinished?.(getCompletion(generation))
      }
    },
    [sessionId, applyEvent, getCompletion],
  )

  /** 断线后用同轮回放同步审批；仅终态清掉正文快照，仍在消费的流由SSE负责。 */
  const reconcileTurn = useCallback((completion: StreamCompletion, replay: TurnData, pending: LivePendingApproval | null) => {
    if (streamingRef.current || generationRef.current !== completion.generation
        || turnRef.current?.turn_id !== completion.turnId
        || replay.turn_id !== completion.turnId) return
    // 批准后模型可能仍在运行；立即采用服务端的下一审批或空值，旧弹窗不能占位。
    setPendingApproval(pending)
    pendingApprovalRef.current = pending
    if (replay.state === 'running') return
    // 断线可能漏掉turn_completed；同代同轮的终态回放可以收掉仍显示running的快照。
    turnRef.current = null
    setTurn(null)
    setPendingApproval(null)
    pendingApprovalRef.current = null
    setReplacedTurnId(null)
  }, [])

  /** 重新生成（错误重试同机制）：回滚最后一轮回复并重跑，事件走同一归约器 */
  const regenerate = useCallback(
    async (source: TurnData | null, onFinished?: (completion: StreamCompletion) => void) => {
      if (!sessionId || streamingRef.current) return
      streamingRef.current = true
      const generation = ++generationRef.current
      setError(null)
      setPendingApproval(null)
      pendingApprovalRef.current = null
      setIsStreaming(true)
      sentUserRef.current = source?.user_message ?? null
      replacementRef.current = source?.turn_id ?? null
      // 重跑被服务端拒绝时，旧快照与身份一并保留，仍可继续恢复其终态。

      try {
        await streamRegenerate(sessionId, applyEvent)
      } catch (e) {
        setError(e instanceof Error ? e.message : '连接中断，可稍后刷新回看进度')
      } finally {
        streamingRef.current = false
        setIsStreaming(false)
        onFinished?.(getCompletion(generation))
      }
    },
    [sessionId, applyEvent, getCompletion],
  )

  return { turn, isStreaming, error, send, regenerate, reconcileTurn, pendingApproval, replacedTurnId, getStreamGeneration, hasActiveStream, getCompletion }
}
