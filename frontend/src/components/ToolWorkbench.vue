<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import * as api from '../api/formal'
import type { Identity, Measurement, RunSummary } from '../types/formal'
import type { ToolInput, ToolName, ToolOptions, ToolPage, ToolResult, ToolSummary, ToolSpectrum, EvidenceResult } from '../types/tools'
import SignalChart from './SignalChart.vue'

const props = defineProps<{ identity: Identity; measurement: Measurement; basics: RunSummary[]; blocked: boolean; writer: boolean }>()
const emit = defineEmits<{ busy: [value: boolean]; expired: [] }>()
const busy = ref(false), message = ref('正在加载工具配置…'), options = ref<ToolOptions>({ contexts: [], profiles: [] })
const history = ref<ToolSummary[]>([]), page = ref(1), context = ref(''), profile = ref(''), envelopeId = ref(''), frequencyId = ref(''), basicId = ref('')
const result = ref<ToolResult | null>(null), series = ref<ToolSpectrum | null>(null), detail = ref<EvidenceResult | null>(null)
const limit = ref<[number, number]>([0, 500]), markers = ref<{ frequency_hz: number; label: string }[]>([])
interface Pending { key: string; input: ToolInput; runId?: string }
const pending = ref<Pending | null>(null)
const storageKey = `vibradiag:${props.identity.organization_id}:${props.identity.user_id}:tools:${props.measurement.id}:${props.measurement.current_revision_id}`
let generation = 0, timer: ReturnType<typeof setTimeout> | undefined
const names: Record<ToolName, string> = { analyze_envelope: '包络计算', calculate_bearing_frequencies: '理论频率', match_frequency_evidence: '频率匹配', compare_measurements: '前后比较' }
const visibleHistory = computed(() => history.value.filter(r => r.tool_name !== 'compare_measurements'))
const statuses: Record<string, string> = { running: '运行中', completed: '已完成', failed: '失败', interrupted: '已中断', ok: '计算完成', unavailable: '条件不足，未执行该计算', observed_support: '观察到支持', no_observed_support: '未观察到支持', ambiguous: '有歧义', not_evaluable: '无法评估' }
const reasons: Record<string, string> = { filter_sensitivity_check: '尚未核查滤波配置敏感性', server_registered_evidence: '尚未登记可用于诊断的证据', complete_frequency_and_speed_uncertainty: '缺少完整的转速或轴承参数不确定量', qualified_numeric_profile: '配置尚未通过诊断使用资格验证', noise_reference_insufficient: '局部背景参考频点不足', resolution_limited: '当前数据不足以分辨目标窗口', window_outside_observation_range: '目标窗口超出观察范围', multiple_peaks: '窗口内存在多个峰', overlapping_target_windows: '目标频率窗口重合', alternative_explanations: '存在转频或拍频等替代解释', no_dynamic_information: '没有可用动态信息', nonpositive_target: '该边带频率非正值' }
const eligible = (tool: ToolName) => history.value.filter(r => r.tool_name === tool && r.status === 'completed' && r.result_status === 'ok' && r.measurement_revision_id === props.measurement.current_revision_id && r.context_id === context.value)
const basicRuns = computed(() => props.basics.filter(r => r.status === 'completed' && r.measurement_revision_id === props.measurement.current_revision_id))
const points = computed<[number, number][]>(() => series.value?.frequency_hz.flatMap((f, i) => f >= limit.value[0] && f <= limit.value[1] ? [[f, series.value!.amplitude_m_s2[i]!] as [number, number]] : []) || [])
const ready = computed(() => props.writer && !!props.measurement.input_confirmation && !props.blocked && !busy.value && !pending.value)
const number = (value: number) => value !== 0 && (Math.abs(value) < .0001 || Math.abs(value) >= 1e7) ? value.toExponential(4) : Number(value.toPrecision(6)).toString()
function savePending() { if (pending.value) sessionStorage.setItem(storageKey, JSON.stringify(pending.value)); else sessionStorage.removeItem(storageKey) }
async function act(fn: (epoch: number) => Promise<void>) {
  if (busy.value) return
  busy.value = true; emit('busy', true); const epoch = generation
  try { await fn(epoch) } catch (error) {
    if (epoch !== generation) return
    result.value = null; series.value = null; detail.value = null
    if (error instanceof api.ApiError && error.status === 401) emit('expired')
    else {
      if (error instanceof api.ApiError && [403, 404].includes(error.status)) { history.value = []; options.value = { contexts: [], profiles: [] }; context.value = ''; profile.value = '' }
      message.value = error instanceof Error ? error.message : '工具请求失败，请重试。'
    }
  } finally { if (epoch === generation) { busy.value = false; emit('busy', false) } }
}
async function load(epoch: number) {
  const [o, h] = await Promise.all([api.get<ToolOptions>(`/tool-runs/options/${props.measurement.id}`), api.get<ToolPage>(`/tool-runs?measurement_id=${props.measurement.id}&page=${page.value}&page_size=20`)])
  if (epoch !== generation) return
  options.value = o; history.value = h.items
  if (!o.contexts.some(c => c.id === context.value && c.is_current_bearing_revision)) context.value = o.contexts.find(c => c.is_current_bearing_revision)?.id || ''
  if (!o.profiles.some(p => p.id === profile.value)) profile.value = o.profiles[0]?.id || ''
}
async function show(id: string, epoch: number) {
  clearTimeout(timer); result.value = null; series.value = null; detail.value = null; markers.value = []
  const response = await api.get<ToolResult>(`/tool-runs/${id}`)
  if (epoch !== generation || response.measurement_id !== props.measurement.id) return
  result.value = response
  if (response.status !== 'running' && pending.value?.runId === id) { pending.value = null; savePending() }
  if (response.status === 'running') {
    message.value = '工具正在运行，可稍后查询；重放原请求不会重复受理。'
    timer = setTimeout(() => { if (epoch === generation) void act(async e => { await show(id, e); await load(e) }) }, 2000)
    return
  }
  message.value = response.status === 'completed' ? statuses[response.result_status || ''] || '已读取结果' : `${statuses[response.status]}：${response.error_code}`
  let plotId = response.tool_name === 'analyze_envelope' && response.result_status === 'ok' ? id : response.envelope_run_id
  if (plotId) {
    const parent = plotId === id ? response : await api.get<ToolResult>(`/tool-runs/${plotId}`)
    const configuration = (parent.result?.summary as { configuration?: { modulation_range_hz: [number, number] } })?.configuration
    const data = await api.get<ToolSpectrum>(`/tool-runs/${plotId}/series?kind=spectrum`)
    if (epoch !== generation) return
    series.value = data; limit.value = configuration?.modulation_range_hz || [0, 500]
  }
  if (response.result?.summary.frequencies) markers.value = Object.entries(response.result.summary.frequencies).map(([label, row]) => ({ label, frequency_hz: row.frequency_hz }))
  if (response.frequency_run_id) {
    const parent = await api.get<ToolResult>(`/tool-runs/${response.frequency_run_id}`)
    if (epoch !== generation) return
    markers.value = Object.entries(parent.result?.summary.frequencies || {}).map(([label, row]) => ({ label, frequency_hz: row.frequency_hz }))
  }
}
function start(tool: ToolName, retry = false) {
  if (props.blocked || !props.measurement.input_confirmation) return
  void act(async epoch => {
    if (!pending.value) {
      const input: ToolInput = { tool_name: tool, measurement_revision_id: props.measurement.current_revision_id, confirmation_id: props.measurement.input_confirmation!.id }
      if (context.value) input.context_id = context.value
      if (tool === 'analyze_envelope') { input.profile_id = profile.value; input.mode = 'exploratory' }
      if (tool === 'match_frequency_evidence') { input.envelope_run_id = envelopeId.value; input.frequency_run_id = frequencyId.value; input.basic_analysis_id = basicId.value }
      if (retry && result.value) {
        input.retry_of = result.value.tool_execution_id
        input.context_id = result.value.context_id || undefined; input.profile_id = result.value.profile_id || undefined
        if (tool === 'match_frequency_evidence') { input.envelope_run_id = result.value.envelope_run_id || undefined; input.frequency_run_id = result.value.frequency_run_id || undefined; input.basic_analysis_id = result.value.basic_analysis_id || undefined }
      }
      pending.value = { key: crypto.randomUUID(), input }; savePending()
    }
    message.value = '正在执行工具，网络中断后请重放原请求。'
    const response = await api.post<ToolResult>('/tool-runs', pending.value.input, { 'Idempotency-Key': pending.value.key })
    if (epoch !== generation) return
    pending.value.runId = response.tool_execution_id; savePending()
    await show(response.tool_execution_id, epoch); await load(epoch)
  })
}
function inspect(id: string, fact = false) { void act(async epoch => { const response = await api.get<EvidenceResult>(`/${fact ? 'facts' : 'evidence'}/${id}`); if (epoch === generation) detail.value = response }) }
function downloadArtifact(kind: string) { const id = result.value?.tool_execution_id; if (id) void act(async () => { await api.download(`/tool-runs/${id}/artifacts/${kind}`, kind + (kind === 'arrays' ? '.npz' : '.json')) }) }
function stopTracking() { clearTimeout(timer); pending.value = null; savePending(); message.value = '已停止跟踪；服务端任务未取消，仍可从历史查询。' }
function turn(delta: number) { page.value += delta; void act(load) }
watch(context, () => { envelopeId.value = ''; frequencyId.value = '' })
watch(basicRuns, () => { if (!basicRuns.value.some(r => r.analysis_id === basicId.value)) basicId.value = basicRuns.value[0]?.analysis_id || '' }, { immediate: true })
void act(async epoch => {
  try { const saved = sessionStorage.getItem(storageKey); if (saved) pending.value = JSON.parse(saved) } catch { sessionStorage.removeItem(storageKey) }
  await load(epoch)
  message.value = pending.value ? '存在未确认响应的工具请求，请重放原请求。' : '选择配置与上下文，进行探索计算。'
  if (pending.value?.runId) await show(pending.value.runId, epoch)
})
onBeforeUnmount(() => { generation++; clearTimeout(timer); emit('busy', false) })
</script>

