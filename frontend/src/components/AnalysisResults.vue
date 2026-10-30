<script setup lang="ts">
import { computed } from 'vue'
import type { Analysis } from '../types/api'
import SignalChart from './SignalChart.vue'
const props = defineProps<{ analysis: Analysis | null }>()
const cards = [['rms', '原始 RMS'], ['ac_rms', 'AC RMS'], ['peak', '绝对峰值'], ['peak_to_peak', '峰峰值'], ['crest_factor', '峰值因子'], ['pearson_kurtosis', 'Pearson 峭度']]
const spectrumPreview = computed<[number, number][]>(() => {
  if (!props.analysis) return []
  const { frequency_hz: f, amplitude_m_s2: a } = props.analysis.spectrum
  const result: [number, number][] = [], step = Math.max(1, Math.ceil(f.length / 1000))
  for (let i = 0; i < f.length; i += step) {
    let lo = i, hi = i
    for (let j = i; j < Math.min(i + step, f.length); j++) { if (a[j] < a[lo]) lo = j; if (a[j] > a[hi]) hi = j }
    for (const j of [...new Set([lo, hi])].sort((x, y) => x - y)) result.push([f[j], a[j]])
  }
  return result
})
function format(value: number | null | undefined) { return value == null ? '不可计算' : value.toPrecision(5) }
</script>

<template>
  <section class="panel">
    <h2>基础分析</h2>
    <template v-if="analysis">
      <div class="metrics"><div v-for="[key, name] in cards" :key="key" class="metric"><b>{{ format(analysis.features[key]) }}</b>{{ name }}</div></div>
      <p class="muted">{{ analysis.sample_count }} 点 · {{ analysis.sampling_rate_hz }} Hz · {{ analysis.duration_seconds.toFixed(3) }} 秒 · bin间隔 {{ analysis.bin_spacing_hz.toFixed(3) }} Hz</p>
      <p class="muted">预览保留局部极值 · ID {{ analysis.analysis_id }}</p>
    </template>
    <p v-else class="muted">暂无数据。普通频谱不等于轴承故障证据。</p>
    <h3>时域预览 / m/s² · 秒</h3><SignalChart :points="analysis?.waveform_preview ?? []" label="原始加速度波形预览" />
    <h3>单边峰值幅度谱 / m/s² · Hz</h3><SignalChart :points="spectrumPreview" label="单边峰值幅度谱" />
    <p v-if="analysis?.warnings.length" class="notice">{{ analysis.warnings.join('；') }}</p>
  </section>
</template>
