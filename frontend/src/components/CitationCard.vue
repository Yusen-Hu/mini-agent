<template>
  <div v-if="citations && citations.length" class="citations">
    <div class="citation-list">
      <div v-for="(c, i) in sortedCitations" :key="i" class="citation-item">
        <div class="citation-header">
          <span class="ref-num">[{{ i + 1 }}]</span>
          <span class="filename">{{ c.filename }}</span>
          <span class="label" :class="labelClass(c.relevance_label)">{{ c.relevance_label }}</span>
          <span v-if="c.chunk_index != null" class="chunk">#{{ c.chunk_index }}</span>
        </div>
        <div v-if="c.snippet" class="snippet">{{ c.snippet }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ citations: Array })

const sortedCitations = computed(() => {
  if (!props.citations) return []
  return [...props.citations].sort((a, b) => (b.rrf_score || 0) - (a.rrf_score || 0))
})

function labelClass(label) {
  if (label === '高度相关') return 'high'
  if (label === '相关') return 'medium'
  return 'low'
}
</script>

<style scoped>
.citations { margin-top: 8px; }
.citation-list {
  background: #f0f4f8; border-radius: 8px; padding: 10px 12px;
  border-left: 3px solid #1a73e8;
}
.citation-item {
  padding: 6px 0; font-size: 13px; line-height: 1.5;
  border-bottom: 1px solid #e0e0e0;
}
.citation-item:last-child { border-bottom: none; }
.citation-header { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ref-num {
  font-weight: 700; color: #1a73e8; font-size: 13px;
  min-width: 24px;
}
.filename { font-weight: 600; color: #333; }
.chunk { color: #888; font-size: 12px; }
.label {
  display: inline-block; padding: 1px 6px; border-radius: 4px;
  font-size: 11px; font-weight: 500;
}
.label.high { background: #e8f5e9; color: #2e7d32; }
.label.medium { background: #fff8e1; color: #f57f17; }
.label.low { background: #f3e5f5; color: #7b1fa2; }
.snippet { color: #666; font-size: 12px; margin-top: 4px; line-height: 1.4; padding-left: 30px; }
</style>
