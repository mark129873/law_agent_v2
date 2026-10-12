/** 运行实际审批组件，验证三选项、提交范围、快捷键及重复点击锁；不连接服务。 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const ts = require('typescript')

const approvedOptions = [
  { option_id: 'allowOnce', label: '仅本次允许' },
  { option_id: 'allowSession', label: '本会话内始终允许', content: '目标：/workspace/a.txt' },
  { option_id: 'deny', label: '拒绝' },
]
function fixture(options = approvedOptions) {
  const slots = [], calls = [], listeners = {}, effects = []
  let cursor = 0, resolve, reject, resolved = 0, tree
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  const react = {
    useState(value) { const i = cursor++; if (!(i in slots)) slots[i] = value; return [slots[i], next => { slots[i] = next }] },
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value } },
    useEffect(fn) { effects.push(fn) },
  }
  const jsx = (type, props) => ({ type, props })
  const exports = {}
  const source = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src/components/ApprovalModal.tsx'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  vm.runInNewContext(source, { exports, require: id => ({ react, 'react/jsx-runtime': { jsx, jsxs: jsx },
    '../api/client': { submitApproval: (...args) => { calls.push(args); return promise } },
  })[id] ?? {}, document: { activeElement: null }, window: {
    addEventListener: (kind, fn) => { listeners[kind] = fn }, removeEventListener: () => {},
  } })
  function nodes(value = tree) {
    if (!value || typeof value !== 'object') return []
    if (Array.isArray(value)) return value.flatMap(item => nodes(item ?? null))
    return [value, ...nodes(value.props?.children ?? null)]
  }
  function render() {
    cursor = 0
    tree = exports.ApprovalModal({ sessionId: 's', approval: { request_id: 'r', tool: 'write_file', input: { path: 'a.txt' }, reason: '写文件', options }, onResolved: () => resolved++ })
    effects.splice(0).forEach(fn => fn())
    return tree
  }
  render()
  return { calls, render, nodes, resolve, reject, get resolved() { return resolved },
    button: id => nodes().find(n => n.props?.['data-permission-option-kind'] === id),
    key(key, typing = false) {
      const dialog = nodes().find(n => n.props?.role === 'dialog')
      dialog.props.ref.current = { focus() {} }
      listeners.keydown({ key, target: { closest: () => typing ? {} : null }, preventDefault() {} })
    },
    async flush() { await new Promise(setImmediate); render() },
  }
}

test('只展示三项，旧永久授权/全访问选项不会渲染或占用快捷键', () => {
  const f = fixture([{ option_id: 'fullAccess', label: '旧完全访问' }, ...approvedOptions, { option_id: 'allowAlways', label: '旧永久允许' }])
  assert.deepEqual(f.nodes().filter(n => n.type === 'button').map(n => n.props['data-permission-option-kind']), ['allowOnce', 'allowSession', 'deny'])
  const serialized = JSON.stringify(f.nodes())
  assert.match(serialized, /本会话内始终允许/)
  assert.match(serialized, /刷新保留，后端重启后失效/)
  assert.match(serialized, /workspace\/a.txt/)
  f.key('2'); assert.equal(f.calls[0][2], 'allowSession')
})

for (const option of ['allowOnce', 'allowSession', 'deny']) {
  test(`${option}提交准确标识，双击/连续快捷键只提交一次`, async () => {
    const f = fixture()
    f.nodes().find(n => n.type === 'textarea').props.onChange({ target: { value: '  只阅读123  ' } })
    f.render()
    f.button(option).props.onClick(); f.button(option).props.onClick(); f.key('2')
    assert.equal(f.calls.length, 1)
    assert.deepEqual(f.calls[0], ['s', 'r', option, option === 'deny' ? '只阅读123' : undefined])
    f.render(); assert.ok(f.nodes().filter(n => n.type === 'button').every(n => n.props.disabled))
    f.resolve({ ok: true }); await f.flush(); assert.equal(f.resolved, 1)
  })
}

test('反馈中的数字不会批准；失败后保留弹窗与反馈且可重试', async () => {
  const f = fixture()
  f.key('2', true); assert.equal(f.calls.length, 0)
  f.button('allowSession').props.onClick()
  f.reject(new Error('会话不匹配')); await f.flush()
  assert.equal(f.resolved, 0)
  assert.ok(f.nodes().find(n => n.props?.role === 'alert'))
  assert.ok(f.nodes().filter(n => n.type === 'button').every(n => !n.props.disabled))
})
