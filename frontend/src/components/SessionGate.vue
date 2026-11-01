<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useSession } from '../composables/useSession'
const s = useSession()
const { identity, busy, status } = s
const org = ref(localStorage.getItem('vibradiag-org') || ''), username = ref(localStorage.getItem('vibradiag-username') || ''), password = ref('')
async function signIn() {
  try {
    await s.login(org.value.trim(), username.value.trim(), password.value)
    if (identity.value) { localStorage.setItem('vibradiag-org', org.value.trim()); localStorage.setItem('vibradiag-username', username.value.trim()) }
  } finally { password.value = '' }
}
onMounted(s.restore)
</script>
<template>
  <header class="formal-header session-header">
    <div><p class="eyebrow">VIBRADIAG / 旋转机械振动分析</p><h1>诊断辅助工作台</h1><p class="muted">探索分析与可追溯取证 · 当前不发布工程诊断结论</p></div>
    <div v-if="identity"><span class="tag">{{ identity.roles.join(' / ') || '只读账号' }}</span><span class="muted session-actor">账号 {{ identity.user_id.slice(0, 8) }}</span><button class="secondary" :disabled="busy" @click="s.logout">退出登录</button></div>
    <span v-else class="tag">本机受限软件版</span>
  </header>
  <section v-if="!identity" class="panel login-panel">
    <h2>登录工作台</h2><p class="muted">使用已分配的组织和账号。应用密码与数据库密码不同。</p>
    <p role="status" aria-live="polite">{{ status }}</p>
    <form @submit.prevent="signIn"><fieldset :disabled="busy"><label>组织 ID<input v-model="org" required autocomplete="organization" placeholder="组织 UUID"></label><label>用户名<input v-model="username" required autocomplete="username"></label><label>密码<input v-model="password" type="password" required autocomplete="current-password"></label><button type="submit">登录</button></fieldset></form>
  </section>
  <slot v-else />
</template>
