<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import * as api from '../api/formal'
import type { Identity, Measurement } from '../types/formal'
import type { ToolPage, ToolSummary } from '../types/tools'
import type { ComparisonInput, ComparisonOption, ComparisonResult } from '../types/comparison'

const props = defineProps<{ identity: Identity; measurement: Measurement; blocked: boolean; writer: boolean }>()
const emit = defineEmits<{ busy: [value: boolean]; expired: [] }>()
const busy = ref(false), status = ref('正在加载比较依据…'), options = ref<ComparisonOption[]>([]), declaration = ref('')
const before = ref<Measurement | null>(null), accepted = ref(false), result = ref<ComparisonResult | null>(null)
const history = ref<ToolSummary[]>([]), page = ref(1), rawCount = ref(0)
interface Pending { key: string; input: ComparisonInput; runId?: string }
const pending = ref<Pending | null>(null)
const storageKey = `vibradiag:${props.identity.organization_id}:${props.identity.user_id}:comparison:${props.measurement.id}:${props.measurement.current_revision_id}`
let generation = 0, timer: ReturnType<typeof setTimeout> | undefined
const selectedOption = computed(() => options.value.find(o => o.id === declaration.value))
const ready = computed(() => !busy.value && !props.blocked && !pending.value && props.writer && !!props.measurement.input_confirmation && !!before.value?.input_confirmation && !!selectedOption.value && before.value.current_revision_id === selectedOption.value.before_revision_id)
const labels: Record<string, string> = { mean: '均值', rms: 'RMS', ac_rms: 'AC RMS', absolute_peak: '绝对峰值', peak_to_peak: '峰峰值', crest_factor: '峰值因子', pearson_kurtosis: 'Pearson峭度', envelope_mean: '包络均值', envelope_rms: '包络RMS', envelope_peak: '包络峰值' }
const states: Record<string, string> = { running: '运行中', completed: '已完成', failed: '失败', interrupted: '已中断', comparable: '条件满足，已计算数值差异', not_comparable: '条件不足或不可比', related_or_same_acquisition: '重复或同源数据，不作独立前后比较' }
const translations: Record<string, string> = {
  ZERO_BASELINE: '比较前值为零', SIGNED_MEAN_PERCENT_NOT_DEFINED: '均值不提供百分比', METRIC_UNAVAILABLE: '父指标不可用', NUMERIC_RANGE_UNSUPPORTED: '超出可表示数值范围', BASELINE_WITHIN_STATED_UNCERTAINTY: '基线落在所给不确定界内', BASELINE_AT_NUMERIC_RESOLUTION: '基线接近数值分辨能力',
  checked_load_tolerance_required: '缺少已核对的负载允许差异及依据', load_difference_exceeds_declared_tolerance: '负载差异超出声明容差', speed_difference_exceeds_declared_tolerance: '转速差异超出声明容差', checked_acquisition_order_required: '缺少采集顺序核对', capture_order_reversed_or_overlapping: '采集顺序颠倒或时间窗口重叠', different_measurement_points: '测点不一致', different_directions: '方向不一致', different_components: '目标部件不一致', different_assets: '设备未确认相同', different_sampling_rates: '采样率不一致', different_original_sample_counts: '原始点数不一致', bearing_parameter_revision_changed: '轴承参数版本改变', different_acquisition_bandwidths: '采集带宽不一致', different_measurement_chains: '采集链配置不一致', different_envelope_processing: '包络处理配置不一致', different_envelope_effective_segments: '有效片段不一致', unsupported_load_units_or_quantity: '负载单位或物理含义不受支持', different_load_quantity_or_reference: '负载物理量或参考不一致', load_tolerance_scope_mismatch: '负载容差适用范围不一致',
  load_value_required: '缺少负载数值', checked_steady_load_required: '缺少稳态负载依据', acquisition_lineage_required: '缺少采集来源关系', checked_capture_start_time_required: '缺少采集开始时间核对', checked_acquisition_bandwidth_required: '缺少有效带宽核对', checked_measurement_chain_required: '缺少采集链核对', reliable_current_speed_required: '缺少本次可靠转速', speed_uncertainty_required: '缺少转速不确定量', identified_target_required: '缺少明确目标', known_measurement_point_required: '缺少明确测点', known_direction_required: '方向未知',
  identical_original_content: '原始内容完全相同', identical_normalized_samples: '规范化样本完全相同', same_measurement_or_revision: '同一采集记录或版本', same_original_file_object: '同一原文件', same_declared_acquisition: '声明为同次采集', shared_acquisition_root: '存在共同采集根', same_basic_execution: '使用了同一个基础结果', same_envelope_execution: '使用了同一个包络结果',
  measurement_uncertainty_not_supplied_or_unchecked: '测量不确定量未知或未核对', change_at_float64_resolution: '变化接近浮点分辨能力', change_within_stated_uncertainty: '变化落在声明的不确定界内', baseline_at_float64_subnormal_resolution: '基线接近最小可表示量'
}
function text(code: string) { const side = code.startsWith('before_') ? '比较前：' : code.startsWith('after_') ? '比较后：' : ''; const key = side ? code.replace(/^(before|after)_/, '') : code; return side + (translations[key] || key) }
function number(value: number | null) { if (value === null) return '—'; return value !== 0 && (Math.abs(value) < .0001 || Math.abs(value) >= 1e7) ? value.toExponential(5) : Number(value.toPrecision(7)).toString() }
function savePending() { if (pending.value) sessionStorage.setItem(storageKey, JSON.stringify(pending.value)); else sessionStorage.removeItem(storageKey) }
async function act(fn: (epoch: number) => Promise<void>) {
  if (busy.value) return
  busy.value = true; emit('busy', true); const epoch = generation
  try { await fn(epoch) } catch (error) {
    if (epoch !== generation) return
    result.value = null
    if (error instanceof api.ApiError && error.status === 401) emit('expired')
    else { if (error instanceof api.ApiError && [403, 404].includes(error.status)) { before.value = null; options.value = []; history.value = [] }; status.value = error instanceof Error ? error.message : '比较请求失败。' }
  } finally { if (epoch === generation) { busy.value = false; emit('busy', false) } }
}
async function load(epoch: number) {
  const [o, h] = await Promise.all([api.get<{ items: ComparisonOption[] }>(`/tool-runs/comparison-options/${props.measurement.id}`), api.get<ToolPage>(`/tool-runs?measurement_id=${props.measurement.id}&page=${page.value}&page_size=20`)])
  if (epoch !== generation) return
  options.value = o.items; rawCount.value = h.items.length; history.value = h.items.filter(r => r.tool_name === 'compare_measurements')
}
async function chooseInner(epoch: number) {
  accepted.value = false; before.value = null
  const option = selectedOption.value; if (!option) return
  const response = await api.measurement(option.before_measurement_id)
  if (epoch !== generation) return
  before.value = response
  status.value = response.current_revision_id === option.before_revision_id ? '请核对两份输入及比较依据。' : '比较前输入已修订，请刷新比较依据。'
}
function choose() { result.value = null; void act(chooseInner) }
async function show(id: string, epoch: number) {
  clearTimeout(timer); result.value = null
  const response = await api.get<ComparisonResult>(`/tool-runs/${id}`)
  if (epoch !== generation || response.tool_name !== 'compare_measurements' || response.measurement_id !== props.measurement.id) return
  result.value = response
  if (response.status === 'running') {
    status.value = '比较运行中，正在查询状态。'
    timer = setTimeout(() => { if (epoch === generation) void act(async e => { await show(id, e); await load(e) }) }, 2000)
  } else {
    if (pending.value?.runId === response.tool_execution_id) { pending.value = null; savePending() }
    status.value = response.status === 'completed' ? states[response.result?.summary.comparison_status || ''] || '已读取比较结果。' : `${states[response.status]}：${response.error_code}`
  }
}
async function submit(epoch: number, retry = false) {
  if (!pending.value) {
    const declarationId = retry ? result.value?.declaration_id : declaration.value
    if (retry && result.value?.before_measurement_id && before.value?.id !== result.value.before_measurement_id) before.value = await api.measurement(result.value.before_measurement_id)
    if (!declarationId || !props.measurement.input_confirmation || !before.value?.input_confirmation) { status.value = '请先确认两份当前输入。'; return }
    const input: ComparisonInput = { tool_name: 'compare_measurements', declaration_id: declarationId, measurement_revision_id: props.measurement.current_revision_id, confirmation_id: props.measurement.input_confirmation.id, before_confirmation_id: before.value.input_confirmation.id }
    if (retry && result.value) input.retry_of = result.value.tool_execution_id
    pending.value = { key: crypto.randomUUID(), input }; savePending()
  }
  status.value = '正在核对两份来源并比较，响应丢失后请重放原请求。'
  const response = await api.post<ComparisonResult>('/tool-runs', pending.value.input, { 'Idempotency-Key': pending.value.key })
  if (epoch !== generation) return
  pending.value.runId = response.tool_execution_id; savePending()
  await show(response.tool_execution_id, epoch); await load(epoch)
}
function run(retry = false) { if (!props.blocked) void act(e => submit(e, retry)) }
function confirmBefore() { if (!before.value || !accepted.value || props.blocked) return; const id = before.value.id; void act(async epoch => { await api.confirm(before.value!); const updated = await api.measurement(id); if (epoch === generation) { before.value = updated; accepted.value = false; status.value = '比较前当前输入已确认。' } }) }
function stopTracking() { clearTimeout(timer); pending.value = null; savePending(); status.value = '已停止本页跟踪；服务端任务未取消，可从比较历史查询。' }
function turn(delta: number) { page.value += delta; void act(load) }
function download(kind: string) { const id = result.value?.tool_execution_id; if (id) void act(async () => { await api.download(`/tool-runs/${id}/artifacts/${kind}`, `comparison-${kind}.json`) }) }
const beforeMetadata = computed(() => result.value?.result?.summary.before.metadata || before.value?.revision.metadata)
const afterMetadata = computed(() => result.value?.result?.summary.after.metadata || props.measurement.revision.metadata)
void act(async epoch => {
  try { const saved = sessionStorage.getItem(storageKey); if (saved) pending.value = JSON.parse(saved) } catch { sessionStorage.removeItem(storageKey) }
  await load(epoch)
  if (pending.value) { declaration.value = pending.value.input.declaration_id; await chooseInner(epoch); status.value = '存在未确认响应的比较请求，请重放原请求。'; if (pending.value?.runId) await show(pending.value.runId, epoch) }
  else status.value = '选择已登记的比较依据，核对输入后运行。'
})
onBeforeUnmount(() => { generation++; clearTimeout(timer); emit('busy', false) })
</script>

