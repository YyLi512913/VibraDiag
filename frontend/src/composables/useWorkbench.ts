import { ref } from 'vue'
import * as api from '../api/client'
import type { Analysis, Diagnosis, DiagnoseInput, SearchHit } from '../types/api'

const STORAGE_KEY = 'vibradiag-analysis'
const message = (error: unknown) => error instanceof Error ? error.message : '未知错误'

export function useWorkbench() {
  const analysis = ref<Analysis | null>(null)
  const diagnosis = ref<Diagnosis | null>(null)
  const hits = ref<SearchHit[]>([])
  const uploading = ref(false)
  const modelBusy = ref(false)
  const status = ref('请选择文件并填写实际采样参数。')

  async function restore() {
    const id = localStorage.getItem(STORAGE_KEY)
    if (!id) return
    try { analysis.value = await api.getAnalysis(id); status.value = '已恢复保存的基础分析。' }
    catch { localStorage.removeItem(STORAGE_KEY) }
  }

  async function upload(form: FormData) {
    uploading.value = true; analysis.value = null; diagnosis.value = null
    status.value = '正在校验和计算…'
    try {
      analysis.value = await api.createAnalysis(form)
      localStorage.setItem(STORAGE_KEY, analysis.value.analysis_id)
      status.value = '基础分析完成；尚未完成故障诊断。'
    } catch (error) { status.value = '导入失败：' + message(error) }
    finally { uploading.value = false }
  }

  async function run(input: Omit<DiagnoseInput, 'analysis_id'>) {
    if (!analysis.value) return
    modelBusy.value = true; diagnosis.value = null
    status.value = '模型正在选择工具和整理草稿…'
    try {
      diagnosis.value = await api.diagnose({ ...input, analysis_id: analysis.value.analysis_id })
      status.value = diagnosis.value.error ? 'Agent 未完成，基础分析仍可用。' : '草稿已生成，尚未人工复核。'
    } catch (error) { status.value = '执行失败：' + message(error) }
    finally { modelBusy.value = false }
  }

  async function search(query: string, embeddingModel: string) {
    modelBusy.value = true
    try { hits.value = (await api.searchKnowledge(query, embeddingModel)).hits }
    catch (error) { status.value = '检索失败：' + message(error) }
    finally { modelBusy.value = false }
  }
  return { analysis, diagnosis, hits, uploading, modelBusy, status, restore, upload, run, search }
}
