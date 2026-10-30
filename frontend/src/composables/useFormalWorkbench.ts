import { onBeforeUnmount, ref } from 'vue'
import * as api from '../api/formal'
import type { AnalysisInput, Asset, FormalAnalysis, Identity, Measurement, Metadata, RecordSummary, Revision, RunSummary, Spectrum, Waveform } from '../types/formal'

interface Pending { key: string; input: AnalysisInput; measurementId: string; analysisId?: string }
export function useFormalWorkbench() {
  const identity = ref<Identity | null>(null), busy = ref(false), status = ref('请登录后导入数据。')
  const records = ref<RecordSummary[]>([]), assets = ref<Asset[]>([]), page = ref(1)
  const selected = ref<Measurement | null>(null), versions = ref<Revision[]>([]), runs = ref<RunSummary[]>([])
  const versionPage = ref(1), runPage = ref(1), result = ref<FormalAnalysis | null>(null)
  const waveform = ref<Waveform | null>(null), spectrum = ref<Spectrum | null>(null), pending = ref<Pending | null>(null)
  let generation = 0, poll: ReturnType<typeof setTimeout> | undefined
  const key = (suffix: string) => `vibradiag:${identity.value?.organization_id}:${identity.value?.user_id}:${suffix}`
  const writer = () => identity.value?.roles.some(r => ['inspector', 'engineer'].includes(r)) || false
  function clearData() { clearTimeout(poll); records.value = []; assets.value = []; selected.value = null; versions.value = []; runs.value = []; result.value = null; waveform.value = null; spectrum.value = null; pending.value = null }
  function reset() { generation++; clearData(); identity.value = null; api.forgetCsrf() }
  async function act(fn: (epoch: number) => Promise<void>) {
    if (busy.value) return
    busy.value = true; const epoch = generation
    try { await fn(epoch) }
    catch (error) {
      if (epoch === generation) {
        if (error instanceof api.ApiError && error.status === 401) { reset(); status.value = '会话失效，请重新登录。' }
        else status.value = error instanceof Error ? error.message : '请求失败，请重试。'
      }
    } finally { busy.value = false }
  }
  async function loadList(epoch: number) { const response = await api.records(page.value); if (epoch === generation) records.value = response.items }
  async function loadHistory(epoch: number) {
    if (!selected.value) return
    const id = selected.value.id
    const [v, r] = await Promise.all([api.revisions(id, versionPage.value), api.analyses(id, runPage.value)])
    if (epoch === generation && selected.value?.id === id) { versions.value = v.items; runs.value = r.items }
  }
  async function showResult(id: string, epoch: number) {
    result.value = null; waveform.value = null; spectrum.value = null
    const response = await api.analysis(id)
    if (epoch !== generation) return
    if (response.measurement_id !== selected.value?.id) return
    result.value = response
    sessionStorage.setItem(key('analysis'), id)
    if (response.status === 'completed') {
      const [w, s] = await Promise.all([api.waveform(id), api.spectrum(id)])
      if (epoch !== generation) return
      waveform.value = w; spectrum.value = s
    }
    if (pending.value?.analysisId === response.analysis_id && response.status !== 'running') {
      pending.value = null; sessionStorage.removeItem(key('pending'))
    }
    if (response.status === 'running') {
      clearTimeout(poll)
      poll = setTimeout(() => { if (epoch === generation) void act(async e => { await showResult(id, e); await loadHistory(e) }) }, 2000)
    }
  }
  async function selectInner(id: string, epoch: number) {
    clearTimeout(poll); selected.value = null; result.value = null; waveform.value = null; spectrum.value = null; runs.value = []; versions.value = []
    const response = await api.measurement(id)
    if (epoch !== generation) return
    selected.value = response; versionPage.value = 1; runPage.value = 1
    sessionStorage.setItem(key('measurement'), id)
    await loadHistory(epoch)
  }
  async function initialize(epoch: number) {
    page.value = 1
    const response = await api.assets(); if (epoch !== generation) return
    assets.value = response.items; await loadList(epoch)
    const saved = sessionStorage.getItem(key('pending'))
    if (saved) { try { pending.value = JSON.parse(saved) } catch { sessionStorage.removeItem(key('pending')) } }
    const id = pending.value?.measurementId || sessionStorage.getItem(key('measurement'))
    if (id) {
      await selectInner(id, epoch)
      const run = sessionStorage.getItem(key('analysis'))
      if (run) await showResult(run, epoch)
    }
    status.value = pending.value ? '存在未确认响应的分析请求，请重试原请求以查询，避免重复分析。' : '已登录，可导入或选择授权数据。'
  }
  const restore = () => act(async epoch => {
    if (!api.hasCsrf()) { status.value = '请在本标签页登录以继续。'; return }
    try { identity.value = await api.me() } catch (error) { if (error instanceof api.ApiError && error.status === 401) return; throw error }
    if (epoch === generation) await initialize(epoch)
  })
  const login = (org: string, username: string, password: string) => act(async epoch => { identity.value = await api.login(org, username, password); if (epoch === generation) await initialize(epoch) })
  const logout = () => act(async () => { clearData(); await api.logout(); reset(); status.value = '已退出登录。' })
  const select = (id: string) => act(async epoch => { await selectInner(id, epoch); status.value = '输入已加载，请核对当前参数版本。' })
  const refresh = () => act(async epoch => { await loadList(epoch); if (selected.value) await selectInner(selected.value.id, epoch) })
  const turnPage = (delta: number) => act(async epoch => { page.value += delta; await loadList(epoch) })
  const historyPage = (kind: 'versions' | 'runs', delta: number) => act(async epoch => { (kind === 'versions' ? versionPage : runPage).value += delta; await loadHistory(epoch) })
  const upload = (file: File, metadata: Metadata) => act(async epoch => {
    status.value = '正在校验与保存原始数据…'
    const response = await api.upload(file, metadata); if (epoch !== generation) return
    await selectInner(response.id, epoch); await loadList(epoch); status.value = '导入完成，请核对预览与参数后确认输入。'
  })
  const revise = (metadata: Metadata, reason: string) => act(async epoch => {
    if (!selected.value) return
    const response = await api.revise(selected.value, metadata, reason); if (epoch !== generation) return
    await selectInner(response.id, epoch); status.value = '新参数版本已保存，需重新确认输入；旧分析保持不变。'
  })
  const confirm = () => act(async epoch => {
    if (!selected.value) return
    await api.confirm(selected.value); await selectInner(selected.value.id, epoch); status.value = '当前输入已确认，可以执行基础分析。'
  })
  const run = (retry = false) => act(async epoch => {
    if (!selected.value?.input_confirmation) return
    if (!pending.value) {
      const input: AnalysisInput = { measurement_revision_id: selected.value.current_revision_id, confirmation_id: selected.value.input_confirmation.id, config_profile: 'basic-v1' }
      if (retry && result.value) input.retry_of = result.value.analysis_id
      pending.value = { key: crypto.randomUUID(), input, measurementId: selected.value.id }
      sessionStorage.setItem(key('pending'), JSON.stringify(pending.value))
    }
    status.value = '正在执行基础分析；网络中断时可重试原请求。'
    const response = await api.analyze(pending.value.input, pending.value.key); if (epoch !== generation) return
    pending.value.analysisId = response.analysis_id; sessionStorage.setItem(key('pending'), JSON.stringify(pending.value))
    await showResult(response.analysis_id, epoch); await loadHistory(epoch)
    status.value = response.status === 'completed' ? '基础分析完成，未进行故障诊断。' : response.status === 'running' ? '分析运行中，正在查询状态…' : `分析${response.status === 'interrupted' ? '中断' : '失败'}：${response.error_code}。可显式重试。`
  })
  const viewResult = (id: string) => act(async epoch => { await showResult(id, epoch); status.value = '已读取分析记录。' })
  const download = (path: string, filename: string) => act(async () => { await api.download(path, filename) })
  const stopTracking = () => { pending.value = null; sessionStorage.removeItem(key('pending')); status.value = '已停止本页请求跟踪；服务端任务未被取消，可从分析历史查询。' }
  const requireLogin = () => { reset(); status.value = '请重新登录以更新当前标签页会话。' }
  onBeforeUnmount(() => { generation++; clearTimeout(poll) })
  return { identity, busy, status, records, assets, selected, versions, runs, result, waveform, spectrum, pending, page, versionPage, runPage, writer, restore, login, logout, select, refresh, turnPage, historyPage, upload, revise, confirm, run, viewResult, download, stopTracking, requireLogin }
}