<template>
  <section class="panel comparison-panel">
    <div class="section-heading"><h2>前后比较 · 条件核对</h2><button class="text-button" :disabled="busy || blocked" @click="act(load)">刷新比较</button></div>
    <p class="muted">当前采集作为“比较后”。比较依据绑定两份输入及父分析结果，缺条件时保留不可比原因；数值下降不等于维修有效。</p>
    <p role="status" aria-live="polite" class="status">{{ status }}</p>
    <div v-if="pending" class="notice"><p>比较请求待确认，重放不会创建重复任务。</p><div class="button-row"><button :disabled="busy || blocked" @click="run()">重放原比较请求</button><button class="secondary" :disabled="busy" @click="stopTracking">停止比较跟踪</button></div></div>
    <label>已登记的比较依据<select v-model="declaration" :disabled="busy || blocked || !!pending" @change="choose"><option value="">选择比较前采集</option><option v-for="o in options" :key="o.id" :value="o.id">{{ o.before_filename }} → 当前采集 · {{ o.id.slice(0, 8) }}</option></select></label>
    <p v-if="!options.length" class="muted">尚无可用比较依据，请先补齐两份采集的来源、工况和处理资料，并由负责参数维护的人员登记。</p>
    <p v-if="selectedOption" class="muted">依据说明：{{ selectedOption.reason }}</p>
    <table v-if="beforeMetadata"><thead><tr><th>采集条件</th><th>比较前</th><th>比较后</th></tr></thead><tbody>
      <tr><th>开始时间</th><td>{{ beforeMetadata.acquired_at || '未知' }}</td><td>{{ afterMetadata.acquired_at || '未知' }}</td></tr>
      <tr><th>测点 / 方向</th><td>{{ beforeMetadata.measurement_point_id || '未知' }} / {{ beforeMetadata.direction }}</td><td>{{ afterMetadata.measurement_point_id || '未知' }} / {{ afterMetadata.direction }}</td></tr>
      <tr><th>转速</th><td>{{ beforeMetadata.rpm ?? '未知' }} rpm</td><td>{{ afterMetadata.rpm ?? '未知' }} rpm</td></tr>
      <tr><th>负载</th><td>{{ beforeMetadata.load_value ?? '未知' }} {{ beforeMetadata.load_unit }}</td><td>{{ afterMetadata.load_value ?? '未知' }} {{ afterMetadata.load_unit }}</td></tr>
    </tbody></table>
    <div v-if="before && !before.input_confirmation && writer" class="confirmation"><label class="check-label"><input v-model="accepted" type="checkbox" :disabled="busy || blocked">已核对比较前当前输入；此确认不代表现场标定或维修有效。</label><button :disabled="busy || blocked || !accepted || !!pending" @click="confirmBefore">确认比较前输入</button></div>
    <p v-if="!measurement.input_confirmation" class="muted">请先在采集信息中确认比较后当前输入。</p>
    <button :disabled="!ready" @click="run()">核对条件并比较</button>
    <div class="table-wrap"><table v-if="history.length"><thead><tr><th>比较任务</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="r in history" :key="r.tool_execution_id"><td>{{ r.tool_execution_id.slice(0, 8) }}<small>{{ new Date(r.started_at).toLocaleString() }}</small></td><td>{{ states[r.status] }}</td><td><button class="text-button" :disabled="busy || blocked" @click="act(e => show(r.tool_execution_id, e))">查看比较结果</button></td></tr></tbody></table></div>
    <div class="button-row"><button class="secondary" :disabled="busy || blocked || page <= 1" @click="turn(-1)">较新比较任务</button><span>{{ page }}</span><button class="secondary" :disabled="busy || blocked || rawCount < 20" @click="turn(1)">更早比较任务</button></div>
    <div v-if="result">
      <h3>前后比较 · {{ states[result.result?.summary.comparison_status || result.status] }}</h3><p class="muted">{{ result.tool_execution_id }}</p>
      <p v-if="!result.is_current_revision || !result.is_current_before_revision" class="notice">历史比较结果：至少一份输入已有新版本，此处保留受理时的原结果。</p>
      <p v-if="!result.input_confirmation_active" class="notice">本次或父结果的输入确认已撤销，不能作为当前有效确认使用。</p>
      <p v-if="result.error_code" class="error">{{ result.error_code }}</p>
      <ul v-if="result.result"><li v-for="reason in [...result.result.summary.related_reasons, ...result.result.summary.reasons]" :key="reason">{{ text(reason) }}</li></ul>
      <p v-if="result.result?.summary.checks.speed" class="muted">转速核对：{{ number(result.result.summary.checks.speed.before_rpm) }} → {{ number(result.result.summary.checks.speed.after_rpm) }} rpm；本版允许差异 {{ number(result.result.summary.checks.speed.tolerance_rpm) }} rpm。</p>
      <p v-if="result.result?.summary.checks.load" class="muted">负载核对：{{ number(result.result.summary.checks.load.before) }} → {{ number(result.result.summary.checks.load.after) }} {{ result.result.summary.checks.load.unit }}；声明允许差异 {{ number(result.result.summary.checks.load.tolerance) }}。</p>
      <div v-if="result.result?.summary.comparison_status === 'comparable'" class="table-wrap"><table class="comparison-metrics"><thead><tr><th>指标 / 单位</th><th>比较前</th><th>比较后</th><th>差值（后−前）</th><th>相对变化</th></tr></thead><tbody><tr v-for="(m, name) in result.result.summary.metrics" :key="name"><td>{{ labels[name] || name }}<small>{{ m.unit === '1' ? '无量纲' : 'm/s²' }}</small></td><td>{{ number(m.before) }}</td><td>{{ number(m.after) }}</td><td>{{ number(m.delta) }}<small v-if="m.delta_reason">{{ text(m.delta_reason) }}</small></td><td>{{ m.relative_change_percent === null ? '—' : number(m.relative_change_percent) + '%' }}<small v-if="m.percent_reason">{{ text(m.percent_reason) }}</small><small v-for="reason in m.limitations" :key="reason">{{ text(reason) }}</small></td></tr></tbody></table></div>
      <p class="notice">本结果仅为声明条件下的数值比较。采集根不同不代表已证明统计独立，当前不判断健康、维修有效或发布诊断证据。</p>
      <div class="button-row"><button v-for="a in result.artifacts" :key="a.kind" class="secondary" :disabled="busy || blocked" @click="download(a.kind)">下载比较{{ a.kind === 'manifest' ? '清单' : '结果' }}</button></div>
      <button v-if="['failed', 'interrupted'].includes(result.status) && result.is_current_revision && result.is_current_before_revision && writer" :disabled="busy || blocked || !!pending" @click="run(true)">显式重试比较（新任务）</button>
    </div>
  </section>
</template>
