import axios from 'axios'

const client = axios.create({
  baseURL: (import.meta.env.VITE_API_URL || '') + '/api',
  timeout: 30000,
})

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

client.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401 && localStorage.getItem('access_token')) {
      localStorage.removeItem('access_token')
      localStorage.removeItem('session_id')
      window.location.href = '/login'
    }
    if (!err.response) {
      // Network error — no response from server
      import('../stores/toast').then(({ useToastStore }) => {
        useToastStore().error('网络连接失败，请检查网络')
      })
    }
    return Promise.reject(err)
  }
)

export default client
