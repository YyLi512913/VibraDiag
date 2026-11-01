<script setup lang="ts">
import { ref } from 'vue'
import FormalWorkbench from './components/FormalWorkbench.vue'
import PrototypeWorkbench from './PrototypeWorkbench.vue'
import SessionGate from './components/SessionGate.vue'
import AgentTaskWorkbench from './features/agent-tasks/AgentTaskWorkbench.vue'
const view = ref<'data' | 'tasks' | 'prototype'>('data')
</script>
<template>
  <nav class="mode-bar" aria-label="工作区"><button :class="{ selected: view === 'data' }" @click="view = 'data'">数据分析</button><button :class="{ selected: view === 'tasks' }" @click="view = 'tasks'">Agent 任务</button><button :class="{ selected: view === 'prototype' }" @click="view = 'prototype'">模型实验（开发）</button><span>VibraDiag · M4开发版</span></nav>
  <PrototypeWorkbench v-if="view === 'prototype'" />
  <SessionGate v-else><AgentTaskWorkbench v-if="view === 'tasks'" /><FormalWorkbench v-else /></SessionGate>
</template>
