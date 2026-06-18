<template>
  <div class="auth-page">
    <h2>{{ isLogin ? '登录' : '注册' }}</h2>

    <input v-if="!isLogin" v-model="form.username" placeholder="用户名" />
    <input v-if="!isLogin" v-model="form.email" placeholder="邮箱" />
    <input v-if="isLogin" v-model="form.username" placeholder="用户名" @keydown.enter="submit" />
    <input v-if="isLogin" v-model="form.password" type="password" placeholder="密码" @keydown.enter="submit" />
    <input v-if="!isLogin" v-model="form.password" type="password" placeholder="密码" @keydown.enter="submit" />

    <button class="btn" @click="submit">{{ isLogin ? '登录' : '注册' }}</button>
    <span class="switch" @click="toggleMode">{{ isLogin ? '没有账号？去注册' : '已有账号？去登录' }}</span>
    <div v-if="error" class="error">{{ error }}</div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()

const isLogin = ref(true)
const error = ref('')
const form = reactive({ username: '', email: '', password: '' })

function toggleMode() {
  isLogin.value = !isLogin.value
  error.value = ''
}

async function submit() {
  error.value = ''
  try {
    if (isLogin.value) {
      await auth.login(form.username, form.password)
    } else {
      await auth.register(form.username, form.email, form.password)
    }
    router.push('/chat')
  } catch (e) {
    error.value = e.response?.data?.detail || '操作失败，请重试'
  }
}
</script>

<style scoped>
.auth-page { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; gap: 16px; }
.auth-page h2 { font-size: 24px; color: #333; margin-bottom: 8px; }
.auth-page input { width: 280px; padding: 10px 14px; border: 1px solid #ddd; border-radius: 8px; font-size: 15px; outline: none; }
.auth-page input:focus { border-color: #1a73e8; }
.btn { width: 280px; padding: 10px; border: none; border-radius: 8px; background: #1a73e8; color: white; font-size: 15px; cursor: pointer; }
.btn:hover { background: #1557b0; }
.switch { color: #1a73e8; cursor: pointer; font-size: 14px; }
.error { color: #e53935; font-size: 14px; }
</style>
