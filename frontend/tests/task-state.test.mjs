import test from 'node:test'
import assert from 'node:assert/strict'
import { ref } from 'vue'
import { createSession } from '../src/composables/useSession.ts'
import { createTaskReader } from '../src/features/agent-tasks/useAgentTasks.ts'
import { ApiError } from '../src/api/formal.ts'
import * as formal from '../src/api/formal.ts'

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const identity = name => ({ organization_id: 'org', user_id: name, roles: ['engineer'] })
const task = (id, status = 'completed') => ({ id, goal: 'inspect_signal', status, version: 1, executor: 'rules_graph_v1',
  draft_validity: { usable: true, inputs_current: true, confirmations_active: true, contexts_current: true, knowledge_current: true },
  draft: { summary: id, observations: [{ text: id, citations: [{ kind: 'basic_analysis', id: 'source' }] }] } })
function fixture(transport = {}) {
  const session = createSession({ me: async () => identity('owner'), login: async () => identity('owner'), logout: async () => {}, hasCsrf: () => true, forgetCsrf: () => {} })
  const visible = ref(true), timers = new Map(), delays = []
  const api = { list: async page => ({ items: [{ id: 'a', goal: 'inspect_signal', status: 'queued' }], page, page_size: 20 }), read: async id => task(id), source: async () => ({ status: 'completed' }), ...transport }
  const reader = createTaskReader(session, api, visible, { set: (fn, ms) => { const id = Symbol(); timers.set(id, fn); delays.push(ms); return id }, clear: id => timers.delete(id) })
  return { session, reader, visible, timers, delays }
}

test('late login and restore cannot adopt an invalidated identity', async () => {
  const pending = deferred(); let forgotten = 0
  const s = createSession({ me: () => pending.promise, login: () => pending.promise, logout: async () => {}, hasCsrf: () => true, forgetCsrf: () => forgotten++ })
  const restore = s.restore(); s.invalidate(); pending.resolve(identity('old')); await restore
  assert.equal(s.identity.value, null); assert.ok(forgotten)
  const loginResult = deferred()
  const second = createSession({ me: async () => identity('x'), login: () => loginResult.promise, logout: async () => {}, hasCsrf: () => false, forgetCsrf: () => {} })
  const login = second.login('org', 'old', 'not-a-real-password'); second.invalidate(); loginResult.resolve(identity('old')); await login
  assert.equal(second.identity.value, null)
})

test('auth operations stay serialized and logout clears identity before network completion', async () => {
  const pending = deferred(); let calls = 0
  const s = createSession({ me: async () => identity('a'), login: async () => { calls++; return pending.promise }, logout: async () => pending.promise, hasCsrf: () => true, forgetCsrf: () => {} })
  const login = s.login('org', 'a', 'test-only'); await s.login('org', 'b', 'test-only'); assert.equal(calls, 1)
  pending.resolve(identity('a')); await login
  const logout = s.logout(); assert.equal(s.identity.value, null); await logout
})

test('late task/list responses do not cross session changes even if transport ignores abort', async t => {
  const pending = deferred(); const f = fixture({ read: () => pending.promise }); t.after(f.reader.dispose)
  await f.session.login('org', 'a', 'test-only'); await f.reader.refreshList()
  const reading = f.reader.select('a'); f.session.invalidate(); pending.resolve(task('a')); await reading
  assert.equal(f.reader.detail.value, null); assert.deepEqual(f.reader.items.value, [])
  const next = deferred(); const g = fixture({ list: () => next.promise }); t.after(g.reader.dispose)
  await g.session.login('org', 'a', 'test-only'); const listing = g.reader.refreshList(); g.session.invalidate()
  next.resolve({ items: [{ id: 'old' }], page: 1 }); await listing
  assert.deepEqual(g.reader.items.value, []); assert.equal(g.reader.listBusy.value, false)
})

test('switching selected task rejects late old detail and does not clear new busy state', async t => {
  const a = deferred(), b = deferred(); const f = fixture({ read: id => id === 'a' ? a.promise : b.promise }); t.after(f.reader.dispose)
  await f.session.login('org', 'a', 'test-only'); const first = f.reader.select('a'), second = f.reader.select('b')
  a.resolve(task('a')); await first; assert.equal(f.reader.detail.value, null); assert.equal(f.reader.detailBusy.value, true)
  b.resolve(task('b')); await second; assert.equal(f.reader.detail.value.id, 'b')
})

test('unusable draft is removed, not only marked in the view', async t => {
  const invalid = task('a'); invalid.draft_validity.usable = false
  const f = fixture({ read: async () => invalid }); t.after(f.reader.dispose)
  await f.session.login('org', 'a', 'test-only'); await f.reader.select('a')
  assert.equal(f.reader.detail.value.draft, null); assert.equal(f.reader.visibleDraft.value, null)
})

