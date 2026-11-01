<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import * as api from '../../api/formal'
import { useSession } from '../../composables/useSession'
import type { Measurement, RecordSummary } from '../../types/formal'
import type { ComparisonOption } from '../../types/comparison'
import { goalLabels, type CreateTask, type Goal } from './types'
const props = defineProps<{ blocked: boolean }>()
const emit = defineEmits<{ create: [input: CreateTask] }>()
const session = useSession(), goal = ref<Goal>('inspect_signal'), description = ref('')
const records = ref<RecordSummary[]>([]), page = ref(1), selected = ref(''), measurement = ref<Measurement | null>(null)
const comparisons = ref<ComparisonOption[]>([]), declaration = ref(''), busy = ref(false), status = ref('请读取当前授权采集记录。')
const equipment = ref<'motor'|'pump'|'fan'|'rotating_generic'>('rotating_generic'), component = ref<'bearing'|'rotating_assembly'|'general'>('general')
const accepted = ref(false)
let serial = 0, disposed = false
const ready = computed(() => !props.blocked && !busy.value && accepted.value && !!description.value.trim() && (goal.value === 'explain_method' || !!measurement.value && (goal.value !== 'compare_measurements' || comparisons.value.some(c => c.id === declaration.value))))
async function load(target = page.value) {
  if (busy.value || props.blocked) return
  const number = ++serial, revision = session.revision.value; busy.value = true; accepted.value = false
  records.value = []; measurement.value = null; selected.value = ''; comparisons.value = []; declaration.value = ''
  try { const data = await api.records(target); if (!disposed && number === serial && revision === session.revision.value) { records.value = data.items; page.value = data.page; status.value = '选择一份采集，绑定服务端返回的当前版本。' } }
  catch (e) { if (!disposed && number === serial && revision === session.revision.value) { if (e instanceof api.ApiError && e.status === 401) session.invalidate(); else status.value = e instanceof Error ? e.message : '读取失败。' } }
  finally { if (!disposed && number === serial && revision === session.revision.value) busy.value = false }
}
async function choose() {
  const id = selected.value, number = ++serial, revision = session.revision.value
  measurement.value = null; comparisons.value = []; declaration.value = ''; accepted.value = false
  if (!id) return
  busy.value = true
  try {
    const data = await api.measurement(id)
    const options = await api.get<{ items: ComparisonOption[] }>(`/tool-runs/comparison-options/${encodeURIComponent(id)}`)
    if (!disposed && number === serial && revision === session.revision.value) { measurement.value = data; comparisons.value = options.items; status.value = '输入版本已读取；若参数有变化，请重新读取，不会沿用旧确认。' }
  } catch (e) { if (!disposed && number === serial && revision === session.revision.value) { if (e instanceof api.ApiError && e.status === 401) session.invalidate(); else status.value = e instanceof Error ? e.message : '读取失败。' } }
  finally { if (!disposed && number === serial && revision === session.revision.value) busy.value = false }
}
function submit() {
  if (!ready.value) return
  const input: CreateTask = { goal: goal.value, description: description.value.trim() }
  if (goal.value === 'explain_method') input.knowledge_scope = { equipment: equipment.value, component: component.value }
  else { input.measurement_revision_id = measurement.value!.current_revision_id; if (goal.value === 'compare_measurements') input.comparison_declaration_id = declaration.value }
  accepted.value = false; emit('create', input)
}
onBeforeUnmount(() => { disposed = true; serial++ })
</script>
<template>
  <details class="panel task-create"><summary>创建取证任务</summary>
    <form @submit.prevent="submit"><fieldset :disabled="blocked || busy">
      <label>任务目标<select v-model="goal" @change="accepted = false"><option v-for="(label, value) in goalLabels" :key="value" :value="value">{{ label }}</option></select></label>
      <label>问题与背景<textarea v-model="description" required maxlength="4000" placeholder="描述要核对的问题、已知条件与疑点；描述不会代替输入确认。" @input="accepted = false"></textarea></label>
      <template v-if="goal !== 'explain_method'">
        <div class="button-row"><button type="button" class="secondary" @click="load()">读取采集记录</button><button type="button" class="secondary" :disabled="page <= 1" @click="load(page - 1)">上一批采集</button><span>第 {{ page }} 页</span><button type="button" class="secondary" @click="load(page + 1)">下一批采集</button></div>
        <p class="muted" role="status">{{ status }}</p>
        <label>任务输入<select v-model="selected" @change="choose"><option value="">选择授权采集</option><option v-for="r in records" :key="r.id" :value="r.id">{{ r.original_filename }} · {{ r.id.slice(0,8) }}</option></select></label>
        <div v-if="measurement" class="task-input"><p>当前版本 {{ measurement.current_revision_id }}<br>采样率 {{ measurement.revision.metadata.sampling_rate_hz }} Hz · 单位 {{ measurement.revision.metadata.acceleration_unit }}</p><p class="notice">{{ measurement.input_confirmation ? '当前输入已有本人的确认；服务端仍将重新核对。' : '当前输入未确认。任务可能等待人工确认，请到数据分析页面核对；创建任务不会自动确认。' }}</p></div>
        <label v-if="goal === 'compare_measurements'">已登记比较依据<select v-model="declaration" @change="accepted = false"><option value="">选择绑定当前采集的比较依据</option><option v-for="o in comparisons" :key="o.id" :value="o.id">{{ o.before_filename }} → 当前采集 · {{ o.reason }}</option></select></label>
        <p v-if="goal === 'compare_measurements' && !comparisons.length" class="muted">没有已登记比较依据；请先完成两份输入及比较条件的登记。</p>
      </template>
      <div v-else class="form-grid"><label>资料设备范围<select v-model="equipment" @change="accepted = false"><option value="rotating_generic">通用旋转机械</option><option value="motor">电机</option><option value="pump">泵</option><option value="fan">风机</option></select></label><label>资料部件范围<select v-model="component" @change="accepted = false"><option value="general">通用</option><option value="bearing">轴承</option><option value="rotating_assembly">旋转总成</option></select></label></div>
      <label class="check-label"><input v-model="accepted" type="checkbox">已核对任务目标、输入版本或资料范围；结果仍为待复核的探索草稿。</label>
      <button :disabled="!ready" type="submit">提交取证任务</button>
    </fieldset></form>
  </details>
</template>
