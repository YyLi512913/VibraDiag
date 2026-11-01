import test from 'node:test'
import assert from 'node:assert/strict'
import { createSession } from '../src/composables/useSession.ts'
import { createTaskCommands } from '../src/features/agent-tasks/useTaskCommands.ts'
import { ApiError } from '../src/api/formal.ts'
import { command as sendCommand } from '../src/features/agent-tasks/api.ts'
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return {promise,resolve} }
const create = {kind:'create',body:{goal:'explain_method',description:'synthetic test question'}}
function fixture(transport, storage, role='engineer') {
  const values = new Map(), seen=[]
  const store=storage || {getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)}
  const session=createSession({login:async (_o,user)=>({organization_id:'org',user_id:user,roles:[role]}),me:async()=>{},logout:async()=>{},hasCsrf:()=>false,forgetCsrf:()=>{}})
  const commands=createTaskCommands(session,async task=>{seen.push(task.id)},transport,store,()=> 'stable-test-key')
  return {session,commands,values,seen,store}
}
test('unknown create persists immutable command before send and replays exact key/body once explicitly',async t=>{
  const calls=[];let attempts=0
  const f=fixture(async(input,key)=>{calls.push({input:structuredClone(input),key});assert.equal(f.values.size,1);if(!attempts++)throw new TypeError('network lost');return{id:'created'}});t.after(f.commands.dispose)
  await f.session.login('org','owner','test-only');const input=structuredClone(create);await f.commands.submit(input);input.body.description='changed'
  assert.ok(f.commands.pending.value);assert.equal(f.commands.rejected.value,false)
  await f.commands.submit(create);assert.equal(calls.length,1)
  await f.commands.replay();assert.deepEqual(calls[0],calls[1]);assert.equal(f.values.size,0);assert.deepEqual(f.seen,['created'])
})
test('inflight command cannot double submit or publish a late response across identity change',async t=>{
  const response=deferred();let calls=0;const f=fixture(async()=>{calls++;return response.promise});t.after(f.commands.dispose)
  await f.session.login('org','owner','test-only');const run=f.commands.submit(create);await f.commands.replay();assert.equal(calls,1)
  await f.session.logout();await f.session.login('org','peer','test-only');response.resolve({id:'old'});await run
  assert.deepEqual(f.seen,[]);assert.equal(f.commands.pending.value,null);assert.equal(f.values.size,1)
  await f.session.logout();await f.session.login('org','owner','test-only');assert.ok(f.commands.pending.value);assert.equal(calls,1)
})
test('remount restores unknown command without sending; dispose fences even ignored abort',async t=>{
  const response=deferred();let calls=0;const f=fixture(async()=>{calls++;return response.promise});await f.session.login('org','owner','test-only')
  const run=f.commands.submit(create);f.commands.dispose();response.resolve({id:'late'});await run;assert.deepEqual(f.seen,[])
  const replacement=createTaskCommands(f.session,async()=>{},async()=>{calls++;return{id:'replayed'}},f.store,()=> 'new-key');t.after(replacement.dispose)
  assert.ok(replacement.pending.value);assert.equal(calls,1);await replacement.replay();assert.equal(calls,2)
})
test('resume conflict preserves original question and version, requires explicit rejection acknowledgement',async t=>{
  const calls=[];const f=fixture(async(input,key)=>{calls.push({input,key});throw new ApiError('VERSION_CONFLICT',409)});t.after(f.commands.dispose);await f.session.login('org','owner','test-only')
  const input={kind:'resume',taskId:'task',body:{expected_version:3,question_id:'original-question',answer:'explicit answer'}}
  await f.commands.submit(input);assert.equal(f.commands.rejected.value,true);assert.match(f.commands.status.value,/不会自动替换版本/)
  await f.commands.replay();assert.deepEqual(calls[0],calls[1]);assert.equal(calls[1].input.body.expected_version,3)
  f.commands.acknowledgeRejection();assert.equal(f.commands.pending.value,null);assert.equal(f.values.size,0)
})
test('storage failure or corrupt cache fails closed before network; read-only roles cannot submit',async t=>{
  let calls=0;const transport=async()=>{calls++;return{id:'forbidden'}}
  for(const storage of [{getItem:()=>null,setItem:()=>{throw Error('full')},removeItem:()=>{}},{getItem:()=>'{invalid',setItem:()=>{},removeItem:()=>{}}]) {
    const f=fixture(transport,storage);t.after(f.commands.dispose);await f.session.login('org','owner','test-only');await f.commands.submit(create);assert.equal(f.commands.storageBlocked.value,true)
  }
  const f=fixture(transport,undefined,'viewer');t.after(f.commands.dispose);await f.session.login('org','owner','test-only');await f.commands.submit(create);assert.equal(calls,0)
})
test('401 clears visible command but preserves owner cache for explicit same-owner replay',async t=>{
  const f=fixture(async()=>{throw new ApiError('expired',401)});t.after(f.commands.dispose);await f.session.login('org','owner','test-only');await f.commands.submit(create)
  assert.equal(f.session.identity.value,null);assert.equal(f.commands.pending.value,null);assert.equal(f.values.size,1)
})
test('403 and 404 remain unknown because authorization may fail after server commit',async t=>{
  for(const code of [403,404]) {
    const f=fixture(async()=>{throw new ApiError('response no longer visible',code)});t.after(f.commands.dispose)
    await f.session.login('org','owner','test-only');await f.commands.submit(create);f.commands.acknowledgeRejection()
    assert.equal(f.commands.rejected.value,false);assert.ok(f.commands.pending.value);assert.equal(f.values.size,1)
  }
})
test('confirmed acceptance clears command even if followup read fails; cancel keeps original expected_version',async t=>{
  const f=fixture(async()=>({id:'cancelled'}));await f.session.login('org','owner','test-only');f.commands.dispose()
  const inputs=[];const c=createTaskCommands(f.session,async()=>{throw Error('read failed')},async input=>{inputs.push(input);return{id:'cancelled'}},f.store,()=> 'cancel-key');t.after(c.dispose)
  await c.submit({kind:'cancel',taskId:'task',body:{expected_version:7}});assert.equal(inputs[0].body.expected_version,7);assert.equal(c.pending.value,null);assert.match(c.status.value,/已确认受理/)
})
test('command transport uses fixed routes, original body and idempotency header',async t=>{
  const previous=globalThis.fetch;t.after(()=>{globalThis.fetch=previous});const calls=[]
  globalThis.fetch=async(path,options)=>{calls.push({path,key:options.headers.get('Idempotency-Key'),body:JSON.parse(options.body)});return Response.json({id:'task'})}
  await sendCommand(create,'original-key',new AbortController().signal)
  await sendCommand({kind:'resume',taskId:'safe-id',body:{expected_version:2,question_id:'q',answer:'checked'}},'resume-key',new AbortController().signal)
  assert.equal(calls[0].path,'/api/v1/agent-tasks');assert.equal(calls[0].key,'original-key');assert.deepEqual(calls[0].body,create.body)
  assert.equal(calls[1].path,'/api/v1/agent-tasks/safe-id/resume');assert.equal(calls[1].body.question_id,'q')
})
