export interface Identity { user_id: string; organization_id: string; roles: string[] }
export interface Page<T> { items: T[]; page: number; page_size: number }
export interface Asset { id: string; name: string; asset_code?: string; point_code?: string }
export interface Metadata {
  sampling_rate_hz: number; acceleration_unit: 'g' | 'm/s2'; asset_id?: string | null
  measurement_point_id?: string | null; rpm?: number | null; direction?: string
  direction_note?: string | null; load_value?: number | null; load_unit?: string | null
  load_note?: string | null; acquired_at?: string | null; source_type?: string; source_note?: string | null
}
export interface Revision { id: string; revision_no: number; parent_id: string | null; metadata: Metadata; reason: string; created_at: string }
export interface Waveform { time_s: number[]; values: number[]; unit: string; duration_s: number; method: string; sample_count: number }
export interface RecordSummary { id: string; original_filename: string; current_revision_id: string; row_version: number; sha256: string; validation_summary: { sample_count: number } }
export interface Measurement extends RecordSummary {
  revision: Revision; preview: Waveform
  input_confirmation: { id: string; revision_id: string } | null
}
export interface AnalysisInput { measurement_revision_id: string; confirmation_id: string; config_profile: 'basic-v1'; retry_of?: string }
export interface RunSummary { analysis_id: string; status: 'running' | 'completed' | 'failed' | 'interrupted'; measurement_revision_id: string; revision_no?: number; started_at: string; error_code: string | null }
export interface FormalAnalysis extends RunSummary {
  measurement_id: string
  is_current_revision: boolean; input_confirmation_active: boolean; algorithm_version: string; config_hash: string
  input_snapshot: { input_sha256: string; revision: Revision }
  metrics: Record<string, { value: number | null; unit: string; reason: string | null }> | null
  warnings: string[]; artifacts: { kind: string; sha256: string }[]
  result_summary: { sample_count: number; sampling_rate_hz: number; duration_s: number; bin_spacing_hz: number } | null
}
export interface Spectrum { frequency_hz: number[]; amplitude_m_s2: number[]; full_bin_count: number; method: string }
