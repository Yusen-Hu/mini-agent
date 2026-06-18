<template>
  <div class="session-list">
    <div
      v-for="s in sessions"
      :key="s.session_uuid"
      :class="['session-item', { active: s.session_uuid === currentSessionId }]"
      @click="$emit('select', s.session_uuid)"
    >
      <div class="title">{{ s.title || '新会话' }}</div>
      <div class="time">{{ formatTime(s.updated_at) }}</div>
      <button class="session-delete" title="删除会话" @click.stop="$emit('delete', s.session_uuid)">×</button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  sessions: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: null },
})
defineEmits(['select', 'delete'])

function formatTime(iso) {
  const d = new Date(iso)
  const now = new Date()
  const diff = now - d
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  return d.toLocaleDateString()
}
</script>

<style scoped>
.session-list { flex: 1; overflow-y: auto; }
.session-item { padding: 12px 16px; cursor: pointer; border-left: 3px solid transparent; position: relative; }
.session-item .session-delete { display: none; position: absolute; right: 8px; top: 50%; transform: translateY(-50%); background: none; border: none; color: #ccc; font-size: 16px; cursor: pointer; padding: 2px 6px; border-radius: 4px; }
.session-item:hover .session-delete { display: block; }
.session-delete:hover { color: #e53935; background: #fce4ec; }
.session-item:hover { background: #f0f0f0; }
.session-item.active { background: #e8f0fe; border-left-color: #1a73e8; }
.title { font-size: 14px; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.time { font-size: 12px; color: #999; margin-top: 4px; }
</style>
