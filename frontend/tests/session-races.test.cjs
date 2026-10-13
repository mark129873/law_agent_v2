/** 执行实际App/Hook源码的可控调度回归；无浏览器、无网络、无额外测试依赖。
 * 最小Hook宿主只控制状态/effect与延迟Promise；浏览器视觉验收另见模拟页。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const ts = require('typescript')

const root = path.resolve(__dirname, '../src')
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const sameDeps = (a, b) => a && b && a.length === b.length && a.every((value, i) => value === b[i])
function fixture() {
  let cursor = 0, effects = [], stream, tree
  const slots = [], details = [], navigations = []
  const timers = new Map()
  let timerId = 0
  const react = {
    useState(initial) {
      const i = cursor++
      if (!(i in slots)) slots[i] = initial
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value }]
    },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial } },
    useCallback(fn, deps) {
      const i = cursor++
      if (!sameDeps(slots[i]?.deps, deps)) slots[i] = { value: fn, deps }
      return slots[i].value
    },
    useEffect(fn, deps) {
      const i = cursor++
      if (!sameDeps(slots[i]?.deps, deps)) {
        const cleanup = slots[i]?.cleanup
        slots[i] = { deps }
        effects.push(() => { cleanup?.(); slots[i].cleanup = fn() })
      }
    },
  }
  const api = {
    ApiError: class extends Error {},
    getPermissionState: async () => ({ mode: 'build' }),
    getSessionDetail() { const d = deferred(); details.push(d); return d.promise },
    streamTurn(_id, _text, emit) { stream = { ...deferred(), emit }; return stream.promise },
    streamRegenerate(_id, emit) { stream = { ...deferred(), emit }; return stream.promise },
  }
  const jsx = (type, props) => ({ type, props })
  const router = { useNavigate: () => navigate }
  const navigate = (...args) => navigations.push(args)
  function load(file, imports, extra = '') {
    const exports = {}
    const compiled = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8') + extra, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText
    vm.runInNewContext(compiled, {
      exports, require: id => imports[id] ?? {}, Date, console,
      setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id },
      clearTimeout(id) { timers.delete(id) },
    })
    return exports
  }
  const hook = load('hooks/useSessionStream.ts', { react, '../api/client': api })
  const approvalUtils = load('utils/approval.ts', {})
  const app = load('App.tsx', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' },
    'react-router-dom': router, './api/client': api, './hooks/useSessionStream': hook,
    './components/ChatArea': { ChatArea: 'ChatArea' },
    './utils/approval': approvalUtils,
  }, '\nexport { SessionView };')
  const draftIdsRef = { current: new Set(['s']) }
  function render() {
    cursor = 0
    tree = app.SessionView({ sessionId: 's', draftIdsRef })
    const pending = effects; effects = []; pending.forEach(fn => fn())
    return tree.props.children[0].props
  }
  return {
    render, details, navigations, timers,
    tick() { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()) },
    async flush() { await new Promise(setImmediate); return render() },
    emit(event) { stream.emit(event) },
    end() { stream.resolve() },
    disconnect() { stream.reject(new Error('连接中断')) },
  }
}
const detail = (turns = [], title = '回放') => ({ session: { id: 's', title }, turns, pending_approval: null })
const started = id => ({ type: 'turn_started', turn_id: id, started_at: 1 })
const completed = id => ({ type: 'turn_completed', turn_id: id, state: 'success', ended_at: 2, active_ms: 1 })
const approval = id => ({ type: 'approval_request', request_id: id, tool: 'write_file', input: { path: 'test' }, reason: 'test', options: [] })

async function mount() {
  const f = fixture(); f.render(); f.details[0].resolve(detail()); await f.flush(); return f
}

test('批准后继续消费delta、工具、下一审批与终态，审批路径不清流', async () => {
  const f = await mount()
  f.render().onSend('开始', {})
  f.emit(started('one')); f.emit({ type: 'delta', text: '批准前' }); f.emit(approval('a'))
  f.emit({ type: 'approval_resolved', request_id: 'a', approved: true })
  f.render().onApprovalResolved()
  f.emit({ type: 'delta', text: '批准后' })
  f.emit({ type: 'tool_started', tool_call_id: 'tool', name: 'write_file', input: {} })
  f.emit({ type: 'tool_completed', tool_call_id: 'tool', name: 'write_file', status: 'completed', output_preview: 'ok' })
  f.emit(approval('b'))
  // a 的迟到resolved不能把更晚到达的b清掉。
  f.emit({ type: 'approval_resolved', request_id: 'a', approved: true })
  let view = await f.flush()
  assert.equal(f.details.length, 1)
  assert.equal(view.liveTurn.final_text, '批准前批准后')
  assert.equal(view.livePendingApproval.request_id, 'b')
  assert.equal(view.liveTurn.work_items.find(x => x.kind === 'tool_call').status, 'completed')
  f.emit({ type: 'approval_resolved', request_id: 'b', approved: false }); f.emit(completed('one')); f.end()
  view = await f.flush()
  assert.equal(view.liveTurn.state, 'success')
  f.details[1].resolve(detail([{ turn_id: 'one', state: 'success', final_text: '持久化结果' }]))
  view = await f.flush()
  assert.equal(view.liveTurn, null)
  assert.equal(view.detail.turns[0].final_text, '持久化结果')
})

test('上一轮迟到GET不能覆盖已开始的新turn', async () => {
  const f = await mount()
  f.render().onSend('第一轮', {}); f.emit(started('one')); f.emit(completed('one')); f.end(); await f.flush()
  f.render().onSend('第二轮', {}); f.emit(started('two')); f.emit({ type: 'delta', text: '新内容' })
  f.details[1].resolve(detail([{ turn_id: 'one', state: 'success' }], '过期标题'))
  let view = await f.flush()
  assert.equal(view.liveTurn.turn_id, 'two'); assert.equal(view.liveTurn.final_text, '新内容')
  assert.equal(view.detail.session.title, '回放')
  f.emit({ type: 'delta', text: '仍增长' }); f.emit(completed('two')); f.end(); await f.flush()
  f.details[2].resolve(detail([{ turn_id: 'two', state: 'success' }], '最新标题'))
  view = await f.flush()
  assert.equal(view.liveTurn, null); assert.equal(view.detail.session.title, '最新标题')
})

test('回放GET失败保留完成快照，新重跑也不被旧身份清理', async () => {
  const f = await mount()
  f.render().onSend('第一轮', {}); f.emit(started('one')); f.emit({ type: 'delta', text: '已显示结果' }); f.emit(completed('one')); f.end()
  await f.flush(); f.details[1].reject(new Error('offline'))
  let view = await f.flush(); assert.equal(view.liveTurn.final_text, '已显示结果')
  view.onRegenerate(); f.emit(started('regenerated')); f.emit({ type: 'delta', text: '重跑结果' })
  view = await f.flush(); assert.equal(view.liveTurn.turn_id, 'regenerated')
  f.emit(completed('regenerated')); f.end(); await f.flush()
})

test('刷新恢复审批可取新回放；同代GET逆序响应只接受最新', async () => {
  const f = await mount()
  f.render().onApprovalResolved(); f.render().onApprovalResolved()
  f.details[2].resolve(detail([], '新状态')); await f.flush()
  f.details[1].resolve(detail([], '旧状态'))
  assert.equal((await f.flush()).detail.session.title, '新状态')
})

test('挂载GET迟到不能覆盖或导航离开正在开始的新轮', async () => {
  const f = fixture(); f.render().onSend('首条', {}); f.emit(started('one'))
  f.details[0].reject(new Error('旧404'))
  const view = await f.flush()
  assert.equal(view.liveTurn.turn_id, 'one'); assert.equal(f.navigations.length, 0)
  f.emit(completed('one')); f.end(); await f.flush()
})


test('审批HTTP应答晚于流结束时，替代GET仍清理对应完成快照', async () => {
  const f = await mount()
  f.render().onSend('第一轮', {}); f.emit(started('one')); f.emit(approval('a'))
  const lateApprovalCallback = f.render().onApprovalResolved
  f.emit({ type: 'approval_resolved', request_id: 'a', approved: true })
  f.emit({ type: 'delta', text: '完成内容' }); f.emit(completed('one')); f.end(); await f.flush()
  lateApprovalCallback()
  f.details[2].resolve(detail([{ turn_id: 'one', state: 'success', final_text: '完成内容' }]))
  let view = await f.flush()
  assert.equal(view.liveTurn, null); assert.equal(view.detail.turns.length, 1)
  f.details[1].resolve(detail([{ turn_id: 'one', state: 'success' }], '过期结束GET'))
  view = await f.flush()
  assert.equal(view.liveTurn, null); assert.equal(view.detail.session.title, '回放')
})

test('断线后的同轮终态回放清理running快照和已解决审批；运行中回放不清理', async () => {
  const f = await mount()
  f.render().onSend('断线请求', {}); f.emit(started('one')); f.emit(approval('a'))
  f.disconnect(); await f.flush()
  f.details[1].resolve({ ...detail([{ turn_id: 'one', state: 'running' }]), pending_approval: { ...approval('a'), status: 'requested' } })
  let view = await f.flush()
  assert.equal(view.liveTurn.state, 'running')
  assert.equal(view.livePendingApproval.request_id, 'a')
  view.onApprovalResolved()
  f.details[2].resolve(detail([{ turn_id: 'one', state: 'success', final_text: '审批后完成' }]))
  view = await f.flush()
  assert.equal(view.isStreaming, false)
  assert.equal(view.liveTurn, null)
  assert.equal(view.livePendingApproval, null)
  assert.equal(view.detail.turns[0].final_text, '审批后完成')
})

test('断线审批后仍运行时清除旧弹窗，轮询发现下一审批并持续到终态', async () => {
  const f = await mount()
  f.render().onSend('断线请求', {}); f.emit(started('one'))
  f.emit({ type: 'delta', message_id: 'step', text: '尚未落盘的部分输出' }); f.emit(approval('a'))
  f.disconnect(); await f.flush()
  const running = (pending = null) => ({ ...detail([{ turn_id: 'one', state: 'running', final_text: null }]), pending_approval: pending })
  f.details[1].resolve(running({ ...approval('a'), status: 'requested' })); await f.flush()
  f.render().onApprovalResolved(); f.details[2].resolve(running())
  let view = await f.flush()
  assert.equal(view.livePendingApproval, null)
  assert.equal(view.liveTurn.final_text, '尚未落盘的部分输出')
  assert.equal(f.timers.size, 1)
  f.tick(); f.details[3].resolve(running({ ...approval('b'), status: 'requested' }))
  view = await f.flush(); assert.equal(view.livePendingApproval.request_id, 'b')
  view.onApprovalResolved(); f.details[4].resolve(running()); await f.flush()
  assert.equal(f.render().livePendingApproval, null)
  f.tick(); f.details[5].resolve(detail([{ turn_id: 'one', state: 'success', final_text: '最终结果' }]))
  view = await f.flush()
  assert.equal(view.liveTurn, null); assert.equal(view.livePendingApproval, null)
  assert.equal(view.detail.turns[0].final_text, '最终结果'); assert.equal(f.timers.size, 0)
})

test('断线轮询失败后仍重试，迟到轮询不能覆盖新流或给新流排定时器', async () => {
  const f = await mount()
  f.render().onSend('第一轮', {}); f.emit(started('one')); f.disconnect(); await f.flush()
  f.details[1].reject(new Error('离线')); await f.flush()
  f.tick(); f.details[2].reject(new Error('仍离线')); await f.flush()
  assert.equal(f.timers.size, 1)
  f.tick()
  f.render().onSend('新请求', {}); f.emit(started('two')); f.emit(approval('new')); f.render()
  f.details[3].resolve(detail([{ turn_id: 'one', state: 'success' }], '旧轮询'))
  let view = await f.flush()
  assert.equal(view.liveTurn.turn_id, 'two'); assert.equal(view.livePendingApproval.request_id, 'new')
  assert.equal(view.detail.session.title, '回放'); assert.equal(f.timers.size, 0)
  f.emit(completed('two')); f.end(); await f.flush()
})

test('旧轮断线后新发送建轮前被拒，仍能收尾旧轮且不确认清空新草稿', async () => {
  const f = await mount()
  f.render().onSend('旧请求', {}); f.emit(started('old')); f.emit({ type: 'delta', text: '旧轮部分输出' })
  f.disconnect(); await f.flush()
  f.details[1].resolve(detail([{ turn_id: 'old', state: 'running' }])); await f.flush()
  let accepted = false
  f.render().onSend('必须保留的新草稿', {}, () => { accepted = true }); f.render()
  f.disconnect() // 模拟后端409，没有turn_started。
  let view = await f.flush()
  assert.equal(accepted, false); assert.equal(view.liveTurn.turn_id, 'old')
  assert.equal(view.liveTurn.final_text, '旧轮部分输出')
  f.details[2].resolve(detail([{ turn_id: 'old', state: 'success', final_text: '旧轮最终结果' }]))
  view = await f.flush()
  assert.equal(view.liveTurn, null); assert.equal(view.detail.turns[0].final_text, '旧轮最终结果')
  assert.equal(f.timers.size, 0)
})

test('刷新恢复的运行轮没有SSE，也轮询后续审批和最终结果', async () => {
  const f = fixture()
  f.render(); f.details[0].resolve(detail([{ turn_id: 'restored', state: 'running' }]))
  await f.flush(); assert.equal(f.timers.size, 1)
  f.tick(); f.details[1].resolve({ ...detail([{ turn_id: 'restored', state: 'running' }]), pending_approval: { ...approval('restored-approval'), status: 'requested' } })
  let view = await f.flush(); assert.equal(view.detail.pending_approval.request_id, 'restored-approval')
  f.tick(); f.details[2].resolve(detail([{ turn_id: 'restored', state: 'success' }]))
  view = await f.flush(); assert.equal(view.detail.pending_approval, null); assert.equal(f.timers.size, 0)
})

test('断线收口只接受当前轮终态，其他轮的终态不能清理快照', async () => {
  const f = await mount()
  f.render().onSend('断线请求', {}); f.emit(started('one')); f.disconnect(); await f.flush()
  f.details[1].resolve(detail([{ turn_id: 'other', state: 'success' }]))
  assert.equal((await f.flush()).liveTurn.turn_id, 'one')
})

test('工具完成与停止保留开始参数和时间；缺失开始事件仍补结果卡', async () => {
  const f = await mount()
  f.render().onSend('读取', {}); f.emit(started('one'))
  f.emit({ type: 'tool_started', tool_call_id: 'tool', name: 'read_file', input: { path: 'sample.txt' } })
  const time = f.render().liveTurn.work_items[0].time
  f.emit({ type: 'tool_completed', tool_call_id: 'tool', name: 'read_file', status: 'stopped', output_preview: '部分结果' })
  let item = f.render().liveTurn.work_items[0]
  assert.equal(item.input.path, 'sample.txt'); assert.equal(item.time, time)
  assert.equal(item.status, 'stopped'); assert.equal(item.output, '部分结果')
  f.emit({ type: 'tool_completed', tool_call_id: 'missing', name: 'glob', status: 'completed', output_preview: 'file.txt' })
  item = f.render().liveTurn.work_items[1]
  assert.equal(item.name, 'glob'); assert.equal(item.output, 'file.txt')
  f.emit(completed('one')); f.end(); await f.flush()
})

test('模型步骤正文按原顺序归入过程，末段单独展示，错误保留部分输出', async () => {
  const f = await mount()
  f.render().onSend('多步请求', {}); f.emit(started('one'))
  f.emit({ type: 'delta', message_id: 'step-1', text: '先' })
  f.emit({ type: 'delta', message_id: 'step-1', text: '检查文件' })
  f.emit({ type: 'tool_started', tool_call_id: 'tool', name: 'read_file', input: { path: 'sample.txt' } })
  f.emit({ type: 'tool_completed', tool_call_id: 'tool', name: 'read_file', status: 'completed', output_preview: '内容' })
  f.emit({ type: 'delta', message_id: 'step-2', text: '最终答复' })
  f.emit({ type: 'delta', message_id: 'step-2', text: '的一部分' })
  f.emit({ type: 'error', message: '模型流中断' })
  const turn = f.render().liveTurn
  assert.equal(turn.final_text, '最终答复的一部分')
  assert.equal(turn.work_items[0].kind, 'text'); assert.equal(turn.work_items[0].text, '先检查文件')
  assert.equal(turn.work_items[1].kind, 'tool_call')
  assert.equal(turn.work_items[2].kind, 'error'); assert.equal(turn.work_items[2].message, '模型流中断')
  f.emit({ ...completed('one'), state: 'failed' }); f.end(); await f.flush()
})

test('没有后续正文时工具前的末段仍作为最终/部分输出，不因工具事件提前移走', async () => {
  const f = await mount()
  f.render().onSend('停止', {}); f.emit(started('one'))
  f.emit({ type: 'delta', message_id: 'step-1', text: '已生成部分' })
  f.emit({ type: 'tool_started', tool_call_id: 'tool', name: 'read_file', input: {} })
  f.emit({ ...completed('one'), state: 'stopped' }); f.end()
  const view = await f.flush()
  assert.equal(view.liveTurn.final_text, '已生成部分')
  assert.equal(view.liveTurn.work_items.filter(x => x.kind === 'text').length, 0)
})

test('建轮确认才通知输入框清稿，建轮前失败不确认', async () => {
  const f = await mount()
  let accepted = 0
  f.render().onSend('保留请求', {}, () => accepted++)
  f.disconnect(); await f.flush()
  assert.equal(accepted, 0); assert.equal(f.render().liveTurn, null)
  f.render().onSend('再试一次', {}, () => accepted++)
  f.emit(started('two'))
  assert.equal(accepted, 1); assert.equal(f.render().liveTurn.user_message.text, '再试一次')
  f.emit(completed('two')); f.end(); await f.flush()
})

/** 直接执行展示组件，验证实际传给子组件的轮次/错误卡，不复制业务过滤规则。 */
function componentFixture(file, exported, extraImports = {}) {
  let cursor = 0
  const slots = []
  const react = {
    useState(initial) {
      const i = cursor++
      if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value }]
    },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial } },
    useEffect() {},
  }
  const jsx = (type, props) => ({ type, props })
  const imports = { react, 'react/jsx-runtime': { jsx, jsxs: jsx }, ...extraImports }
  const compiled = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  const exports = {}
  vm.runInNewContext(compiled, { exports, require: id => imports[id] ?? {}, Date })
  return props => { cursor = 0; return exports[exported](props) }
}
function nodes(tree, type) {
  if (!tree || typeof tree !== 'object') return []
  if (Array.isArray(tree)) return tree.flatMap(node => nodes(node, type))
  return [...(tree.type === type ? [tree] : []), ...nodes(tree.props?.children, type)]
}
const chatFixture = () => componentFixture('components/ChatArea.tsx', 'ChatArea', {
  '../ShellContext': { useShell: () => ({}) }, '../utils/approval': { pendingFromDetail: () => null },
  './TurnGroup': { TurnGroup: 'TurnGroup' },
})