<template>
  <section class="panel tool-panel">
    <div class="section-heading"><h2>轴承工具 · 探索观察</h2><button class="text-button" :disabled="busy || blocked" @click="act(load)">刷新工具</button></div>
    <p class="notice">这里展示包络、理论频率及匹配观察，尚不提供故障确诊或已审核诊断证据。相同采集派生的多个峰和证据不能算作多次独立采集。</p>
    <p role="status" aria-live="polite" class="status">{{ message }}</p>
    <div v-if="pending" class="notice"><p>工具请求待确认：重放会查询同一任务。</p><div class="button-row"><button :disabled="busy || blocked" @click="start(pending.input.tool_name)">重放原工具请求</button><button class="secondary" :disabled="busy" @click="stopTracking">停止工具跟踪</button></div></div>
    <div class="form-grid">
      <label>目标与诊断上下文<select v-model="context" :disabled="busy || blocked || !!pending"><option value="">未选择（可仅执行包络探索）</option><option v-for="c in options.contexts" :key="c.id" :value="c.id" :disabled="!c.is_current_bearing_revision">{{ c.component_name }} · {{ c.id.slice(0, 8) }}{{ !c.is_current_bearing_revision ? '（旧参数）' : '' }}</option></select></label>
      <label>包络配置<select v-model="profile" :disabled="busy || blocked || !!pending"><option value="">暂无可用配置</option><option v-for="p in options.profiles" :key="p.id" :value="p.id">{{ p.configuration.band_low_hz }}—{{ p.configuration.band_high_hz }} Hz · {{ p.id.slice(0, 8) }} · 探索</option></select></label>
    </div>
    <p v-if="!options.profiles.length || !options.contexts.length" class="muted">缺少配置或目标参数时，请联系负责参数维护的人员。理论频率与匹配需要完整的目标、转速及不确定量依据。</p>
    <p v-if="!measurement.input_confirmation" class="muted">请先确认当前输入。</p>
    <div class="button-row"><button :disabled="!ready || !profile" @click="start('analyze_envelope')">运行包络探索</button><button :disabled="!ready || !context" @click="start('calculate_bearing_frequencies')">计算理论频率</button></div>
    <details><summary>选择父结果并匹配</summary><p class="muted">下拉框显示当前工具历史页中，同上下文、同输入的成功结果；更多结果可翻页查询。基础分析取当前基础分析历史页。</p>
      <label>包络父结果<select v-model="envelopeId" :disabled="busy || blocked || !!pending"><option value="">选择已完成包络</option><option v-for="r in eligible('analyze_envelope')" :key="r.tool_execution_id" :value="r.tool_execution_id">{{ r.tool_execution_id.slice(0, 8) }} · {{ new Date(r.started_at).toLocaleString() }}</option></select></label>
      <label>理论频率父结果<select v-model="frequencyId" :disabled="busy || blocked || !!pending"><option value="">选择理论频率</option><option v-for="r in eligible('calculate_bearing_frequencies')" :key="r.tool_execution_id" :value="r.tool_execution_id">{{ r.tool_execution_id.slice(0, 8) }}</option></select></label>
      <label>基础分析父结果<select v-model="basicId" :disabled="busy || blocked || !!pending"><option value="">选择同输入的基础分析</option><option v-for="r in basicRuns" :key="r.analysis_id" :value="r.analysis_id">{{ r.analysis_id.slice(0, 8) }}</option></select></label>
      <button :disabled="!ready || !context || !envelopeId || !frequencyId || !basicId" @click="start('match_frequency_evidence')">匹配频率与歧义</button>
    </details>
    <div class="table-wrap"><table><thead><tr><th>工具 / 时间</th><th>执行状态</th><th>结果</th></tr></thead><tbody><tr v-for="r in visibleHistory" :key="r.tool_execution_id"><td>{{ names[r.tool_name] }} · {{ r.tool_execution_id.slice(0, 8) }}<small>{{ new Date(r.started_at).toLocaleString() }}</small></td><td>{{ statuses[r.status] }}</td><td><button class="text-button" :disabled="busy || blocked" @click="act(e => show(r.tool_execution_id, e))">查看工具结果</button></td></tr></tbody></table></div>
    <div class="button-row"><button class="secondary" :disabled="busy || blocked || page <= 1" @click="turn(-1)">较新工具任务</button><span>{{ page }}</span><button class="secondary" :disabled="busy || blocked || history.length < 20" @click="turn(1)">更早工具任务</button></div>
    <div v-if="result">
      <h3>{{ names[result.tool_name] }} · {{ statuses[result.status] }} / {{ statuses[result.result_status || ''] }}</h3><p class="muted">{{ result.tool_execution_id }}</p>
      <p v-if="!result.is_current_revision || result.is_current_bearing_revision === false" class="notice">历史版本结果：保持原输入和轴承参数，不代表已按最新参数重算。</p>
      <p v-if="!result.input_confirmation_active" class="notice">输入确认已撤销，请勿作为当前有效确认使用。</p>
      <p v-if="result.error_code" class="error">{{ result.error_code }}</p>
      <ul v-if="result.result?.missing_requirements.length"><li v-for="reason in result.result.missing_requirements" :key="reason">{{ reasons[reason] || reason }}</li></ul>
      <p v-if="result.result?.summary.effective_segment" class="muted">有效原始索引 [{{ result.result.summary.effective_segment.start_index }}, {{ result.result.summary.effective_segment.stop_index_exclusive }}) · {{ result.result.summary.effective_segment.sample_count }} 点</p>
      <SignalChart v-if="points.length" :points="points" :markers="markers" label="包络谱与理论频率标线，Hz / 单边峰值幅度m/s²；标线不等于命中" /><p v-if="points.length" class="muted">包络谱 · Hz / m/s² · 仅展示观察频段，预览最多2000点；标线表示理论名义频率。</p>
      <table v-if="result.result?.summary.frequencies"><thead><tr><th>理论项</th><th>Hz</th></tr></thead><tbody><tr v-for="(f, name) in result.result.summary.frequencies" :key="name"><td>{{ name }}</td><td>{{ number(f.frequency_hz) }}</td></tr></tbody></table>
      <div v-if="result.result?.summary.rows" class="table-wrap matching-table"><table><thead><tr><th>目标 / 窗口</th><th>观察状态</th><th>候选 / 限制</th><th>来源</th></tr></thead><tbody><tr v-for="(r, i) in result.result.summary.rows" :key="r.target_id"><td>{{ r.target_id }}{{ r.aliases.length ? ' / ' + r.aliases.join(', ') : '' }}<small>{{ number(r.target_hz) }} ± {{ number(r.half_width_hz) }} Hz{{ r.auxiliary_only ? ' · 辅助' : '' }}</small></td><td>{{ statuses[r.status] }}</td><td>{{ r.candidates.map(c => number(c.frequency_hz) + ' Hz').join(', ') || '无可报告候选' }}<small>{{ r.reasons.map(x => reasons[x] || x).join('；') }}</small><small v-if="r.overlap_group">共享窗口组：{{ r.overlap_group }}</small></td><td><button v-if="result.evidence_refs[i]" class="text-button" :disabled="busy || blocked" @click="inspect(result.evidence_refs[i]!)">查看观察来源</button></td></tr></tbody></table></div>
      <div v-if="detail" class="notice"><h3>来源解析 · {{ detail.polarity ? statuses[detail.polarity] : detail.kind }}</h3><p>来源产物已校验；{{ detail.validity.input_confirmations_active ? '输入确认仍有效' : '输入确认已撤销' }}。用途：探索观察，未取得诊断证据资格。</p><p v-for="root in detail.root_acquisition_refs" :key="root.file_object_id" class="muted">共同采集根 {{ root.file_object_id }}<br>SHA256 {{ root.sha256 }}</p><button v-if="detail.fact_id" class="text-button" :disabled="busy" @click="inspect(detail.fact_id!, true)">查看关联事实</button><div v-if="detail.parent_fact_ids" class="button-row"><button v-for="id in detail.parent_fact_ids" :key="id" class="text-button" :disabled="busy" @click="inspect(id, true)">上游事实 {{ id.slice(0, 8) }}</button></div><details><summary>事实数值与单位</summary><pre>{{ JSON.stringify(detail.content, null, 2) }}</pre></details></div>
      <p v-if="result.tool_name === 'match_frequency_evidence'" class="notice">混淆检查尚未完整，滤波敏感性未检查；这些材料均为同源探索观察，不能直接生成故障确诊。</p>
      <div class="button-row"><button v-for="a in result.artifacts" :key="a.kind" class="secondary" :disabled="busy || blocked" @click="downloadArtifact(a.kind)">下载 {{ a.kind === 'arrays' ? '完整数值' : a.kind === 'manifest' ? '产物清单' : '结果JSON' }}</button></div>
      <button v-if="['failed', 'interrupted'].includes(result.status) && result.is_current_revision" :disabled="!ready" @click="start(result.tool_name, true)">显式重试工具（新任务）</button>
    </div>
  </section>
</template>
