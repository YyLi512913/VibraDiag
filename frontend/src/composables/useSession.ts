import { readonly, ref, shallowReadonly, shallowRef } from 'vue'
import * as api from '../api/formal'
import type { Identity } from '../types/formal'

interface SessionApi {
  me: () => Promise<Identity>
  login: (org: string, username: string, password: string, current: () => boolean) => Promise<Identity>
  logout: () => Promise<unknown>
  hasCsrf: () => boolean
  forgetCsrf: () => void
}

export function createSession(transport: SessionApi = api) {
  const identity = shallowRef<Identity | null>(null), revision = ref(0), busy = ref(false)
  const status = ref('请登录正式工作台。')
  function invalidate(message = '会话失效，请重新登录。') {
    revision.value++; identity.value = null; transport.forgetCsrf(); status.value = message
  }
  async function authenticate(action: (current: () => boolean) => Promise<Identity>, message: string) {
    if (busy.value) return
    busy.value = true; invalidate(message); const epoch = revision.value
    const current = () => epoch === revision.value
    try {
      const value = await action(current)
      if (current()) { identity.value = value; status.value = '已登录正式工作台。' }
    } catch (error) {
      if (current()) invalidate(error instanceof api.ApiError && error.status === 401 ? '登录信息无效或会话已失效。' : error instanceof Error ? error.message : '登录未完成，请重试。')
    } finally { busy.value = false }
  }
  // Restore is serialized with login/logout. It never adopts a response from an older session revision.
  async function restore() {
    if (busy.value || identity.value || !transport.hasCsrf()) return
    busy.value = true; const epoch = revision.value
    try {
      const value = await transport.me()
      if (epoch === revision.value) { identity.value = value; status.value = '已恢复本标签页会话。' }
    } catch { if (epoch === revision.value) invalidate() }
    finally { busy.value = false }
  }
  const login = (org: string, username: string, password: string) => authenticate(current => transport.login(org, username, password, current), '正在登录…')
  async function logout() {
    if (busy.value) return
    busy.value = true; revision.value++; identity.value = null
    try { await transport.logout(); status.value = '已退出登录。' }
    catch { status.value = '本页已清空，服务器退出状态未确认；请重新登录。' }
    finally { transport.forgetCsrf(); busy.value = false }
  }
  return { identity: shallowReadonly(identity), revision: readonly(revision), busy: readonly(busy), status: readonly(status), login, restore, logout, invalidate }
}

const shared = createSession()
export const useSession = () => shared
export type Session = ReturnType<typeof createSession>
