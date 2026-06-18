<template>
  <div class="messages" ref="box">
    <div v-for="(msg, i) in messages" :key="i" :class="['message', msg.role]">
      <div :class="['avatar', msg.role]">{{ msg.role === 'ai' ? 'AI' : '我' }}</div>
      <div class="bubble-wrap">
        <div v-if="msg.role === 'ai' && msg.agent" class="agent-badge">{{ agentLabel(msg.agent) }}</div>
        <div v-if="msg.toolStatus" class="tool-status">
          <span class="tool-spinner"></span>
          <span>{{ msg.toolStatus }}</span>
        </div>
        <div v-if="msg.thinking" class="bubble thinking">
          <div class="dot"></div><div class="dot"></div><div class="dot"></div>
        </div>
        <div v-else class="bubble" @mouseenter="hoverIdx = i" @mouseleave="hoverIdx = -1">
          <span v-html="renderMd(msg.content)"></span>
          <button
            v-if="msg.role === 'ai' && msg.content && hoverIdx === i"
            class="copy-btn"
            @click="onCopy(msg.content, i)"
            :title="copiedIdx === i ? '已复制' : '复制'"
          >{{ copiedIdx === i ? '✓' : '⧉' }}</button>
        </div>
        <CitationCard v-if="msg.role === 'ai' && msg.citations" :citations="msg.citations" />
      </div>
    </div>
    <div v-if="connectionStatus === 'disconnected'" class="connection-bar">
      连接已断开，请重新发送消息
    </div>
    <button v-if="showScrollBtn" class="scroll-bottom-btn" @click="onClickScrollBtn">↓</button>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { marked } from 'marked'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import CitationCard from './CitationCard.vue'

const props = defineProps({
  messages: Array,
  connectionStatus: { type: String, default: 'idle' },
})
const box = ref(null)
const showScrollBtn = ref(false)
const hoverIdx = ref(-1)
const copiedIdx = ref(-1)

const AGENT_LABELS = { general_chat: '通用对话', rag_agent: '文档检索', analysis_agent: '文档分析' }
function agentLabel(agent) { return AGENT_LABELS[agent] || agent }

function onCopy(text, idx) {
  navigator.clipboard.writeText(text).then(() => {
    copiedIdx.value = idx
    setTimeout(() => { copiedIdx.value = -1 }, 1500)
  })
}

function renderMd(text) {
  // 1. 先提取 LaTeX 公式，用占位符保护（防止 marked 破坏 _ ^ * 等）
  const mathBlocks = []
  let safe = text || ''
  // $$...$$（display math，必须先于 $...$ 匹配）
  safe = safe.replace(/\$\$([\s\S]+?)\$\$/g, (_, expr) => {
    const idx = mathBlocks.length
    mathBlocks.push({ expr: expr.trim(), display: true })
    return `%%MATH${idx}%%`
  })
  // $...$（inline math）
  safe = safe.replace(/\$([^\n$]+?)\$/g, (_, expr) => {
    const idx = mathBlocks.length
    mathBlocks.push({ expr: expr.trim(), display: false })
    return `%%MATH${idx}%%`
  })

  // 2. marked 解析（此时 LaTeX 特殊字符已安全）
  let html = marked.parse(safe).trim()

  // 3. 替换引用标记
  html = html.replace(/【片段(\d+)】/g, '<sup class="ref-mark">[$1]</sup>')
  html = html.replace(/\[(\d+)\]/g, '<sup class="ref-mark">[$1]</sup>')

  // 4. 用 KaTeX 渲染公式占位符
  html = html.replace(/%%MATH(\d+)%%/g, (_, idx) => {
    const { expr, display } = mathBlocks[Number(idx)]
    try {
      return katex.renderToString(expr, { displayMode: display, throwOnError: false })
    } catch {
      return `<code>${expr}</code>`
    }
  })

  return html
}

const BOTTOM_THRESHOLD = 120

