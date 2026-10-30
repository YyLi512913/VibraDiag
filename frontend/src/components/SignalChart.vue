<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
const props = defineProps<{ points: [number, number][]; label: string; markers?: { frequency_hz: number; label: string }[] }>()
const canvas = ref<HTMLCanvasElement | null>(null)
function draw() {
  const element = canvas.value, context = element?.getContext('2d')
  if (!element || !context) return
  const width = element.width, height = element.height
  context.clearRect(0, 0, width, height)
  if (!props.points.length) return
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity
  for (const [x, y] of props.points) { xmin = Math.min(xmin, x); xmax = Math.max(xmax, x); ymin = Math.min(ymin, y); ymax = Math.max(ymax, y) }
  const yscale = Math.max(Math.abs(ymin), Math.abs(ymax)) || 1
  let low = ymin / yscale, high = ymax / yscale
  if (low === high) { low -= .5; high += .5 }
  const xscale = Math.max(Math.abs(xmin), Math.abs(xmax)) || 1
  context.strokeStyle = '#85d7cf'; context.lineWidth = 1; context.beginPath()
  props.points.forEach(([x, y], i) => {
    const px = 34 + (x / xscale - xmin / xscale) / ((xmax / xscale - xmin / xscale) || 1) * (width - 50)
    const py = height - 22 - (y / yscale - low) / (high - low) * (height - 40)
    if (i) context.lineTo(px, py); else context.moveTo(px, py)
  })
  context.stroke(); context.fillStyle = '#9baebf'
  context.save(); context.strokeStyle = '#e6c887'; context.fillStyle = '#e6c887'; context.setLineDash([4, 4])
  ;(props.markers || []).filter(m => m.frequency_hz >= xmin && m.frequency_hz <= xmax).slice(0, 10).forEach((marker, i) => {
    const px = 34 + (marker.frequency_hz / xscale - xmin / xscale) / ((xmax / xscale - xmin / xscale) || 1) * (width - 50)
    context.beginPath(); context.moveTo(px, 35); context.lineTo(px, height - 22); context.stroke()
    context.fillText(marker.label, Math.min(px + 3, width - 65), 14 + i % 2 * 14)
  })
  context.restore()
  const tick = (value: number) => value !== 0 && (Math.abs(value) < .01 || Math.abs(value) >= 1e6) ? value.toExponential(2) : value.toFixed(2)
  context.fillText(tick(xmin), 34, height - 5); context.fillText(tick(xmax), width - 70, height - 5)
  context.fillText(ymax.toPrecision(4), 3, 12)
}
onMounted(draw)
watch(() => props.points, draw)
watch(() => props.markers, draw)
</script>

<template><canvas ref="canvas" width="900" height="190" role="img" :aria-label="label" /></template>
