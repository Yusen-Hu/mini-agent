<template>
  <div class="sidebar">
    <div class="sidebar-header">
      <button class="new-btn" @click="$emit('newSession')">+ 新建会话</button>
      <button class="close-btn" @click="$emit('close')">×</button>
    </div>

    <!-- 上传区域 -->
    <div class="upload-section">
      <input
        ref="fileInput"
        type="file"
        :accept="ACCEPT_EXTS"
        style="display: none"
        @change="onFileChange"
      />
      <button
        class="upload-btn"
        :disabled="uploading"
        @click="$refs.fileInput.click()"
      >
        {{ uploading ? '上传中...' : '上传文档' }}
      </button>
      <p v-if="uploadError" class="upload-error">{{ uploadError }}</p>
    </div>

    <!-- 文档列表 -->
    <div v-if="documents.length" class="doc-section">
      <div class="doc-title">文档 ({{ documents.length }})</div>
      <ul class="doc-list">
        <li v-for="doc in documents" :key="doc.id" class="doc-item">
          <div class="doc-info">
            <span class="doc-name" :title="doc.filename">{{ doc.filename }}</span>
            <span class="doc-meta">
              <span :class="['status', doc.status]">{{ statusLabel(doc.status) }}</span>
              <span v-if="doc.status === 'ready'" class="chunks">{{ doc.chunk_count }}段</span>
            </span>
          </div>
          <button class="doc-delete" title="删除" @click="onDelete(doc)">×</button>
        </li>
      </ul>
    </div>

    <SessionList
      :sessions="sessions"
      :currentSessionId="currentSessionId"
      @select="$emit('select', $event)"
      @delete="$emit('delete', $event)"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import SessionList from './SessionList.vue'
import { useDocumentsStore } from '../stores/documents'

const ACCEPT_EXTS = '.pdf,.docx,.doc,.txt,.md,.html'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: null },
})
defineEmits(['newSession', 'select', 'delete', 'close'])

const store = useDocumentsStore()
const documents = computed(() => store.docs)
const uploading = computed(() => store.uploading)
const uploadError = computed(() => store.uploadError)

function statusLabel(status) {
  if (status === 'ready') return '就绪'
  if (status === 'processing') return '处理中'
  if (status === 'error') return '失败'
  return status
}

async function onFileChange(e) {
  const file = e.target.files[0]
  if (!file) return
  await store.upload(file)
  e.target.value = ''  // 清空，允许重复选同一文件
}

async function onDelete(doc) {
  if (!confirm(`确定删除 "${doc.filename}"？`)) return
  await store.remove(doc.id)
}
</script>

<style scoped>
.sidebar { width: 260px; border-right: 1px solid var(--border); display: flex; flex-direction: column; background: #fafafa; flex-shrink: 0; overflow-y: auto; }
.sidebar-header { padding: 16px 16px 8px; display: flex; gap: 8px; align-items: center; }
.new-btn { flex: 1; padding: 10px; border: 1px dashed #ccc; border-radius: 8px; background: transparent; color: var(--primary); font-size: 14px; cursor: pointer; }
.new-btn:hover { background: #e8f0fe; border-color: var(--primary); }
.close-btn { display: none; background: none; border: none; font-size: 22px; cursor: pointer; color: #888; padding: 4px 8px; border-radius: 4px; }
.close-btn:hover { background: #eee; }

/* 上传 */
.upload-section { padding: 0 16px 8px; }
.upload-btn { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 8px; background: #fff; color: var(--text); font-size: 13px; cursor: pointer; }
.upload-btn:hover { background: #f0f4f8; }
.upload-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.upload-error { color: var(--danger); font-size: 12px; margin: 6px 0 0; }

/* 文档列表 */
.doc-section { padding: 0 16px; border-top: 1px solid var(--border); }
.doc-title { font-size: 12px; color: #888; padding: 10px 0 6px; }
.doc-list { list-style: none; padding: 0; margin: 0; }
.doc-item { display: flex; align-items: center; gap: 6px; padding: 6px 0; border-bottom: 1px solid #f0f0f0; }
.doc-item:last-child { border-bottom: none; }
.doc-info { flex: 1; min-width: 0; }
.doc-name { display: block; font-size: 13px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.doc-meta { display: flex; gap: 6px; align-items: center; margin-top: 2px; }
.status { font-size: 11px; padding: 1px 4px; border-radius: 3px; }
.status.ready { background: #e8f5e9; color: #2e7d32; }
.status.processing { background: #fff8e1; color: #f57f17; }
.status.error { background: #fce4ec; color: #c62828; }
.chunks { font-size: 11px; color: #888; }
.doc-delete { background: none; border: none; color: #ccc; font-size: 16px; cursor: pointer; padding: 2px 6px; border-radius: 4px; }
.doc-delete:hover { color: var(--danger); background: #fce4ec; }

@media (max-width: 768px) {
  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    z-index: 100;
    transform: translateX(-100%);
    transition: transform 0.25s ease;
    box-shadow: none;
  }
  .sidebar.open {
    transform: translateX(0);
    box-shadow: 4px 0 16px rgba(0,0,0,0.15);
  }
  .close-btn { display: block; }
}
</style>
