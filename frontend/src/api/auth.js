import client from './client'

export function register(data) {
  return client.post('/auth/register', data)
}

export function login(data) {
  return client.post('/auth/login', data)
}

export function getMe() {
  return client.get('/auth/me')
}
