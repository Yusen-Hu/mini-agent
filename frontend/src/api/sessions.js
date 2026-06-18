import client from './client'

export function listSessions(page = 1, pageSize = 20) {
  return client.get('/sessions', { params: { page, page_size: pageSize } })
}

export function getMessages(sessionUuid, page = 1, pageSize = 200) {
  return client.get(`/sessions/${sessionUuid}/messages`, { params: { page, page_size: pageSize } })
}

export function deleteSession(sessionUuid) {
  return client.delete(`/sessions/${sessionUuid}`)
}

export function renameSession(sessionUuid, title) {
  return client.patch(`/sessions/${sessionUuid}`, null, { params: { title } })
}
