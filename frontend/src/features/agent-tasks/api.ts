import { get, post } from '../../api/formal'
import type { Page } from '../../types/formal'
import type { Citation, TaskCommand, TaskDetail, TaskRow } from './types'

export const command = (input: TaskCommand, key: string, signal: AbortSignal) => post<TaskDetail>(input.kind === 'create' ? '/agent-tasks' : `/agent-tasks/${encodeURIComponent(input.taskId)}/${input.kind}`, input.body, { 'Idempotency-Key': key }, signal)

export const list = (page: number, signal: AbortSignal) => get<Page<TaskRow>>(`/agent-tasks?page=${page}&page_size=20`, signal)
export const read = (id: string, signal: AbortSignal) => get<TaskDetail>(`/agent-tasks/${encodeURIComponent(id)}`, signal)
export function source(citation: Citation, signal: AbortSignal) {
  const routes: Record<string, string> = { basic_analysis: 'analyses', tool_run: 'tool-runs', knowledge_chunk: 'knowledge-documents/chunks' }
  if (!routes[citation.kind] || !/^[0-9a-f-]{36}$/i.test(citation.id)) throw new Error('当前来源类型没有可用的正式读取入口。')
  return get<Record<string, unknown>>(`/${routes[citation.kind]}/${encodeURIComponent(citation.id)}`, signal)
}
