<template>
  <div class="chat-page">
    <div v-if="sidebarOpen" class="sidebar-overlay" @click="sidebarOpen = false"></div>
    <Sidebar
      :class="{ open: sidebarOpen }"
      :sessions="chat.sessionList"
      :currentSessionId="chat.sessionId"
      @newSession="chat.newSession(); sidebarOpen = false"
      @select="onSelectSession"
      @delete="onDeleteSession"
      @close="sidebarOpen = false"
    />
    <div class="main">
      <div class="header">
        <button class="hamburger" @click="sidebarOpen = !sidebarOpen">☰</button>
        <span>Mini Agent</span>
        <div class="header-actions">
          <a v-if="auth.user?.role === 'admin'" href="/admin.html" class="admin-link">管理后台</a>
          <span class="logout" @click="onLogout">退出登录</span>
        </div>
      </div>
      <MessageList :messages="chat.messages" :connectionStatus="chat.connectionStatus" />
      <InputBar :loading="chat.loading" @send="chat.sendMessage" @stop="chat.stopGeneration()" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'
import { useDocumentsStore } from '../stores/documents'
import { useToastStore } from '../stores/toast'
import Sidebar from '../components/Sidebar.vue'
import MessageList from '../components/MessageList.vue'
import InputBar from '../components/InputBar.vue'

const router = useRouter()
const auth = useAuthStore()
const chat = useChatStore()
const docs = useDocumentsStore()
const toast = useToastStore()
const sidebarOpen = ref(false)

onMounted(async () => {
  await auth.fetchUser()
  await chat.loadSessionList()
  await docs.fetchDocuments()

  // 首屏恢复策略
  const localId = localStorage.getItem('session_id')
  if (localId && chat.sessionList.some(s => s.session_uuid === localId)) {
    await chat.loadSession(localId)
  } else if (chat.sessionList.length > 0) {
    await chat.loadSession(chat.sessionList[0].session_uuid)
  }
})

function onSelectSession(uuid) {
  chat.loadSession(uuid)
  sidebarOpen.value = false
}

async function onDeleteSession(uuid) {
  if (!confirm('确定删除这个会话？')) return
  await chat.deleteSession(uuid)
  toast.success('会话已删除')
}

function onLogout() {
  try {
    auth.logout()
    chat.newSession()
  } finally {
    router.replace('/login')
  }
}
</script>

<style scoped>
.chat-page { width: 100%; height: 100vh; display: flex; position: relative; }
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.header { padding: 16px 24px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 18px; color: var(--text); display: flex; justify-content: space-between; align-items: center; }
.header-actions { display: flex; align-items: center; gap: 16px; }
.admin-link { font-size: 14px; color: var(--primary); text-decoration: none; font-weight: 400; }
.admin-link:hover { text-decoration: underline; }
.logout { font-size: 14px; color: #888; cursor: pointer; font-weight: 400; }
.logout:hover { color: var(--danger); }
.hamburger { display: none; background: none; border: none; font-size: 22px; cursor: pointer; color: var(--text); padding: 0 8px 0 0; }
.sidebar-overlay { display: none; }

@media (max-width: 768px) {
  .hamburger { display: block; }
  .sidebar-overlay {
    display: block;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.3);
    z-index: 99;
  }
}
</style>
