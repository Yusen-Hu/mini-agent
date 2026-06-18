import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as authApi from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('access_token') || '')
  const user = ref(null)

  async function login(username, password) {
    const { data } = await authApi.login({ username, password })
    token.value = data.access_token
    localStorage.setItem('access_token', data.access_token)
  }

  async function register(username, email, password) {
    const { data } = await authApi.register({ username, email, password })
    token.value = data.access_token
    localStorage.setItem('access_token', data.access_token)
  }

  async function fetchUser() {
    try {
      const { data } = await authApi.getMe()
      user.value = data
    } catch {
      logout()
    }
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('access_token')
    localStorage.removeItem('session_id')
  }

  return { token, user, login, register, fetchUser, logout }
})
