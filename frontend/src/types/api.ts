export interface Analysis {
  analysis_id: string
  sample_count: number
  sampling_rate_hz: number
  duration_seconds: number
  bin_spacing_hz: number
  sha256: string
  features: Record<string, number | null>
  waveform_preview: [number, number][]
  spectrum: { frequency_hz: number[]; amplitude_m_s2: number[] }
  warnings: string[]
}

export interface Draft {
  status: 'analysis_only' | 'needs_information' | 'insufficient_evidence'
  summary: string
  observations: { text: string; evidence_ids: string[] }[]
  missing_information: string[]
  next_steps: string[]
}

export interface Diagnosis {
  diagnosis_id: string
  model: string
  embedding_model: string
  wall_seconds: number
  draft: Draft | null
  error: string | null
  trace: { stage: string; name?: string; [key: string]: unknown }[]
  metrics: Record<string, unknown>[]
}

export interface SearchHit {
  id: string; title: string; text: string; source: string; score: number
}

export interface DiagnoseInput {
  analysis_id: string
  description: string
  rpm: number | null
  model: string
  embedding_model: string
}
