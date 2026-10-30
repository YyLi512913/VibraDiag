<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import MetadataForm from './MetadataForm.vue'
import SignalChart from './SignalChart.vue'
import ToolWorkbench from './ToolWorkbench.vue'
import ComparisonWorkbench from './ComparisonWorkbench.vue'
import { useFormalWorkbench } from '../composables/useFormalWorkbench'
import type { Metadata } from '../types/formal'

const w = useFormalWorkbench()
const { identity, busy: baseBusy, status, records, assets, selected, versions, runs, result, waveform, spectrum, pending, page, versionPage, runPage } = w
const toolBusy = ref(false)
const comparisonBusy = ref(false)
const busy = computed(() => baseBusy.value || toolBusy.value || comparisonBusy.value)
const org = ref(localStorage.getItem('vibradiag-org') || ''), username = ref(localStorage.getItem('vibradiag-username') || ''), password = ref('')
const importing = ref(false), editing = ref(false), accepted = ref(false)
watch(() => identity.value?.user_id, () => { importing.value = false; editing.value = false; accepted.value = false })
const labels: Record<string, string> = { mean: '均值', rms: 'RMS（含直流）', ac_rms: 'AC RMS', absolute_peak: '绝对峰值', peak_to_peak: '峰峰值', crest_factor: '峰值因子', pearson_kurtosis: 'Pearson 峭度' }
const stateLabels = { running: '运行中', completed: '已完成', failed: '失败', interrupted: '已中断' }
const sources: Record<string, string> = { synthetic: '合成数据', unknown: '未知 / 未核实', public_dataset: '公开数据集', field_upload: '现场上传（声明）' }
const reasonLabels: Record<string, string> = { ZERO_RMS: 'RMS为零', ZERO_VARIANCE: '方差为零', NUMERIC_RANGE_UNSUPPORTED: '超出可表示数值范围' }
const qualityWarnings = computed(() => (result.value?.warnings || []).filter(text => !text.startsWith('Basic analysis only')).map(text => text.startsWith('Constant signal') ? '恒定信号：动态信息不足，峭度不可计算；不能据此判断健康。' : text.startsWith('Zero signal') ? '零信号：峰值因子不可计算，请核对采集条件。' : text.startsWith('One or more') ? '部分派生指标超出数值范围，已标记为不可用。' : text))
const rawPoints = computed<[number, number][]>(() => selected.value?.preview.time_s.map((t, i) => [t, selected.value!.preview.values[i]!]) || [])
const wavePoints = computed<[number, number][]>(() => waveform.value?.time_s.map((t, i) => [t, waveform.value!.values[i]!]) || [])
const spectrumPoints = computed<[number, number][]>(() => spectrum.value?.frequency_hz.map((f, i) => [f, spectrum.value!.amplitude_m_s2[i]!]) || [])
const retryable = computed(() => result.value && ['failed', 'interrupted'].includes(result.value.status) && result.value.is_current_revision)
async function signIn() { try { await w.login(org.value.trim(), username.value.trim(), password.value); if (identity.value) { localStorage.setItem('vibradiag-org', org.value.trim()); localStorage.setItem('vibradiag-username', username.value.trim()) } } finally { password.value = '' } }
async function save(metadata: Metadata, reason: string, file: File | null) {
  if (editing.value) { const old = selected.value?.current_revision_id; await w.revise(metadata, reason); if (old !== selected.value?.current_revision_id) editing.value = false }
  else if (file) { const old = selected.value?.id; await w.upload(file, metadata); if (old !== selected.value?.id) importing.value = false }
  accepted.value = false
}
function choose(id: string) { importing.value = false; editing.value = false; accepted.value = false; void w.select(id) }
const number = (value: number | null) => value === null ? '不可用' : value !== 0 && (Math.abs(value) < 1e-4 || Math.abs(value) >= 1e7) ? value.toExponential(5) : new Intl.NumberFormat('zh-CN', { maximumSignificantDigits: 7 }).format(value)
onMounted(w.restore)
</script>