test('active polling pauses when hidden and resumes with one refreshed request', async t => {
  let reads = 0; const f = fixture({ read: async id => { reads++; return task(id, 'queued') } }); t.after(f.reader.dispose)
  await f.session.login('org', 'a', 'test-only'); await f.reader.select('a')
  assert.equal(f.timers.size, 1); assert.equal(f.delays.at(-1), 2000)
  f.visible.value = false; assert.equal(f.timers.size, 0)
  f.visible.value = true; await new Promise(resolve => setImmediate(resolve))
  assert.equal(reads, 2); assert.equal(f.timers.size, 1)
})

test('failed poll hides stale content, backs off and does not invent success', async t => {
  let reads = 0; const f = fixture({ read: async id => { if (reads++) throw new ApiError('busy', 429); return task(id, 'queued') } }); t.after(f.reader.dispose)
  await f.session.login('org', 'a', 'test-only'); await f.reader.select('a'); await f.reader.refreshDetail()
  assert.equal(f.reader.detail.value, null); assert.equal(f.delays.at(-1), 4000); assert.equal(f.timers.size, 1)
})

test('401 clears the shared session; forbidden or missing tasks clear detail and stop polling', async t => {
  for (const code of [401, 403, 404]) {
    const f = fixture({ read: async () => { throw new ApiError('not available', code) } }); t.after(f.reader.dispose)
    await f.session.login('org', 'a', 'test-only'); await f.reader.refreshList(); await f.reader.select('a')
    assert.equal(f.reader.detail.value, null); assert.equal(f.timers.size, 0)
    if (code === 401) assert.equal(f.session.identity.value, null)
  }
})

test('source fetch revalidates the task, rejects removed citations and clears revoked source', async t => {
  let reads = 0, sources = 0
  const f = fixture({ read: async id => { const value = task(id); if (reads++) value.draft_validity.usable = false; return value }, source: async () => { sources++; return {} } }); t.after(f.reader.dispose)
  await f.session.login('org', 'a', 'test-only'); await f.reader.select('a'); await f.reader.readSource({ kind: 'basic_analysis', id: 'source' })
  assert.equal(sources, 0); assert.equal(f.reader.visibleDraft.value, null)
  const g = fixture({ source: async () => ({ is_current_revision: false }) }); t.after(g.reader.dispose)
  await g.session.login('org', 'a', 'test-only'); await g.reader.select('a'); await g.reader.readSource({ kind: 'basic_analysis', id: 'source' })
  assert.equal(g.reader.detail.value, null); assert.equal(g.reader.preview.value, null)
})

test('late source cannot reappear after disposal', async t => {
  const response = deferred(); const f = fixture({ source: () => response.promise }); t.after(f.reader.dispose)
  await f.session.login('org', 'a', 'test-only'); await f.reader.select('a')
  const reading = f.reader.readSource({ kind: 'basic_analysis', id: 'source' }); await new Promise(resolve => setImmediate(resolve))
  f.reader.dispose(); response.resolve({ status: 'completed' }); await reading
  assert.equal(f.reader.preview.value, null); assert.equal(f.timers.size, 0)
})

test('logout retains its required CSRF header while discarding a late download', async t => {
  const previousFetch = globalThis.fetch, previousDocument = globalThis.document, response = deferred()
  t.after(() => { globalThis.fetch = previousFetch; globalThis.document = previousDocument; formal.forgetCsrf() })
  let logoutHeader
  globalThis.document = { createElement: () => { throw new Error('Old-session download must not be triggered') } }
  globalThis.fetch = async (path, options) => {
    if (path.endsWith('/auth/login')) return Response.json({ csrf_token: 'test-csrf-only' })
    if (path.endsWith('/auth/me')) return Response.json(identity('owner'))
    if (path.endsWith('/auth/logout')) { logoutHeader = options.headers.get('X-CSRF-Token'); return new Response(null, { status: 204 }) }
    return response.promise
  }
  await formal.login('org', 'owner', 'test-only')
  const download = formal.download('/test-artifact', 'test.csv'); await formal.logout()
  response.resolve(new Response('synthetic bytes')); await download
  assert.equal(logoutHeader, 'test-csrf-only'); assert.equal(formal.hasCsrf(), false)
})

test('late authentication response cannot restore old CSRF state', async t => {
  const previousFetch = globalThis.fetch, response = deferred()
  t.after(() => { globalThis.fetch = previousFetch; formal.forgetCsrf() })
  formal.forgetCsrf(); globalThis.fetch = () => response.promise
  const login = formal.login('org', 'owner', 'test-only', () => false)
  response.resolve(Response.json({ csrf_token: 'must-not-be-stored' }))
  await assert.rejects(login, error => error.name === 'AbortError')
  assert.equal(formal.hasCsrf(), false)
})
