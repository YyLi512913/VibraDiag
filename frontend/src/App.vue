<script setup lang="ts">
import { ref } from 'vue'
import FormalWorkbench from './components/FormalWorkbench.vue'
import PrototypeWorkbench from './PrototypeWorkbench.vue'
const experimental = ref(false)
</script>
<template>
  <nav class="mode-bar" aria-label="工作区"><button :class="{ selected: !experimental }" @click="experimental = false">数据分析工作台</button><button :class="{ selected: experimental }" @click="experimental = true">模型实验（开发）</button><span>VibraDiag · M1开发版</span></nav>
  <PrototypeWorkbench v-if="experimental" /><FormalWorkbench v-else />
</template>