<template>
  <header class="formal-header"><div><p class="eyebrow">VIBRADIAG / 基础分析</p><h1>振动数据工作台</h1><p class="muted">导入数据、核对参数，保存可追溯的分析结果。</p></div><div v-if="identity"><span class="tag">{{ identity.roles.join(' / ') || '只读账号' }}</span><button class="text-button" :disabled="busy" @click="w.requireLogin">重新登录</button><button class="secondary" :disabled="busy" @click="w.logout">退出登录</button></div><span v-else class="tag">本机开发验证</span></header>
  <div class="status formal-status" role="status" aria-live="polite">{{ status }}</div>
  <section v-if="!identity" class="panel login-panel">
    <h2>登录工作台</h2><p class="muted">使用已分配的组织和账号。应用密码与数据库密码不同。</p>
    <form @submit.prevent="signIn"><fieldset :disabled="busy"><label>组织 ID<input v-model="org" required autocomplete="organization" placeholder="组织 UUID"></label><label>用户名<input v-model="username" required autocomplete="username"></label><label>密码<input v-model="password" type="password" required autocomplete="current-password"></label><button type="submit">登录</button></fieldset></form>
  </section>
  <main v-else class="formal-workspace">
    <aside class="record-sidebar"><section class="panel">
      <div class="section-heading"><h2>采集记录</h2><button class="text-button" :disabled="busy" @click="w.refresh">刷新列表</button></div>
      <button v-if="w.writer()" :disabled="busy || !!pending" @click="importing = true; editing = false">导入 CSV</button>
      <p v-if="!records.length" class="muted">当前页没有可访问的记录。</p>
      <button v-for="record in records" :key="record.id" class="record-button" :class="{ active: record.id === selected?.id }" :disabled="busy || !!pending" @click="choose(record.id)"><strong>{{ record.original_filename }}</strong><small>{{ record.validation_summary.sample_count }} 点 · {{ record.id.slice(0, 8) }}</small></button>
      <div class="button-row"><button class="secondary" :disabled="busy || page <= 1" @click="w.turnPage(-1)">上一页</button><span>{{ page }}</span><button class="secondary" :disabled="busy || records.length < 10" @click="w.turnPage(1)">下一页</button></div>
    </section><p class="notice">当前提供基础数值分析，尚未完成轴承故障诊断与工程报告复核。</p></aside>
    <div class="workspace-content">
      <section v-if="pending" class="panel pending-panel"><h2>分析请求待确认</h2><p>重试原请求会查询同一任务，不会重复创建分析。</p><div class="button-row"><button :disabled="busy" @click="w.run()">重试原请求</button><button class="secondary" :disabled="busy" @click="w.stopTracking">停止本页跟踪</button></div><small>停止跟踪不会取消服务端任务，可从分析历史查询。</small></section>
      <section v-if="importing || editing" class="panel"><div class="section-heading"><h2>{{ editing ? '修订采集参数' : '导入振动数据' }}</h2><button class="text-button" :disabled="busy" @click="importing = false; editing = false">关闭表单</button></div><MetadataForm :key="editing ? selected?.current_revision_id : 'new'" :busy="busy" :assets="assets" :initial="editing ? selected?.revision.metadata : undefined" :revision="editing" @save="save" @cancel="editing = false" /></section>
      <section v-if="!selected && !importing" class="panel empty-state"><h2>从一份振动文件开始</h2><p>导入单列 CSV，填写采样率与单位，核对后执行基础分析。</p><button v-if="w.writer()" :disabled="busy" @click="importing = true">开始导入</button></section>
      <template v-if="selected">
        <section class="panel"><div class="section-heading"><h2>{{ selected.original_filename }}</h2><span class="tag">参数 v{{ selected.revision.revision_no }}</span></div>
          <div class="facts"><span>{{ selected.validation_summary.sample_count }} 点</span><span>{{ selected.revision.metadata.sampling_rate_hz }} Hz</span><span>输入 {{ selected.revision.metadata.acceleration_unit }}</span><span>{{ sources[selected.revision.metadata.source_type || 'unknown'] }}</span></div>
          <p class="muted">{{ selected.id }}<br>SHA256：{{ selected.sha256 }}</p>
          <details><summary>本版本采集信息</summary><table><tbody>
            <tr><th>设备 / 测点</th><td>{{ assets.find(a => a.id === selected?.revision.metadata.asset_id)?.name || selected.revision.metadata.asset_id || '未关联' }} / {{ selected.revision.metadata.measurement_point_id || '未指定' }}</td></tr>
            <tr><th>本次转速</th><td>{{ selected.revision.metadata.rpm == null ? '未提供' : selected.revision.metadata.rpm + ' rpm' }}</td></tr>
            <tr><th>方向</th><td>{{ { unknown: '未知', horizontal_radial: '水平径向', vertical_radial: '垂直径向', axial: '轴向', other: '其他' }[selected.revision.metadata.direction || 'unknown'] || selected.revision.metadata.direction }} {{ selected.revision.metadata.direction_note }}</td></tr>
            <tr><th>负载</th><td>{{ selected.revision.metadata.load_value == null ? '未提供数值' : selected.revision.metadata.load_value + ' ' + selected.revision.metadata.load_unit }} {{ selected.revision.metadata.load_note }}</td></tr>
            <tr><th>采集时间</th><td>{{ selected.revision.metadata.acquired_at || '未提供；不以上传时间代替' }}</td></tr>
            <tr><th>来源说明</th><td>{{ selected.revision.metadata.source_note || '未提供' }}；此来源为声明，未作现场真实性或专业校准验证。</td></tr>
          </tbody></table></details>
          <SignalChart :points="rawPoints" label="原始振动预览；横轴时间秒，纵轴为输入加速度单位" /><p class="muted">原始预览 · 时间（s） / {{ selected.preview.unit }} · 分桶保留极值，最多2000点；全量数据用于计算。</p>
          <div class="button-row"><button class="secondary" :disabled="busy" @click="w.download(`/measurements/${selected.id}/original`, selected.id + '.csv')">下载原始 CSV</button><button v-if="w.writer()" class="secondary" :disabled="busy || !!pending" @click="editing = true; importing = false">修订参数</button></div>
          <div v-if="!selected.input_confirmation && w.writer()" class="confirmation"><label class="check-label"><input v-model="accepted" type="checkbox" :disabled="busy">已核对此版本采样率、单位和来源；此确认不代表传感器校准或工程复核。</label><button :disabled="busy || !accepted || !!pending" @click="w.confirm(); accepted = false">确认当前输入</button></div>
          <div v-else-if="selected.input_confirmation" class="button-row"><span class="tag">本人已确认当前输入</span><button v-if="w.writer()" :disabled="busy || !!pending" @click="w.run()">运行基础分析</button></div>
        </section>
        <section class="panel"><h2>参数历史</h2><div class="table-wrap"><table><thead><tr><th>版本</th><th>采样率 / 单位</th><th>修订原因</th></tr></thead><tbody><tr v-for="version in versions" :key="version.id"><td>v{{ version.revision_no }} {{ version.id === selected.current_revision_id ? '（当前）' : '' }}</td><td>{{ version.metadata.sampling_rate_hz }} Hz / {{ version.metadata.acceleration_unit }}</td><td>{{ version.reason }}</td></tr></tbody></table></div><div class="button-row"><button class="secondary" :disabled="busy || versionPage <= 1" @click="w.historyPage('versions', -1)">较新版本</button><span>{{ versionPage }}</span><button class="secondary" :disabled="busy || versions.length < 10" @click="w.historyPage('versions', 1)">更早版本</button></div></section>
        <section class="panel"><h2>分析历史</h2><p v-if="!runs.length" class="muted">当前页没有分析任务。确认输入后可执行基础分析。</p><div class="table-wrap"><table v-if="runs.length"><thead><tr><th>任务</th><th>参数</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="run in runs" :key="run.analysis_id"><td>{{ run.analysis_id.slice(0, 8) }}<small>{{ new Date(run.started_at).toLocaleString() }}</small></td><td>v{{ run.revision_no }}</td><td>{{ stateLabels[run.status] }}</td><td><button class="text-button" :disabled="busy" @click="w.viewResult(run.analysis_id)">查看分析</button></td></tr></tbody></table></div><div class="button-row"><button class="secondary" :disabled="busy || runPage <= 1" @click="w.historyPage('runs', -1)">较新任务</button><span>{{ runPage }}</span><button class="secondary" :disabled="busy || runs.length < 10" @click="w.historyPage('runs', 1)">更早任务</button></div></section>
        <ToolWorkbench :key="identity.user_id + ':' + selected.current_revision_id" :identity="identity" :measurement="selected" :basics="runs" :blocked="baseBusy || comparisonBusy || !!pending" :writer="w.writer()" @busy="toolBusy = $event" @expired="w.requireLogin" />
        <ComparisonWorkbench :key="'comparison:' + identity.user_id + ':' + selected.current_revision_id" :identity="identity" :measurement="selected" :blocked="baseBusy || toolBusy || !!pending" :writer="w.writer()" @busy="comparisonBusy = $event" @expired="w.requireLogin" />
        <section v-if="result" class="panel analysis-panel"><div class="section-heading"><h2>分析结果 · {{ stateLabels[result.status] }}</h2><span class="tag">{{ result.algorithm_version }}</span></div><p class="muted">{{ result.analysis_id }} · 参数 v{{ result.input_snapshot.revision?.revision_no }}</p>
          <p v-if="!result.is_current_revision" class="notice">历史参数结果：已有新版本，此处保留原输入和计算结果。</p><p v-if="!result.input_confirmation_active" class="notice">输入确认已撤销，不能作为当前有效确认继续使用。</p>
          <p v-if="result.error_code" role="alert" class="error">未完成分析：{{ result.error_code }}</p>
          <div v-if="retryable && w.writer()" class="button-row"><button :disabled="busy || !!pending || !selected.input_confirmation" @click="w.run(true)">显式重试（新任务）</button></div>
          <template v-if="result.status === 'completed'">
            <div class="metrics"><div v-for="(metric, name) in result.metrics" :key="name" class="metric"><span>{{ labels[name] || name }}</span><b>{{ number(metric.value) }}</b><small>{{ metric.unit === '1' ? '无量纲' : 'm/s²' }}{{ metric.reason ? ' · ' + (reasonLabels[metric.reason] || metric.reason) : '' }}</small></div></div>
            <p v-for="warning in qualityWarnings" :key="warning" class="notice">{{ warning }}</p>
            <p class="muted">{{ result.result_summary?.sample_count }} 点 · 全文件 · 周期 Hann 窗 · 谱去均值 · 单边峰值幅度<br>bin 间隔 {{ number(result.result_summary?.bin_spacing_hz ?? null) }} Hz，不等于实际频率分辨能力。</p>
            <SignalChart :points="wavePoints" label="分析波形，时间秒 / 加速度m/s²" /><p class="muted">时间（s） / m/s² · {{ waveform?.values.length || 0 }}个展示点</p>
            <SignalChart :points="spectrumPoints" label="单边幅度谱，频率Hz / 峰值幅度m/s²" /><p class="muted">频率（Hz） / 峰值幅度（m/s²） · 全谱 {{ spectrum?.full_bin_count }} 个bin，预览最多2000点。</p>
            <p class="notice">基础分析已完成；尚未进行故障分类、健康判断或工程报告复核。</p>
            <div class="button-row"><button v-for="artifact in result.artifacts" :key="artifact.kind" class="secondary" :disabled="busy" @click="w.download(`/analyses/${result.analysis_id}/artifacts/${artifact.kind}`, artifact.kind + (artifact.kind === 'amplitude_spectrum' ? '.npz' : '.json'))">下载 {{ { result: '结果JSON', waveform_preview: '波形JSON', amplitude_spectrum: '全谱NPZ', manifest: '产物清单' }[artifact.kind] || artifact.kind }}</button></div>
          </template>
        </section>
      </template>
    </div>
  </main>
</template>
