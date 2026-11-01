export type TaskStatus = 'queued' | 'running' | 'waiting_information' | 'completed' | 'completed_degraded' | 'refused' | 'failed' | 'cancelled' | 'interrupted' | 'superseded' | 'budget_exhausted' | 'timed_out'
export type Goal = 'inspect_signal' | 'bearing_observations' | 'compare_measurements' | 'explain_method'
export interface TaskRow { id: string; goal: Goal; status: TaskStatus; version: number; inputs_current: boolean }
export interface Citation { kind: string; id: string }
export interface Draft {
  publication_state: 'unpublished_draft'; status: string; summary: string
  observations: { claim_kind: string; text: string; citations: Citation[] }[]
  missing_requirements: string[]; limitations: string[]; next_steps: string[]
}
export interface TaskDetail extends Omit<TaskRow, 'inputs_current'> {
  description: string; executor: string; error_code: string | null
  created_at: string; updated_at: string; finished_at: string | null
  question: { code: string; choices: { id: string; [key: string]: unknown }[]; selection_field: string | null } | null
  draft: Draft | null
  draft_validity: { inputs_current: boolean; confirmations_active: boolean; contexts_current: boolean; knowledge_current: boolean; usable: boolean }
  input_snapshot: { roots: { revision_id: string; measurement_id: string; sha256: string }[]; model_configuration?: { provider: string; model: string } }
  budget: { active_ms: number; reservations: { kind: 'tool' | 'model'; key: string; retries_used?: number }[] }
  calls: { id: string; call_no: number; action: string; state: string; resource_kind: string | null; resource_id: string | null; error_code: string | null }[]
  model_rounds: { id: string; phase: string; state: string; provider: string; requested_model: string; error_code: string | null; trace: unknown; validation_diagnostics: unknown }[]
  graph: { version: string; checkpoint_count: number } | null
  model_called: boolean; embedding_called: boolean
  model_call_activity: string; embedding_activity: string
  events: { task_version: number; kind: string; created_at: string; details: unknown }[]
}
export interface SourcePreview { citation: Citation; data: Record<string, unknown> }
export const statusLabels: Record<TaskStatus, string> = { queued: '排队中', running: '执行中', waiting_information: '等待补充信息', completed: '流程已完成', completed_degraded: '受限完成', refused: '无法支持本次请求', failed: '执行失败', cancelled: '已取消', interrupted: '已中断', superseded: '输入已被替代', budget_exhausted: '预算耗尽', timed_out: '执行超时' }
export const goalLabels: Record<Goal, string> = { inspect_signal: '基础信号检查', bearing_observations: '轴承探索观察', compare_measurements: '双数据比较', explain_method: '方法资料查询' }
export const activeStates = new Set<TaskStatus>(['queued', 'running'])
export const conditionLabels: Record<string, string> = {
  input_confirmation_required: '需要人工确认当前输入', before_input_confirmation_required: '需要确认比较前的输入',
  knowledge_scope_required: '需要明确资料适用范围', knowledge_query_required: '需要补充有效的方法问题',
  profile_required: '需要选择数值配置', target_context_required: '需要明确部件与工况',
  source_uncertainty_required: '需要补充参数来源与不确定量', execution_retry_required: '执行中断，等待显式恢复',
  session_reauthentication_required: '会话已失效，需要重新登录', knowledge_retrieval_retry_required: '资料检索中断，等待显式恢复',
  diagnostic_profile_not_qualified: '诊断配置尚未通过资格验证', knowledge_relevance_not_qualified: '资料相关性与工程适用性仍待复核',
  geometry_uncertainty: '轴承几何参数的不确定量未明确',
  published_knowledge_not_found_or_no_longer_available: '没有当前可用的已发布资料', model_sources_not_sufficient: '检索资料无法支持本次问题',
  model_decision_unavailable: '模型决策未通过校验或调用未完成', matching_parents_or_uncertainty_incomplete: '匹配所需父结果或不确定量不完整',
}
