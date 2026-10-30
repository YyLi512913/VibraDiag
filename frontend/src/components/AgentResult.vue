<script setup lang="ts">
import type { Diagnosis } from '../types/api'
defineProps<{ result: Diagnosis | null }>()
const statusNames = { analysis_only: '基础分析说明', needs_information: '需要补充信息', insufficient_evidence: '证据不足' }
</script>

<template>
  <section class="panel">
    <h2>Agent 草稿与实际工具轨迹</h2>
    <p class="muted">结构和引用校验通过仅代表格式可用，内容仍需人工复核。</p>
    <p v-if="!result">尚未运行。</p>
    <template v-else>
      <p class="muted">{{ result.model }} · {{ result.wall_seconds.toFixed(2) }} 秒</p>
      <p v-if="result.error" class="notice">{{ result.error }}</p>
      <template v-if="result.draft">
        <strong>{{ statusNames[result.draft.status] }}</strong><p>{{ result.draft.summary }}</p>
        <ul><li v-for="(item, i) in result.draft.observations" :key="i">{{ item.text }} <small>{{ item.evidence_ids.join(', ') }}</small></li></ul>
        <h3 v-if="result.draft.missing_information.length">待补充</h3><ul><li v-for="item in result.draft.missing_information" :key="item">{{ item }}</li></ul>
        <h3 v-if="result.draft.next_steps.length">下一步建议</h3><ul><li v-for="item in result.draft.next_steps" :key="item">{{ item }}</li></ul>
      </template>
      <details><summary>实际工具与校验详情</summary><pre>{{ JSON.stringify({ trace: result.trace, metrics: result.metrics }, null, 2) }}</pre></details>
    </template>
  </section>
</template>
