<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import * as api from '../api/formal'
import type { Asset, Metadata } from '../types/formal'
const props = defineProps<{ busy: boolean; assets: Asset[]; initial?: Metadata; revision?: boolean }>()
const emit = defineEmits<{ save: [metadata: Metadata, reason: string, file: File | null]; cancel: [] }>()
const file = ref<File | null>(null), points = ref<Asset[]>([]), error = ref('')
const form = reactive({ rate: '', unit: '', asset: '', point: '', rpm: '', direction: 'unknown', directionNote: '', load: '', loadUnit: '', loadNote: '', acquired: '', source: 'unknown', sourceNote: '', reason: '' })
watch(() => props.initial, value => {
  form.rate = value ? String(value.sampling_rate_hz) : ''; form.unit = value?.acceleration_unit || ''
  form.asset = value?.asset_id || ''; form.point = value?.measurement_point_id || ''
  form.rpm = value?.rpm == null ? '' : String(value.rpm); form.direction = value?.direction || 'unknown'
  form.directionNote = value?.direction_note || ''; form.load = value?.load_value == null ? '' : String(value.load_value)
  form.loadUnit = value?.load_unit || ''; form.loadNote = value?.load_note || ''; form.acquired = value?.acquired_at || ''
  form.source = value?.source_type || 'unknown'; form.sourceNote = value?.source_note || ''; form.reason = ''
}, { immediate: true })
watch(() => form.asset, async id => {
  const previous = form.point; points.value = []
  try { if (id) { const response = await api.points(id); if (id === form.asset) points.value = response.items } }
  catch { error.value = '测点读取失败，请重新选择设备或刷新。' }
  if (id === form.asset && !points.value.some(p => p.id === previous)) form.point = ''
}, { immediate: true })
function pick(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] || null
  form.rate = ''; form.unit = ''; form.source = 'unknown'; form.sourceNote = ''
}
function sample() {
  file.value = new File(['value\n' + Array.from({ length: 1024 }, (_, n) => 2 * Math.sin(2 * Math.PI * 64 * n / 1024)).join('\n') + '\n'], 'synthetic-64hz.csv', { type: 'text/csv' })
  form.rate = '1024'; form.unit = 'm/s2'; form.source = 'synthetic'; form.sourceNote = '浏览器生成的1024点64 Hz正弦；不是现场数据'
}
function save() {
  error.value = ''
  try {
    if (!props.revision && !file.value) throw new Error('请选择CSV文件。')
    if (file.value && file.value.size > 32 * 1024 * 1024) throw new Error('文件不能超过32 MiB。')
    const rate = Number(form.rate)
    if (!String(form.rate).trim() || !Number.isFinite(rate) || rate <= 0 || !['g', 'm/s2'].includes(form.unit)) throw new Error('请填写有限正采样率并选择加速度单位。')
    if (form.acquired && !/(Z|[+-]\d{2}:\d{2})$/i.test(form.acquired)) throw new Error('采集时间请包含时区，例如2026-10-29T10:00:00+08:00。')
    if (props.revision && !form.reason.trim()) throw new Error('请填写参数修订原因。')
    const metadata: Metadata = { sampling_rate_hz: rate, acceleration_unit: form.unit as 'g' | 'm/s2', asset_id: form.asset || null, measurement_point_id: form.point || null,
      rpm: form.rpm !== '' ? Number(form.rpm) : null, direction: form.direction, direction_note: form.directionNote || null,
      load_value: form.load !== '' ? Number(form.load) : null, load_unit: form.loadUnit || null, load_note: form.loadNote || null,
      acquired_at: form.acquired || null, source_type: form.source, source_note: form.sourceNote || null }
    emit('save', metadata, form.reason.trim(), file.value)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '请核对输入。' }
}
</script>

<template>
  <form class="metadata-form" @submit.prevent="save">
    <fieldset :disabled="busy">
      <template v-if="!revision">
        <label>振动 CSV 文件<input type="file" accept=".csv,text/csv" @change="pick"></label>
        <button type="button" class="secondary" @click="sample">使用合成样例</button>
        <p class="muted">{{ file?.name || '单列 value · UTF-8 · 1024—1000000点 · ≤32 MiB' }}</p>
      </template>
      <div class="form-grid">
        <label>采样率（Hz）<input v-model="form.rate" type="number" min="0" step="any" required placeholder="填写实际采样率"></label>
        <label>加速度单位<select v-model="form.unit" required><option value="" disabled>请选择</option><option value="m/s2">m/s²</option><option value="g">g</option></select></label>
        <label>关联设备（可空）<select v-model="form.asset"><option value="">未关联</option><option v-for="asset in assets" :key="asset.id" :value="asset.id">{{ asset.name }}</option></select></label>
        <label>测点（可空）<select v-model="form.point" :disabled="!form.asset"><option value="">未指定</option><option v-for="point in points" :key="point.id" :value="point.id">{{ point.name }}</option></select></label>
        <label>本次转速（rpm，可空）<input v-model="form.rpm" type="number" step="any" min="0" placeholder="未知则留空"></label>
        <label>采集方向<select v-model="form.direction"><option value="unknown">未知</option><option value="horizontal_radial">水平径向</option><option value="vertical_radial">垂直径向</option><option value="axial">轴向</option><option value="other">其他</option></select></label>
      </div>
      <label v-if="form.direction === 'other'">方向说明<input v-model="form.directionNote" maxlength="500" required></label>
      <details><summary>补充采集信息（可空）</summary>
        <div class="form-grid"><label>负载数值<input v-model="form.load" type="number" step="any"></label><label>负载单位<input v-model="form.loadUnit" maxlength="64" :required="String(form.load) !== ''"></label></div>
        <label>负载说明<input v-model="form.loadNote" maxlength="1000"></label>
        <label>采集时间（含时区）<input v-model="form.acquired" placeholder="2026-10-29T10:00:00+08:00"></label>
      </details>
      <label>数据来源<select v-model="form.source"><option value="unknown">未知 / 未核实</option><option value="synthetic">合成数据</option><option value="public_dataset">公开数据集</option><option value="field_upload">现场上传（来源声明）</option></select></label>
      <label>来源说明<input v-model="form.sourceNote" maxlength="2000" placeholder="可填写数据集名称、生成方式或采集说明"></label>
      <label v-if="revision">修订原因<input v-model="form.reason" maxlength="1000" required></label>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <div class="button-row"><button type="submit">{{ revision ? '保存新参数版本' : '上传并校验' }}</button><button v-if="revision" type="button" class="secondary" @click="emit('cancel')">取消修订</button></div>
    </fieldset>
  </form>
</template>
