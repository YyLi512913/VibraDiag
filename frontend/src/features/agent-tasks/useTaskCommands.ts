import { computed, ref, shallowRef, watch } from 'vue'
import { ApiError } from '../../api/formal'
import type { Session } from '../../composables/useSession'
import type { TaskCommand, TaskDetail } from './types'
import * as api from './api'

interface Pending { key: string; command: TaskCommand }
interface Store { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }
export function createTaskCommands(session: Session, completed: (task: TaskDetail) => Promise<void>, transport = api.command, storage: Store = sessionStorage, newKey = () => crypto.randomUUID()) {
  const pending = shallowRef<Pending | null>(null), busy = ref(false), status = ref(''), rejected = ref(false), storageBlocked = ref(false)
  const writer = computed(() => session.identity.value?.roles.some(r => ['inspector', 'engineer'].includes(r)) ?? false)
  let serial = 0, controller: AbortController | undefined, disposed = false
  const storageKey = () => session.identity.value ? `vibradiag:${session.identity.value.organization_id}:${session.identity.value.user_id}:agent-command-v1` : ''
  function reset() {
    serial++; controller?.abort(); busy.value = false; pending.value = null; rejected.value = false; storageBlocked.value = false; status.value = ''
    const key = storageKey()
    if (!key) return
    try {
      const value = storage.getItem(key)
      if (value) {
        const saved = JSON.parse(value)
        if (typeof saved.key !== 'string' || !saved.command || !['create','resume','cancel'].includes(saved.command.kind) || !saved.command.body || (saved.command.kind !== 'create' && typeof saved.command.taskId !== 'string')) throw new Error('Invalid saved command')
        pending.value = saved; status.value = '存在响应未确认的命令，请核对原内容后重放；不会自动提交。'
      }
    } catch { storageBlocked.value = true; status.value = '命令缓存不可读，已阻止新提交；请恢复本标签页存储后重新登录。' }
  }
  const stop = watch([session.identity, session.revision], reset, { flush: 'sync', immediate: true })
  async function replay() {
    if (busy.value || !writer.value || !pending.value || storageBlocked.value || disposed) return
    const saved = pending.value, key = storageKey(), revision = session.revision.value, number = ++serial
    controller = new AbortController(); busy.value = true; rejected.value = false
    const current = () => !disposed && number === serial && revision === session.revision.value && key === storageKey()
    status.value = '正在提交原命令；离开页面不会撤销服务端操作。'
    try {
      const task = await transport(saved.command, saved.key, controller.signal)
      if (!current()) return
      // Commit is acknowledged independently of the subsequent read/selection refresh.
      try { storage.removeItem(key) } catch { storageBlocked.value = true }
      pending.value = null; status.value = '命令已确认受理；请以下方当前任务读取结果为准。'
      try { await completed(task) } catch { if (current()) status.value = '命令已确认受理，详情读取未完成，请刷新列表。' }
    } catch (error) {
      if (!current()) return
      if (error instanceof ApiError && error.status === 401) { session.invalidate(); return }
      // 403/404 can occur while reading an already committed command's response.
      // Preserve those as unknown rather than inviting a fresh command.
      rejected.value = error instanceof ApiError && [400,409,422,429].includes(error.status)
      status.value = error instanceof ApiError && error.status === 409 ? '版本、问题或输入已变化。请刷新当前任务并核对；不会自动替换版本重试。' : `${error instanceof Error ? error.message : '请求未完成'}。原命令已保留；重放沿用相同幂等键。`
    } finally { if (current()) busy.value = false }
  }
  async function submit(command: TaskCommand) {
    if (pending.value || busy.value || !writer.value || storageBlocked.value || disposed) return
    // Persist an immutable copy before any network side effect; fail closed on storage errors.
    const saved: Pending = { key: newKey(), command: JSON.parse(JSON.stringify(command)) }
    try { storage.setItem(storageKey(), JSON.stringify(saved)) }
    catch { storageBlocked.value = true; status.value = '无法保存幂等命令，未发送请求。'; return }
    pending.value = saved; await replay()
  }
  function acknowledgeRejection() {
    if (!rejected.value || busy.value || !pending.value) return
    try { storage.removeItem(storageKey()) } catch { storageBlocked.value = true; return }
    pending.value = null; rejected.value = false; status.value = '已结束被拒命令的跟踪；请核对当前输入和版本后重新操作。'
  }
  function dispose() { disposed = true; serial++; controller?.abort(); stop() }
  return { pending, busy, status, rejected, writer, storageBlocked, submit, replay, acknowledgeRejection, dispose }
}