test('打开历史会话后重跑保留原用户消息，并在实际对话组件替换旧轮', async () => {
  const f = fixture()
  const original = { turn_id: 'old', state: 'success', user_message: { id: 'saved-user', text: '原始请求', time: 1 }, final_text: '旧答案' }
  const earlier = { turn_id: 'earlier', state: 'success', user_message: { id: 'earlier-user', text: '更早请求', time: 0 } }
  f.render(); f.details[0].resolve(detail([earlier, original])); await f.flush()
  f.render().onRegenerate(); f.emit(started('new')); f.emit({ type: 'delta', message_id: 'new-step', text: '新答案' })
  const view = await f.flush()
  assert.equal(view.liveTurn.user_message.text, '原始请求'); assert.equal(view.liveTurn.user_message.id, 'saved-user')
  const shown = nodes(chatFixture()(view), 'TurnGroup').map(node => node.props.turn)
  assert.equal(shown.length, 2); assert.equal(shown[0].turn_id, 'earlier'); assert.equal(shown[1].turn_id, 'new')
  f.emit(completed('new')); f.end(); await f.flush()
})

test('运行中回放和实时轮同ID只显示一份用户消息', () => {
  const turn = { turn_id: 'same', state: 'running', user_message: { id: 'local', text: '请求' }, work_items: [] }
  const tree = chatFixture()({ detail: detail([turn]), liveTurn: turn, permission: { mode: 'build' } })
  assert.equal(nodes(tree, 'TurnGroup').length, 1)
})

