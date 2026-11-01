<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useSession } from '../../composables/useSession'
import { createTaskCommands } from './useTaskCommands'
import TaskCreator from './TaskCreator.vue'
import type { TaskDetail } from './types'
const props = defineProps<{ task: TaskDetail | null; reading: boolean }>()
const emit = defineEmits<{ accepted: [task: TaskDetail]; refresh: [] }>()
const session = useSession(), commands = createTaskCommands(session, async task => { emit('accepted', task) })
const { pending, busy, status, rejected, writer, storageBlocked } = commands
const answer = ref(''), checked = ref(false), cancelChecked = ref(false), rejectionChecked = ref(false)
const terminal = new Set(['completed','completed_degraded','refused','failed','cancelled','superseded','budget_exhausted','timed_out'])
const resumable = computed(() => !!props.task?.question && ['waiting_information','interrupted'].includes(props.task.status))
const blocked = computed(() => busy.value || !!pending.value || storageBlocked.value)
let lastTask = ''
watch(() => props.task, task => { if (task && `${task.id}:${task.version}` !== lastTask) { lastTask = `${task.id}:${task.version}`; answer.value = ''; checked.value = false; cancelChecked.value = false } })
function resume() {
  const task = props.task
  if (!task?.question || !resumable.value || !checked.value || !answer.value.trim() || props.reading) return
  void commands.submit({ kind:'resume', taskId:task.id, body:{ expected_version:task.version, question_id:task.question.id, answer:answer.value.trim() } })
}
function cancel() {
  const task = props.task
  if (!task || terminal.has(task.status) || !cancelChecked.value || props.reading) return
  void commands.submit({ kind:'cancel', taskId:task.id, body:{expected_version:task.version} })
}
onBeforeUnmount(commands.dispose)
</script>
<template>
  <section class="task-commands" aria-label="任务操作">
    <p v-if="!writer" class="notice">当前账号没有创建、恢复或取消任务的权限。</p>
    <p v-if="status" class="status" role="status" aria-live="polite">{{ status }}</p>
    <section v-if="pending" class="panel pending-panel"><h3>命令响应待核对 · {{ pending.command.kind }}</h3><p class="notice">重放原内容使用同一幂等键。切页或退出不会取消已受理任务；本标签页缓存按账号隔离保留。</p><details><summary>核对原命令内容</summary><pre>{{ JSON.stringify(pending.command, null, 2) }}</pre></details><div class="button-row"><button :disabled="busy || !writer || storageBlocked" @click="commands.replay">重放原命令</button><button class="secondary" :disabled="busy" @click="emit('refresh')">查询当前任务状态</button></div>
      <template v-if="rejected"><p class="muted">服务端已返回拒绝。请先查询当前状态，再决定是否结束跟踪；不自动生成新请求。</p><label class="check-label"><input v-model="rejectionChecked" type="checkbox" :disabled="busy">已核对当前任务或输入及错误，确认结束被拒命令跟踪。</label><button class="secondary" :disabled="busy || !rejectionChecked" @click="commands.acknowledgeRejection(); rejectionChecked = false">结束被拒命令跟踪</button></template>
    </section>
    <TaskCreator v-if="writer" :blocked="blocked" @create="commands.submit({kind:'create',body:$event})" />
    <section v-if="writer && task && !terminal.has(task.status)" class="panel"><h3>当前任务操作 · v{{ task.version }}</h3><p class="muted task-id">{{ task.id }}</p>
      <form v-if="resumable" @submit.prevent="resume"><fieldset :disabled="blocked || reading"><p class="notice">回答只响应当前问题，不会修改采集参数、确认输入或授权维修。缺少确认或参数时，请先在数据页面完成相应操作再显式恢复。</p>
        <label v-if="task.question?.choices.length">当前问题允许的选择<select v-model="answer" required><option value="">请选择</option><option v-for="choice in task.question.choices" :key="choice.id" :value="choice.id">{{ choice.name || choice.equipment || choice.id }}</option></select></label>
        <label v-else>本次回答或恢复说明<textarea v-model="answer" required maxlength="4000" placeholder="说明已补充或核对的内容；方法问题可在此补充明确查询。"></textarea></label>
        <label class="check-label"><input v-model="checked" type="checkbox">确认以当前问题和版本显式恢复本任务。</label><button :disabled="!checked || !answer.trim()" type="submit">回答并恢复任务</button>
      </fieldset></form>
      <fieldset :disabled="blocked || reading"><label class="check-label"><input v-model="cancelChecked" type="checkbox">确认取消当前任务；已产生的调用和预算记录仍保留。</label><button class="secondary" :disabled="!cancelChecked" @click="cancel">取消当前任务</button></fieldset>
    </section>
  </section>
</template>