function isNearBottom() {
  if (!box.value) return true
  const { scrollHeight, scrollTop, clientHeight } = box.value
  return scrollHeight - scrollTop - clientHeight < BOTTOM_THRESHOLD
}

function scrollToBottom() {
  nextTick(() => {
    if (box.value) box.value.scrollTop = box.value.scrollHeight
  })
}

// token 更新时：仅在底部附近才自动跟随
function scrollIfNeeded() {
  nextTick(() => {
    if (box.value && isNearBottom()) {
      box.value.scrollTop = box.value.scrollHeight
    }
  })
}

function onScroll() {
  showScrollBtn.value = !isNearBottom()
}

function onClickScrollBtn() {
  scrollToBottom()
  showScrollBtn.value = false
}

onMounted(() => { box.value?.addEventListener('scroll', onScroll) })
onUnmounted(() => { box.value?.removeEventListener('scroll', onScroll) })

watch(() => props.messages.length, scrollToBottom)
watch(() => props.messages[props.messages.length - 1]?.content, scrollIfNeeded)
</script>

<style scoped>
.messages { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; position: relative; }
.message { display: flex; gap: 12px; align-items: flex-start; }
.message.user { flex-direction: row-reverse; }
.avatar { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 600; flex-shrink: 0; }
.avatar.ai { background: #e8f4fd; color: #1a73e8; }
.avatar.user { background: #e8f5e9; color: #2e7d32; }
.bubble { padding: 12px 16px; border-radius: 12px; font-size: 15px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; position: relative; }
.bubble :deep(ul), .bubble :deep(ol) { padding-left: 20px; margin: 4px 0; }
.bubble :deep(li) { margin: 2px 0; }
.bubble :deep(strong) { font-weight: 600; }
.bubble :deep(.ref-mark) {
  font-size: 11px; color: #1a73e8; font-weight: 600;
  cursor: default; vertical-align: super; line-height: 1;
}
.bubble :deep(p) { margin: 0; }
.bubble :deep(p + p) { margin-top: 6px; }
.message.ai .bubble { background: #f8f9fa; color: #333; border-top-left-radius: 4px; }
.message.user .bubble { background: #1a73e8; color: white; border-top-right-radius: 4px; }
.thinking { display: flex; gap: 4px; align-items: center; padding: 12px 16px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #999; animation: bounce 1.2s infinite; }
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce { 0%, 60%, 100% { transform: translateY(0); } 30% { transform: translateY(-6px); } }
.scroll-bottom-btn { position: sticky; bottom: 16px; align-self: center; width: 36px; height: 36px; border-radius: 50%; border: 1px solid #ddd; background: #fff; color: #555; font-size: 18px; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.12); display: flex; align-items: center; justify-content: center; z-index: 10; flex-shrink: 0; }
.scroll-bottom-btn:hover { background: #f0f0f0; }
.bubble-wrap { display: flex; flex-direction: column; max-width: 80%; }
.agent-badge { font-size: 11px; color: #888; margin-bottom: 2px; }
.tool-status { font-size: 12px; color: #888; padding: 4px 8px; display: flex; align-items: center; gap: 6px; }
.tool-spinner { width: 12px; height: 12px; border: 2px solid #ddd; border-top-color: #666; border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.copy-btn {
  position: absolute;
  top: 4px;
  right: 4px;
  background: rgba(0,0,0,0.06);
  border: none;
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 13px;
  cursor: pointer;
  color: #666;
  opacity: 0.8;
  transition: opacity 0.15s;
}
.copy-btn:hover { opacity: 1; background: rgba(0,0,0,0.1); }

.connection-bar {
  background: #fff3e0;
  color: #e65100;
  text-align: center;
  padding: 8px;
  font-size: 13px;
  border-top: 1px solid #ffe0b2;
}

@media (max-width: 768px) {
  .bubble-wrap { max-width: 90%; }
  .messages { padding: 16px; }
}
</style>