test('重跑本地快照时用原turn ID替换回放', async () => {
  const f = await mount()
  f.render().onSend('原始请求', {}); f.emit(started('old')); f.emit({ type: 'delta', text: '旧答案' })
  f.emit(completed('old')); f.end(); await f.flush()
  // 回放仍在飞行中，此时重跑来源的用户ID只能是local。
  f.render().onRegenerate(); f.emit(started('new'))
  const view = await f.flush()
  assert.equal(view.replacedTurnId, 'old'); assert.equal(view.liveTurn.user_message.text, '原始请求')
  const copy = { ...view, detail: detail([{ turn_id: 'old', user_message: { id: 'saved-user', text: '原始请求' } }]) }
  const shown = nodes(chatFixture()(copy), 'TurnGroup')
  assert.equal(shown.length, 1); assert.equal(shown[0].props.turn.turn_id, 'new')
  f.emit(completed('new')); f.end(); await f.flush()
})

test('历史轮重跑在建轮前失败时，原用户消息和旧答案仍可回看', async () => {
  const f = fixture()
  const original = { turn_id: 'old', state: 'success', user_message: { id: 'saved-user', text: '原始请求', time: 1 }, work_items: [], final_text: '已有答案' }
  f.render(); f.details[0].resolve(detail([original])); await f.flush()
  f.render().onRegenerate(); f.disconnect()
  const view = await f.flush()
  const shown = nodes(chatFixture()(view), 'TurnGroup')
  assert.equal(view.liveTurn, null); assert.equal(shown.length, 1)
  assert.equal(shown[0].props.turn.final_text, '已有答案')
  assert.equal(shown[0].props.turn.user_message.text, '原始请求')
})

