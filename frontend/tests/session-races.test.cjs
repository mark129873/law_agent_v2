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
      if (!sameDeps(slots[i]?.deps, deps)) { slots[i] = { deps }; effects.push(fn) }
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
    vm.runInNewContext(compiled, { exports, require: id => imports[id] ?? {}, Date, console })
    return exports
  }
  const hook = load('hooks/useSessionStream.ts', { react, '../api/client': api })
  const app = load('App.tsx', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' },
    'react-router-dom': router, './api/client': api, './hooks/useSessionStream': hook,
    './components/ChatArea': { ChatArea: 'ChatArea' },
  }, '\nexport { SessionView };')
  const draftIdsRef = { current: new Set(['s']) }
  function render() {
    cursor = 0
    tree = app.SessionView({ sessionId: 's', draftIdsRef })
    const pending = effects; effects = []; pending.forEach(fn => fn())
    return tree.props.children[0].props
  }
  return {
    render, details, navigations,
    async flush() { await new Promise(setImmediate); return render() },
    emit(event) { stream.emit(event) },
    end() { stream.resolve() },
  }
}
const detail = (turns = [], title = '回放') => ({ session: { id: 's', title }, turns, pending_approval: null })
const started = id => ({ type: 'turn_started', turn_id: id, started_at: 1 })
const completed = id => ({ type: 'turn_completed', turn_id: id, state: 'success', ended_at: 2, active_ms: 1 })
const approval = id => ({ type: 'approval_request', request_id: id, tool: 'write_file', input: { path: 'test' }, reason: 'test', options: [], full_access: false })

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
