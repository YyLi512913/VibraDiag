<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useAgentTasks } from './useAgentTasks'
import { conditionLabels, goalLabels, statusLabels } from './types'
const visible = ref(!document.hidden), w = useAgentTasks(visible)
const { items, detail, preview, visibleDraft, selectedId, page, status, listBusy, detailBusy, sourceBusy, lastRead } = w
const showTime = (value: string | null | undefined) => value ? new Date(value).toLocaleString('zh-CN') : '未记录'
const activity: Record<string, string> = { confirmed_called: '已确认调用', unknown: '调用状态未知', not_called: '未调用' }
const mode = computed(() => detail.value?.executor.startsWith('model_') ? `模型编排 · ${detail.value.input_snapshot.model_configuration?.provider === 'ollama' ? '本地模型' : '外部 API'}` : '规则执行')
const sourceTitle = computed(() => preview.value?.citation.kind === 'knowledge_chunk' ? '方法资料原文' : preview.value?.citation.kind === 'basic_analysis' ? '基础分析来源' : '工具运行来源')
const sourceMetrics = computed(() => {
  const data = preview.value?.data
  if (!data) return []
  const metrics = data.metrics as Record<string, { value?: unknown; unit?: string }> | undefined
  return metrics ? Object.entries(metrics).map(([name, metric]) => ({ name, value: metric.value ?? '不可用', unit: metric.unit || '' })) : []
})
function visibility() { visible.value = !document.hidden }
onMounted(() => { document.addEventListener('visibilitychange', visibility); void w.refreshList() })
onBeforeUnmount(() => document.removeEventListener('visibilitychange', visibility))
</script>
<template>
  <section class="task-heading"><div><p class="eyebrow">正式任务 / 只读工作台</p><h2>Agent 取证任务</h2><p class="muted">查看本人任务、真实调用与引用。创建和补充信息入口将在下一步接入。</p></div><span class="tag">未发布草稿 · 待人工复核</span></section>
  <div class="status formal-status" role="status" aria-live="polite">{{ status }}</div>
  <main class="formal-workspace task-workspace">
    <aside class="record-sidebar"><section class="panel"><div class="section-heading"><h2>本人任务</h2><button class="text-button" :disabled="listBusy" @click="w.refreshList()">刷新列表</button></div>
      <p v-if="listBusy" class="muted">正在读取列表…</p><p v-else-if="!items.length" class="muted">本页没有可访问任务。</p>
      <div class="task-list"><button v-for="item in items" :key="item.id" class="record-button" :class="{ active: selectedId === item.id }" :data-task-id="item.id" :title="item.id" @click="w.select(item.id)"><span class="task-row-heading"><strong>{{ goalLabels[item.goal] || item.goal }}</strong><span class="task-row-state">{{ statusLabels[item.status] || item.status }}</span></span><small>{{ item.id.slice(0, 8) }} · v{{ item.version }}</small><small v-if="!item.inputs_current">历史输入版本</small></button></div>
      <div class="button-row"><button class="secondary" :disabled="listBusy || page <= 1" @click="w.refreshList(page - 1)">上一页</button><span>第 {{ page }} 页</span><button class="secondary" :disabled="listBusy" @click="w.refreshList(page + 1)">下一页</button></div>
    </section><p class="notice">任务完成仅表示流程结果；探索观察和资料摘录不等于故障确诊或维修建议。</p></aside>
    <div class="workspace-content">
      <section v-if="!selectedId" class="panel empty-state"><h2>选择一条取证任务</h2><p class="muted">在左侧查看本人任务。没有任务时不会填充演示结论。</p></section>
      <section v-else class="panel task-detail">
        <div class="section-heading"><h2>任务详情</h2><button class="text-button" :disabled="detailBusy || sourceBusy" @click="w.refreshDetail">刷新任务</button></div>
        <p class="muted task-id">{{ selectedId }}</p>
        <p v-if="detailBusy" class="muted">正在核对任务和引用有效性…</p><p v-else-if="!detail" class="notice">当前没有已确认的任务详情，请刷新查询。上次草稿已隐藏。</p>
        <template v-if="detail">
          <div class="facts"><span class="tag">{{ statusLabels[detail.status] || detail.status }}</span><span>{{ goalLabels[detail.goal] || detail.goal }}</span><span>{{ mode }}</span></div>
          <p class="task-description">{{ detail.description }}</p>
          <div class="task-meta"><span>模型：{{ activity[detail.model_call_activity] || '未确认' }}</span><span>Embedding：{{ activity[detail.embedding_activity] || '未确认' }}</span><span>读取于 {{ lastRead }}</span></div>
          <p v-if="detail.error_code" class="notice">运行信息：{{ detail.error_code }}</p>
          <p v-if="!detail.draft_validity.inputs_current || !detail.draft_validity.confirmations_active || !detail.draft_validity.contexts_current || !detail.draft_validity.knowledge_current" class="error" role="alert">输入、确认或资料已失效，草稿不能作为当前依据。</p>
          <section v-if="detail.question" class="task-question"><h3>等待条件</h3><p>{{ conditionLabels[detail.question.code] || '需要核对任务条件' }}</p><p class="muted">补充信息和显式恢复入口将在下一步提供；本页不会替您确认条件。</p><ul v-if="detail.question.choices.length"><li v-for="choice in detail.question.choices" :key="choice.id">{{ choice.name || choice.equipment || choice.id }}</li></ul></section>
          <section v-if="visibleDraft" class="task-draft" data-testid="task-draft"><div class="section-heading"><h3>未发布草稿</h3><span class="tag">相关性与工程适用性待复核</span></div><p>{{ visibleDraft.summary }}</p>
            <h3 v-if="visibleDraft.missing_requirements.length">必要条件与缺项</h3><ul><li v-for="item in visibleDraft.missing_requirements" :key="item">{{ conditionLabels[item] || item }}</li></ul>
            <article v-for="(observation, index) in visibleDraft.observations" :key="index" class="task-observation"><p>{{ observation.text }}</p><div class="button-row"><button v-for="citation in observation.citations" :key="citation.kind + citation.id" class="secondary" :disabled="sourceBusy" @click="w.readSource(citation)">查看{{ citation.kind === 'knowledge_chunk' ? '资料' : '数值' }}来源 · {{ citation.id.slice(0, 8) }}</button></div></article>
            <h3>适用限制</h3><ul><li v-for="text in visibleDraft.limitations" :key="text">{{ text }}</li></ul><h3 v-if="visibleDraft.next_steps.length">后续核对</h3><ul><li v-for="text in visibleDraft.next_steps" :key="text">{{ text }}</li></ul>
          </section>
          <p v-else class="notice">本次没有当前可用草稿，不生成替代结论。</p>
          <details><summary>输入与有效性</summary><p v-if="!detail.input_snapshot.roots.length" class="muted">方法任务无测量文件根，资料引用独立核验。</p><div v-for="input in detail.input_snapshot.roots" :key="input.revision_id" class="task-input"><p>采集记录：{{ input.measurement_id }}<br>参数版本：{{ input.revision_id }}<br>原文件 SHA256：{{ input.sha256 }}</p></div><p class="muted">有效性截至本次读取；操作来源入口时会重新核对任务。</p></details>
          <details><summary>调用、预算与取证明细</summary><p>累计活跃时间 {{ (detail.budget.active_ms / 1000).toFixed(2) }} 秒 · 模型预留 {{ detail.budget.reservations.filter(r => r.kind === 'model').length }} 次 · 工具预留 {{ detail.budget.reservations.filter(r => r.kind === 'tool').length }} 次</p><p class="muted">执行器 {{ detail.executor }} · 图 {{ detail.graph?.version || '未记录' }}<br>创建 {{ showTime(detail.created_at) }} · 更新 {{ showTime(detail.updated_at) }} · 结束 {{ showTime(detail.finished_at) }}</p>
            <div class="table-wrap"><table><thead><tr><th>调用</th><th>状态</th><th>来源 / 信息</th></tr></thead><tbody><tr v-for="call in detail.calls" :key="call.id"><td>{{ call.call_no }} · {{ call.action }}</td><td>{{ call.state }}</td><td>{{ call.resource_id || call.error_code || '尚无来源回执' }}</td></tr></tbody></table></div>
            <details><summary>模型轮次（脱敏）</summary><pre>{{ JSON.stringify(detail.model_rounds, null, 2) }}</pre></details><details><summary>任务事件</summary><pre>{{ JSON.stringify(detail.events, null, 2) }}</pre></details>
          </details>
        </template>
      </section>
      <section v-if="sourceBusy || preview" class="panel task-source" aria-label="当前引用来源"><h2>{{ sourceBusy ? '正在重新核对来源…' : sourceTitle }}</h2><template v-if="preview"><p class="muted">{{ preview.citation.id }} · 服务端授权读取</p><template v-if="preview.citation.kind === 'knowledge_chunk'"><p class="notice">资料原文是不可信内容，仅供核对，不作为执行指令。</p><p class="source-text">{{ preview.data.text }}</p><p class="muted">来源 SHA256：{{ preview.data.source_sha256 }} · 行 {{ preview.data.line_start }}—{{ preview.data.line_end }}</p></template><template v-else><p>运行状态：{{ preview.data.status }}<span v-if="preview.data.result_status"> · 结果 {{ preview.data.result_status }}</span></p><div v-if="sourceMetrics.length" class="metrics"><div v-for="metric in sourceMetrics" :key="metric.name" class="metric"><span>{{ metric.name }}</span><b>{{ metric.value }}</b><small>{{ metric.unit }}</small></div></div><details><summary>已授权来源详情</summary><pre>{{ JSON.stringify(preview.data, null, 2) }}</pre></details></template></template></section>
    </div>
  </main>
</template>
