<script setup lang="ts">
import { ref } from 'vue'
import type { DiagnoseInput } from '../types/api'
defineProps<{ available: boolean; busy: boolean }>()
const emit = defineEmits<{ submit: [input: Omit<DiagnoseInput, 'analysis_id'>] }>()
const embeddingModel = defineModel<string>('embeddingModel', { required: true })
const description = ref('请检查这段信号，是否足以判断轴承内圈或外圈故障？')
const rpm = ref('')
const model = ref('deepseek/deepseek-flash')
function submit() {
  emit('submit', { description: description.value, rpm: rpm.value ? Number(rpm.value) : null, model: model.value, embedding_model: embeddingModel.value })
}
</script>

<template>
  <section class="panel">
    <h2>2 / Agent 取证</h2>
    <p class="muted">DeepSeek 接收问题、参数及必要特征/知识摘要；原始 CSV 和完整波形不默认外发。Embedding 仍在本机运行。</p>
    <form @submit.prevent="submit">
      <label>现象与问题<textarea v-model="description" required maxlength="1000" /></label>
      <label>本次采集转速 / rpm（未知可留空）<input v-model="rpm" type="number" min="0.0001" step="any" /></label>
      <label>Agent 模型<select v-model="model"><option value="deepseek/deepseek-flash">DeepSeek Flash · API</option><option value="qwen3.5:2b">Qwen3.5 2B · 本地对照</option><option value="qwen3.5:0.8b">Qwen3.5 0.8B · 本地对照</option></select></label>
      <label>检索模型<select v-model="embeddingModel"><option>bge-m3:567m</option><option>qwen3-embedding:0.6b</option></select></label>
      <button :disabled="!available || busy">{{ busy ? '模型处理中…' : '根据问题选择工具' }}</button>
    </form>
  </section>
</template>
