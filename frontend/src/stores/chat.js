import { defineStore } from 'pinia'
import { ref } from 'vue'
import { chatStream } from '../api/chat'
import { listSessions, getMessages, deleteSession as apiDelete, renameSession as apiRename } from '../api/sessions'
import { useToastStore } from './toast'

const WELCOME = { role: 'ai', content: '你好！我是 Mini Agent，有什么可以帮你的？' }

export const useChatStore = defineStore('chat', () => {
  const messages = ref([WELCOME])
  const sessionId = ref(localStorage.getItem('session_id') || null)
  const sessionList = ref([])
  const loading = ref(false)
  const connectionStatus = ref('idle') // 'idle' | 'connected' | 'disconnected'
  let abortController = null

  async function loadSessionList() {
    const { data } = await listSessions()
    sessionList.value = data.sessions
  }

  async function loadSession(uuid) {
    const { data } = await getMessages(uuid)
    if (data.messages && data.messages.length > 0) {
      messages.value = data.messages.map(m => ({
        role: m.role === 'user' ? 'user' : 'ai',
        content: m.content,
        agent: m.agent_name || undefined,
        citations: m.extra_data?.citations || undefined,
      }))
    } else {
      messages.value = [WELCOME]
    }
    sessionId.value = uuid
    localStorage.setItem('session_id', uuid)
  }

  async function sendMessage(text) {
    const toast = useToastStore()
    messages.value.push({ role: 'user', content: text })
    messages.value.push({ role: 'ai', content: '', thinking: true })
    const botIdx = messages.value.length - 1
    loading.value = true
    connectionStatus.value = 'connected'

    abortController = new AbortController()
    let receivedDone = false

    try {
      const res = await chatStream(text, sessionId.value, abortController.signal)
      if (res.status === 401) {
        toast.error('登录已过期，请重新登录')
        localStorage.removeItem('access_token')
        localStorage.removeItem('session_id')
        window.location.href = '/login'
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let reply = ''
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          let data
          try {
            data = JSON.parse(line.slice(6))
          } catch {
            console.warn('SSE JSON parse error:', line)
            continue
          }
          switch (data.type) {
            case 'agent':
              messages.value[botIdx].agent = data.agent
              break
            case 'tool_start':
              messages.value[botIdx].toolStatus = data.label
              break
            case 'token':
              reply += data.content
              messages.value[botIdx] = { role: 'ai', content: reply, thinking: false, agent: messages.value[botIdx].agent, toolStatus: '' }
              break
            case 'session':
              sessionId.value = data.session_id
              localStorage.setItem('session_id', data.session_id)
              break
            case 'citation':
              if (data.schema_version === 1) {
                messages.value[botIdx].citations = data.items
              }
              break
            case 'error':
              reply += '\n\n' + (data.message || '出错了，请重试。')
              messages.value[botIdx].content = reply
              toast.error(data.message || '请求处理失败')
              receivedDone = true
              return
            case 'done':
              receivedDone = true
              return
          }
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        messages.value[botIdx].content = (messages.value[botIdx].content || '') + '\n\n（已停止生成）'
      } else {
        messages.value[botIdx] = { role: 'ai', content: messages.value[botIdx].content || '出错了，请重试。', thinking: false }
        toast.error('发送失败，请重试')
      }
    } finally {
      loading.value = false
      abortController = null
      if (!receivedDone && connectionStatus.value === 'connected') {
        connectionStatus.value = 'disconnected'
      } else {
        connectionStatus.value = 'idle'
      }
    }
  }

  function stopGeneration() {
    if (abortController) {
      abortController.abort()
    }
  }

  function newSession() {
    sessionId.value = null
    localStorage.removeItem('session_id')
    messages.value = [WELCOME]
    connectionStatus.value = 'idle'
  }

  async function deleteSession(uuid) {
    await apiDelete(uuid)
    sessionList.value = sessionList.value.filter(s => s.session_uuid !== uuid)
    if (sessionId.value === uuid) newSession()
  }

  async function renameSession(uuid, title) {
    await apiRename(uuid, title)
    const item = sessionList.value.find(s => s.session_uuid === uuid)
    if (item) item.title = title
  }

  return {
    messages, sessionId, sessionList, loading, connectionStatus,
    loadSessionList, loadSession, sendMessage, stopGeneration, newSession, deleteSession, renameSession,
  }
})
