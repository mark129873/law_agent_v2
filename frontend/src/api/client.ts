/**
 * API 客户端：所有后端请求的唯一出口。
 * - 普通请求用 fetch + JSON；
 * - turn 的 SSE 用 fetch ReadableStream 手动解析（EventSource 不支持 POST）。
 */

import type { SessionDetail, SessionItem, SseEvent } from '../types'

/** 统一错误：带状态码与后端 detail 信息 */
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    // 后端错误格式：{detail: "..."}（FastAPI 默认）
    let detail = `请求失败（${resp.status}）`
    try {
      const body = await resp.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* 忽略解析失败，用默认信息 */
    }
    throw new ApiError(resp.status, detail)
  }
  return resp.json() as Promise<T>
}

// ---------- 会话 CRUD ----------

/** 新建会话（draft 语义：后端只发 id，首条消息才落库） */
export function createSession(): Promise<{ id: string }> {
  return request('/api/sessions', { method: 'POST' })
}

/** 会话列表（含 running 呼吸点标志） */
export function listSessions(): Promise<SessionItem[]> {
  return request('/api/sessions')
}

/** 会话详情（全量回放，即 resume 的读取路径） */
export function getSessionDetail(id: string): Promise<SessionDetail> {
  return request(`/api/sessions/${id}`)
}

/** 删除会话（用户视角永久删除；进行中会 409） */
export function deleteSession(id: string): Promise<{ ok: boolean }> {
  return request(`/api/sessions/${id}`, { method: 'DELETE' })
}

/** 停止当前 turn */
export function stopTurn(id: string): Promise<{ ok: boolean }> {
  return request(`/api/sessions/${id}/stop`, { method: 'POST' })
}

/** 提交审批决定（与连接无关：刷新后仍可提交） */
export function submitApproval(
  id: string,
  requestId: string,
  approved: boolean,
): Promise<{ ok: boolean }> {
  return request(`/api/sessions/${id}/approval`, {
    method: 'POST',
    body: JSON.stringify({ request_id: requestId, approved }),
  })
}

// ---------- SSE 流式 ----------

/**
 * 发送用户消息并消费 SSE 事件流。
 * 实现要点：
 * 1. POST 响应体就是事件流，用 getReader() 逐块读；
 * 2. 按空行分帧（SSE 规范），每帧含 event: 行与 data: 行；
 * 3. ": keepalive" 心跳帧直接跳过；
 * 4. onEvent 回调由 useSessionStream 提供，负责把事件并入界面状态。
 * 断开/出错时抛出异常；turn 本身在后端继续跑完（刷新回看语义）。
 */
export async function streamTurn(
  sessionId: string,
  text: string,
  onEvent: (event: SseEvent) => void,
): Promise<void> {
  const resp = await fetch(`/api/sessions/${sessionId}/turn`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!resp.ok || !resp.body) {
    let detail = `发送失败（${resp.status}）`
    try {
      const body = await resp.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* keep default */
    }
    throw new ApiError(resp.status, detail)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  // 持续读取直到流结束（turn_completed 后后端会关流）
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // 按空行切帧；最后一段可能不完整，留在 buffer
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const event = parseFrame(frame)
      if (event) onEvent(event)
    }
  }
}

/** 解析单个 SSE 帧；心跳注释帧返回 null */
function parseFrame(frame: string): SseEvent | null {
  const lines = frame.split('\n')
  let dataLine: string | null = null
  for (const line of lines) {
    if (line.startsWith(':')) continue // 心跳
    if (line.startsWith('data: ')) dataLine = line.slice(6)
  }
  if (!dataLine) return null
  try {
    return JSON.parse(dataLine) as SseEvent
  } catch {
    return null
  }
}
