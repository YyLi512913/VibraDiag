import type { AnalysisInput, Asset, FormalAnalysis, Identity, Measurement, Metadata, Page, RecordSummary, Revision, RunSummary, Spectrum, Waveform } from '../types/formal'

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}
let csrf = sessionStorage.getItem('vibradiag-csrf') || ''
export function forgetCsrf() { csrf = ''; sessionStorage.removeItem('vibradiag-csrf') }
export const hasCsrf = () => !!csrf
async function send(path: string, options: RequestInit = {}, blob = false): Promise<unknown> {
  const headers = new Headers(options.headers)
  if (csrf && options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf)
  const response = await fetch('/api/v1' + path, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const body = await response.json()
      message = body.error?.message || (typeof body.detail === 'string' ? body.detail : message)
      if (body.error?.row) message += `，第 ${body.error.row} 行`
      if (body.error?.code) message += ` [${body.error.code}]`
    } catch { /* Keep status when an intermediary returns non-JSON. */ }
    throw new ApiError(message, response.status)
  }
  if (response.status === 204) return null
  return blob ? response.blob() : response.json()
}
export const get = <T>(path: string) => send(path) as Promise<T>
export const post = <T>(path: string, body: unknown, extra = {}) => send(path, { method: 'POST', headers: { 'Content-Type': 'application/json', ...extra }, body: JSON.stringify(body) }) as Promise<T>
export async function login(organization_id: string, username: string, password: string) {
  const result = await post<{ csrf_token: string }>('/auth/login', { organization_id, username, password })
  csrf = result.csrf_token; sessionStorage.setItem('vibradiag-csrf', csrf)
  return me()
}
export const me = () => get<Identity>('/auth/me')
export async function logout() { await post('/auth/logout', {}); forgetCsrf() }
export const assets = () => get<Page<Asset>>('/assets?page_size=100')
export const points = (id: string) => get<Page<Asset>>(`/assets/${id}/measurement-points?page_size=100`)
export const records = (page = 1) => get<Page<RecordSummary>>(`/measurements?page=${page}&page_size=10`)
export const measurement = (id: string) => get<Measurement>(`/measurements/${id}`)
export const revisions = (id: string, page = 1) => get<Page<Revision>>(`/measurements/${id}/revisions?page=${page}&page_size=10`)
export const analyses = (id: string, page = 1) => get<Page<RunSummary>>(`/analyses?measurement_id=${id}&page=${page}&page_size=10`)
export async function upload(file: File, metadata: Metadata) {
  const form = new FormData(); form.set('file', file); form.set('metadata', JSON.stringify(metadata))
  return send('/measurements', { method: 'POST', body: form }) as Promise<Measurement>
}
export const revise = (record: Measurement, metadata: Metadata, reason: string) => post<Measurement>(`/measurements/${record.id}/revisions`, { metadata, reason, expected_current_revision_id: record.current_revision_id, row_version: record.row_version })
export const confirm = (record: Measurement) => post(`/measurements/${record.id}/confirmations`, { revision_id: record.current_revision_id })
export const analyze = (input: AnalysisInput, key: string) => post<FormalAnalysis>('/analyses', input, { 'Idempotency-Key': key })
export const analysis = (id: string) => get<FormalAnalysis>(`/analyses/${id}`)
export const waveform = (id: string) => get<Waveform>(`/analyses/${id}/series?kind=waveform`)
export const spectrum = (id: string) => get<Spectrum>(`/analyses/${id}/series?kind=spectrum`)
export async function download(path: string, filename: string) {
  const blob = await send(path, {}, true) as Blob
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = filename; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