test('发送草稿在确认前保留，确认只清理未修改的草稿，后续编辑不丢失', () => {
  const render = componentFixture('components/Composer.tsx', 'Composer')
  let accept, sent
  const props = { streaming: false, permission: { mode: 'build' }, onSend(text, _mode, onAccepted) { sent = text; accept = onAccepted } }
  const area = () => nodes(render(props), 'textarea')[0].props
  const submit = () => area().onKeyDown({ key: 'Enter', shiftKey: false, nativeEvent: { isComposing: false }, preventDefault() {} })
  area().onChange({ target: { value: '第一份草稿' } }); submit()
  assert.equal(sent, '第一份草稿'); assert.equal(area().value, '第一份草稿')
  accept(); assert.equal(area().value, '')
  area().onChange({ target: { value: '第二份草稿' } }); submit()
  area().onChange({ target: { value: '等待期间的新草稿' } }); accept()
  assert.equal(area().value, '等待期间的新草稿')
  submit() // 模拟请求失败，没有建轮确认：草稿仍留在输入框。
  assert.equal(area().value, '等待期间的新草稿')
})

test('失败轮即使没有工作条目，也显示失败卡与可用重试动作', () => {
  const render = componentFixture('components/TurnGroup.tsx', 'TurnGroup', {
    '../utils/format': { fmtDuration: () => '0 秒' }, './MessageItem': { ErrorCard: 'ErrorCard' },
  })
  let retries = 0
  const tree = render({ turn: { turn_id: 'failed', state: 'failed', started_at: 1, active_ms: 0, work_items: [], final_text: null }, onRegenerate: () => retries++ })
  const errors = nodes(tree, 'ErrorCard')
  assert.equal(errors.length, 1); assert.match(errors[0].props.message, /失败/)
  errors[0].props.onRetry(); assert.equal(retries, 1)
})
