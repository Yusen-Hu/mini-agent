<template>
  <div class="input-area">
    <textarea
      v-model="text"
      placeholder="输入消息，按 Enter 发送..."
      @keydown.enter.exact.prevent="send"
      :disabled="loading"
    ></textarea>
    <button v-if="!loading" @click="send" :disabled="!text.trim()">&#9658;</button>
    <button v-else class="stop" @click="$emit('stop')">&#9632;</button>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({ loading: Boolean })
const emit = defineEmits(['send', 'stop'])

const text = ref('')

function send() {
  const t = text.value.trim()
  if (!t) return
  emit('send', t)
  text.value = ''
}
</script>

<style scoped>
.input-area { padding: 16px 24px; border-top: 1px solid #eee; display: flex; gap: 12px; }
textarea { flex: 1; padding: 12px 16px; border: 1px solid #ddd; border-radius: 24px; resize: none; font-size: 15px; font-family: inherit; outline: none; height: 48px; line-height: 24px; transition: border-color 0.2s; }
textarea:focus { border-color: #1a73e8; }
button { width: 48px; height: 48px; border-radius: 50%; border: none; background: #1a73e8; color: white; font-size: 20px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background 0.2s; flex-shrink: 0; }
button:hover:not(:disabled) { background: #1557b0; }
button:disabled { background: #ccc; cursor: not-allowed; }
button.stop { background: #e53935; }
button.stop:hover { background: #b71c1c; }
</style>
