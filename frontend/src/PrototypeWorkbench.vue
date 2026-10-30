<script setup lang="ts">
import { onMounted, ref } from 'vue'
import DataImportPanel from './components/DataImportPanel.vue'
import AgentPanel from './components/AgentPanel.vue'
import KnowledgeSearchPanel from './components/KnowledgeSearchPanel.vue'
import AnalysisResults from './components/AnalysisResults.vue'
import AgentResult from './components/AgentResult.vue'
import { useWorkbench } from './composables/useWorkbench'

const { analysis, diagnosis, hits, uploading, modelBusy, status, restore, upload, run, search } = useWorkbench()
const embeddingModel = ref('bge-m3:567m')
onMounted(restore)
</script>

<template>
  <header><div><h1>VibraDiag <small>设备听诊台</small></h1><small>数据 → 工具 → 证据 → 待复核草稿</small></div><span class="muted">独立前端 · 本机开发原型</span></header>
  <main>
    <aside>
      <div class="notice">当前验证基础分析、模型工具调用与开发知识检索。尚未实现包络定类、共享权限及工单；不能用于现场维修决策。</div>
      <DataImportPanel :busy="uploading || modelBusy" @submit="upload" />
      <AgentPanel v-model:embedding-model="embeddingModel" :available="!!analysis && !uploading" :busy="modelBusy" @submit="run" />
      <KnowledgeSearchPanel :busy="modelBusy" :hits="hits" @search="query => search(query, embeddingModel)" />
    </aside>
    <div><div class="status" role="status">{{ status }}</div><AnalysisResults :analysis="analysis" /><AgentResult :result="diagnosis" /></div>
  </main>
</template>
