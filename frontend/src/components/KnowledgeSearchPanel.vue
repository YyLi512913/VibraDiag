<script setup lang="ts">
import { ref } from 'vue'
import type { SearchHit } from '../types/api'
defineProps<{ busy: boolean; hits: SearchHit[] }>()
const emit = defineEmits<{ search: [query: string] }>()
const query = ref('')
</script>

<template>
  <section class="panel">
    <h2>独立知识检索</h2>
    <form @submit.prevent="emit('search', query)">
      <input v-model="query" aria-label="知识检索问题" placeholder="例如：缺少转速还能做什么分析？" maxlength="200" required />
      <button :disabled="busy">检索开发知识卡片</button>
    </form>
    <article v-for="hit in hits" :key="hit.id" class="knowledge-hit">
      <strong>{{ hit.id }} · {{ hit.title }}</strong><small>相关性 {{ hit.score.toFixed(3) }}</small>
      <p>{{ hit.text }}</p><small>来源：{{ hit.source }}</small>
    </article>
  </section>
</template>
