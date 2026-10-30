import type { Analysis, Diagnosis, DiagnoseInput, SearchHit } from '../types/api'

const BASE = '/api/v1'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, options)
  let data: unknown
  try { data = await response.json() } catch { throw new Error('服务未返回有效 JSON，请检查后端连接') }
  if (!response.ok) {
    const detail = (data as { detail?: unknown })?.detail
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail ?? data))
  }
  return data as T
}

export const createAnalysis = (form: FormData) => request<Analysis>('/prototype/analyses', { method: 'POST', body: form })
export const getAnalysis = (id: string) => request<Analysis>('/prototype/analyses/' + encodeURIComponent(id))
export const diagnose = (input: DiagnoseInput) => request<Diagnosis>('/diagnoses', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
})
export const searchKnowledge = (query: string, model: string) => request<{ hits: SearchHit[] }>('/knowledge/search', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, embedding_model: model }),
})
