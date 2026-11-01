import { computed, onScopeDispose, ref, shallowRef, watch, type Ref } from 'vue'
import { ApiError } from '../../api/formal'
import { useSession, type Session } from '../../composables/useSession'
import * as api from './api'
import { activeStates, type Citation, type SourcePreview, type TaskDetail, type TaskRow } from './types'

interface Clock { set: (fn: () => void, ms: number) => unknown; clear: (value: unknown) => void }
export function createTaskReader(session: Session, transport: typeof api, visible: Ref<boolean>, clock: Clock = {
  set: (fn, ms) => setTimeout(fn, ms), clear: value => clearTimeout(value as ReturnType<typeof setTimeout>),
}) {
  const items = shallowRef<TaskRow[]>([]), detail = shallowRef<TaskDetail | null>(null), preview = shallowRef<SourcePreview | null>(null)
  const page = ref(1), selectedId = ref(''), status = ref('正在读取本人任务…')
  const listBusy = ref(false), detailBusy = ref(false), sourceBusy = ref(false), lastRead = ref('')
  type Channel = 'list' | 'detail' | 'source'
  const requests: Partial<Record<Channel, AbortController>> = {}, serial = { list: 0, detail: 0, source: 0 }
  let disposed = false, timer: unknown, pollEligible = false, failures = 0
  function stopTimer() { if (timer !== undefined) clock.clear(timer); timer = undefined }
  function abort(channel: Channel) { requests[channel]?.abort(); serial[channel]++ }
  function begin(channel: Channel) {
    abort(channel); const controller = new AbortController(), number = serial[channel], revision = session.revision.value
    requests[channel] = controller
    return { signal: controller.signal, current: () => !disposed && !controller.signal.aborted && serial[channel] === number && revision === session.revision.value && !!session.identity.value }
  }
  function clearSelection() {
    stopTimer(); abort('detail'); abort('source'); selectedId.value = ''; detail.value = null; preview.value = null
    detailBusy.value = false; sourceBusy.value = false; pollEligible = false; failures = 0; lastRead.value = ''
  }
  function clear() { abort('list'); clearSelection(); items.value = []; page.value = 1; listBusy.value = false }
  function handle(error: unknown, scope: Channel) {
    if (error instanceof ApiError && error.status === 401) { session.invalidate(); return false }
    if (error instanceof ApiError && [403, 404].includes(error.status)) {
      if (scope === 'list') items.value = []
      detail.value = null; preview.value = null; pollEligible = false
      status.value = '当前任务或来源已不可见，请刷新列表。'; return false
    }
    status.value = error instanceof ApiError && error.status === 429 ? '读取暂受容量限制，稍后重新查询。' : '状态暂未确认，已隐藏上次详情；可刷新重试。'
    return !(error instanceof ApiError) || error.status === 429 || error.status >= 500
  }
  function schedule() {
    stopTimer()
    if (!disposed && visible.value && session.identity.value && selectedId.value && pollEligible) {
      timer = clock.set(() => { timer = undefined; void refreshDetail() }, Math.min(10000, 2000 * 2 ** Math.min(failures, 3)))
    }
  }
  function apply(data: TaskDetail) {
    detail.value = data.draft_validity.usable ? data : { ...data, draft: null }
    lastRead.value = new Date().toLocaleTimeString('zh-CN'); pollEligible = activeStates.has(data.status); failures = 0
    items.value = items.value.map(item => item.id === data.id ? { ...item, status: data.status, version: data.version, inputs_current: data.draft_validity.inputs_current } : item)
    status.value = pollEligible ? '正在跟踪本任务状态。离开此页不会取消服务端任务。' : '已读取任务；有效性截至本次读取，查看来源前会再次核对。'
  }
  async function refreshList(target = page.value) {
    if (!session.identity.value || disposed) return
    clearSelection(); const request = begin('list'); listBusy.value = true; items.value = []
    try {
      const data = await transport.list(target, request.signal)
      if (!request.current()) return
      items.value = data.items; page.value = data.page
      status.value = data.items.length ? '仅显示本人发起且当前可访问的任务。' : '当前页暂无可访问任务，可刷新或翻页；创建入口将在后续提供。'
    } catch (error) { if (request.current()) handle(error, 'list') }
    finally { if (request.current()) listBusy.value = false }
  }
  async function refreshDetail() {
    const id = selectedId.value
    if (!id || !session.identity.value || disposed || !visible.value) return
    stopTimer(); abort('source'); sourceBusy.value = false; preview.value = null
    const request = begin('detail'); detailBusy.value = true; detail.value = null
    try {
      const data = await transport.read(id, request.signal)
      if (!request.current() || selectedId.value !== id) return
      if (data.id !== id) throw new Error('Task binding changed')
      apply(data)
    } catch (error) {
      if (request.current()) { detail.value = null; preview.value = null; failures++; if (!handle(error, 'detail')) pollEligible = false }
    } finally { if (request.current()) { detailBusy.value = false; schedule() } }
  }
  function select(id: string) {
    clearSelection(); selectedId.value = id; pollEligible = activeStates.has(items.value.find(r => r.id === id)?.status ?? 'failed')
    return refreshDetail()
  }
  async function readSource(citation: Citation) {
    const id = selectedId.value
    if (!id || !detail.value?.draft_validity.usable) return
    stopTimer(); abort('detail'); detailBusy.value = false
    const request = begin('source'); sourceBusy.value = true; preview.value = null; detail.value = null
    try {
      const data = await transport.read(id, request.signal)
      if (!request.current() || selectedId.value !== id) return
      apply(data)
      const included = data.draft_validity.usable && data.draft?.observations.some(o => o.citations.some(c => c.id === citation.id && c.kind === citation.kind))
      if (!included) { status.value = '引用已失效或不再属于当前草稿，请重新查看任务。'; return }
      const source = await transport.source(citation, request.signal)
      if (!request.current() || selectedId.value !== id) return
      if (['is_current_revision', 'is_current_before_revision', 'is_current_bearing_revision', 'input_confirmation_active', 'parent_confirmations_active'].some(k => source[k] === false) || citation.kind === 'knowledge_chunk' && source.status !== 'published') {
        detail.value = null; status.value = '来源已失效，已隐藏草稿；请刷新任务。'; return
      }
      preview.value = { citation, data: source }
    } catch (error) { if (request.current()) { detail.value = null; preview.value = null; if (!handle(error, 'source')) pollEligible = false } }
    finally { if (request.current()) { sourceBusy.value = false; schedule() } }
  }
  const stopSession = watch(session.revision, () => { clear(); status.value = '会话已变更，已清空上一账号的任务数据。' }, { flush: 'sync' })
  const stopVisibility = watch(visible, value => {
    stopTimer()
    if (!value) { abort('detail'); abort('source'); detailBusy.value = false; sourceBusy.value = false; preview.value = null }
    else if (selectedId.value) void refreshDetail()
  }, { flush: 'sync' })
  function dispose() { disposed = true; clear(); stopSession(); stopVisibility() }
  return { items, detail, preview, page, selectedId, status, listBusy, detailBusy, sourceBusy, lastRead,
    visibleDraft: computed(() => detail.value?.draft_validity.usable ? detail.value.draft : null),
    refreshList, refreshDetail, select, readSource, dispose }
}

export function useAgentTasks(visible: Ref<boolean>) {
  const reader = createTaskReader(useSession(), api, visible)
  onScopeDispose(reader.dispose)
  return reader
}
