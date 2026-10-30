import type { Metadata } from './formal'
import type { ToolResult } from './tools'
export interface ComparisonOption { id: string; before_measurement_id: string; before_revision_id: string; before_filename: string; checked_at: string; reason: string }
export interface ComparisonInput { tool_name: 'compare_measurements'; measurement_revision_id: string; confirmation_id: string; before_confirmation_id: string; declaration_id: string; retry_of?: string }
export interface Difference { before: number | null; after: number | null; unit: string; delta: number | null; relative_change_percent: number | null; delta_reason?: string | null; percent_reason: string | null; limitations: string[] }
export interface ComparisonResult extends Omit<ToolResult, 'result'> {
  declaration_id: string; before_measurement_id: string; before_measurement_revision_id: string; is_current_before_revision: boolean
  result: { summary: { comparison_status: string; reasons: string[]; related_reasons: string[]; before: { metadata: Metadata }; after: { metadata: Metadata }; checks: { speed?: { before_rpm: number; after_rpm: number; tolerance_rpm: number }; load?: { before: number; after: number; unit: string; tolerance: number } }; metrics: Record<string, Difference> }; missing_requirements: string[]; limitations: string[] } | null
}
