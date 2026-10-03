/**
 * 类型定义：与后端 SSE / 回放契约一一对应（docs/ARCHITECTURE.md §6）。
 * 后端字段用 epoch 毫秒浮点，前端保持一致不做转换。
 */

/** 侧栏会话项（GET /api/sessions） */
export interface SessionItem {
  id: string
  title: string
  model: string
  created_at: number
  updated_at: number
  tokens_used: number
  input_tokens: number // 输入 token 累计（用量聚合）
  running: boolean // 后台生成中 → 呼吸点
}

/** 工作块条目（kind 判别联合） */
export type WorkItem =
  | { kind: 'text'; id: string; text: string; time: number }
  | {
      kind: 'tool_call'
      id: string
      name: string
      input: unknown
      status: ToolStatus
      output: string
      time: number
    }
  | {
      kind: 'subtask'
      id: string
      goal: string
      status: string
      output: string
      time: number
    }
  | { kind: 'todo'; id: string; items: TodoItem[]; time: number }
  | { kind: 'error'; id: string; message: string; time: number }
  | {
      kind: 'approval'
      id: string
      tool: string
      input: unknown
      reason: string
      status: 'requested' | 'approved' | 'denied' | 'auto' // auto = 权限规则放行
      time: number
    }
  | {
      kind: 'compaction'
      id: string
      tokens_before: number | null
      tokens_after: number | null
      time: number
    }

export type ToolStatus = 'pending' | 'running' | 'completed' | 'failed' | 'denied'

export interface TodoItem {
  content: string
  status: 'pending' | 'in_progress' | 'completed'
}

/** 一轮对话（turn）= 用户消息 + 工作块 + 最终回复 */
export interface TurnData {
  turn_id: string
  state: 'running' | 'success' | 'stopped' | 'failed'
  started_at: number
  ended_at: number | null
  active_ms: number | null
  user_message: { id: string; text: string; time: number } | null
  work_items: WorkItem[]
  final_text: string | null
}

/** 会话详情（GET /api/sessions/{id}） */
export interface SessionDetail {
  session: {
    id: string
    title: string
    model: string
    created_at: number
    updated_at: number
    tokens_used: number
    input_tokens: number // 输入 token 累计
    context_used: number // 最近一轮"最后一步 input"≈当前上下文占用（进度条口径）
    context_window: number // 上下文窗口大小（token）
  }
  turns: TurnData[]
  pending_approval: {
    turn_id?: string
    request_id: string
    tool: string
    input: unknown
    reason: string
    status: string
    options?: { option_id: string; label: string; content?: string }[]
    full_access?: boolean
    time?: number
  } | null
}

/** SSE 事件（后端 event type 的前端形态） */
export type SseEvent =
  | { type: 'turn_started'; turn_id: string; started_at: number }
  | { type: 'delta'; text: string }
  | { type: 'tool_started'; tool_call_id: string; name: string; input: unknown }
  | {
      type: 'tool_completed'
      tool_call_id: string
      name: string
      status: ToolStatus
      output_preview: string
    }
  | { type: 'todo_updated'; items: TodoItem[] }
  | { type: 'subtask_started'; subtask_id: string; goal: string }
  | { type: 'subtask_delta'; subtask_id: string; text: string }
  | { type: 'subtask_completed'; subtask_id: string; status: string }
  | {
      type: 'approval_request'
      request_id: string
      tool: string
      input: unknown
      reason: string
      options: { option_id: string; label: string; content?: string }[] // 动态选项（allowOnce/fullAccess/allowAlways/deny）
      full_access: boolean // 是否投放"完全访问"（子助手请求不投放）
    }
  | { type: 'approval_resolved'; request_id: string; approved: boolean }
  | { type: 'token_count'; input_tokens: number; output_tokens: number }
  | { type: 'compacted'; tokens_before: number; tokens_after: number }
  | {
      type: 'turn_completed'
      turn_id: string
      ended_at: number
      active_ms: number
      state: 'success' | 'stopped' | 'failed'
    }
  | { type: 'error'; message: string }
