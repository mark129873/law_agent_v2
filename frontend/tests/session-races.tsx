/** 真React/真SSE解析器的浏览器回归页。全部数据留内存，任何未知请求都拒绝。
 * npm run dev 后打开 /tests/session-races.html：
 * 发送→两次批准→完成当前轮→发送第二轮→释放旧回放→确认新轮仍在→完成当前轮。
 */
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import App from '../src/App'
import '../src/index.css'
import type { SessionDetail, SseEvent, TurnData } from '../src/types'

let controller: ReadableStreamDefaultController<Uint8Array> | null = null
let turnNumber = 0
let currentText = ''
let holdNextDetail = false
let releaseOld: (() => void) | null = null
const turns: TurnData[] = []
const session = { id: 'test', title: '审批与回放竞态（模拟数据）', model: 'fake', created_at: Date.now(), updated_at: Date.now(), tokens_used: 0, input_tokens: 0, context_used: 0, context_window: 1000000 }
const snapshot = (): SessionDetail => ({ session, turns: structuredClone(turns), pending_approval: null })
const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
function emit(event: SseEvent) {
  controller?.enqueue(new TextEncoder().encode(`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`))
}
function ask(id: string) {
  emit({ type: 'approval_request', request_id: id, tool: 'write_file', input: { path: `${id}.txt`, content: '仅模拟，不写文件' }, reason: '浏览器回归模拟审批', full_access: false, options: [{ option_id: 'allowOnce', label: '仅本次允许' }, { option_id: 'deny', label: '拒绝' }] })
}
// 完全接管当前测试页的fetch：不连接真实API、数据库或模型，也不透传未知请求。
window.fetch = async (input, init) => {
  const url = String(input)
  if (url === '/api/permission/state') return json({ mode: 'build' })
  if (url === '/api/sessions') return json([{ ...session, running: controller !== null }])
  if (url === '/api/sessions/test' && !init?.method) {
    const copy = snapshot()
    if (holdNextDetail) {
      holdNextDetail = false
      return new Promise<Response>((resolve) => { releaseOld = () => resolve(json(copy)) })
    }
    return json(copy)
  }
  if (url === '/api/sessions/test/turn') {
    turnNumber += 1
    currentText = JSON.parse(String(init?.body)).text
    const body = new ReadableStream<Uint8Array>({ start(next) { controller = next } })
    emit({ type: 'turn_started', turn_id: `turn-${turnNumber}`, started_at: Date.now() })
    emit({ type: 'delta', text: turnNumber === 1 ? '批准前的正文。' : '新一轮结果，不应被旧回放覆盖。' })
    if (turnNumber === 1) ask('approval-1')
    return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
  }
  if (url === '/api/sessions/test/approval') {
    const { request_id, option_id } = JSON.parse(String(init?.body))
    emit({ type: 'approval_resolved', request_id, approved: option_id === 'allowOnce' })
    if (request_id === 'approval-1') {
      emit({ type: 'delta', text: '\n第一项批准后，文字继续增长。' })
      emit({ type: 'tool_started', tool_call_id: 'tool-1', name: 'write_file', input: { path: 'fake.txt' } })
      emit({ type: 'tool_completed', tool_call_id: 'tool-1', name: 'write_file', status: 'completed', output_preview: '模拟工具完成，无文件副作用' })
      ask('approval-2')
    } else emit({ type: 'delta', text: '\n第二项审批后，仍在实时输出。' })
    return json({ ok: true })
  }
  throw new Error(`测试页禁止外部请求：${url}`)
}
function finish() {
  if (!controller) return
  const id = `turn-${turnNumber}`
  turns.push({ turn_id: id, state: 'success', started_at: Date.now() - 1000, ended_at: Date.now(), active_ms: 1000,
    user_message: { id: `u-${turnNumber}`, text: currentText, time: Date.now() }, work_items: [],
    final_text: turnNumber === 1 ? '第一轮持久化结果。' : '新一轮持久化结果，旧回放未覆盖。' })
  if (turnNumber === 1) holdNextDetail = true
  emit({ type: 'turn_completed', turn_id: id, state: 'success', ended_at: Date.now(), active_ms: 1000 })
  controller.close()
  controller = null
}
createRoot(document.getElementById('root')!).render(<>
  <MemoryRouter initialEntries={['/session/test']}><App /></MemoryRouter>
  <div style={{ position: 'fixed', bottom: 6, left: 6, zIndex: 100, padding: 8, background: '#fff8ce', border: '1px solid #aa8100', borderRadius: 8, fontSize: 12 }}>
    <span>纯前端模拟数据，无模型调用 </span>
    <button onClick={finish}>完成当前轮</button>{' · '}
    <button onClick={() => { releaseOld?.(); releaseOld = null }}>释放旧回放</button>
  </div>
</>)
