<script setup lang="ts">
defineProps<{ busy: boolean }>()
const emit = defineEmits<{ submit: [form: FormData] }>()
function submit(event: Event) { emit('submit', new FormData(event.target as HTMLFormElement)) }
</script>

<template>
  <section class="panel">
    <h2>1 / 导入振动数据</h2>
    <a href="/api/v1/samples/sine.csv">下载已知 128 Hz 合成测试信号</a>
    <form @submit.prevent="submit">
      <label>CSV（单列 value）<input type="file" name="file" accept=".csv" required /></label>
      <label>采样率 / Hz<input name="sampling_rate" type="number" min="0.0001" step="any" placeholder="请填写；测试信号为4096" required /></label>
      <label>加速度单位<select name="unit" required><option value="">请选择</option><option value="m/s2">m/s²</option><option value="g">g</option></select></label>
      <button :disabled="busy">{{ busy ? '分析中…' : '校验并分析' }}</button>
    </form>
  </section>
</template>
