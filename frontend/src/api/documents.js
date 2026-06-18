import client from './client'

export function uploadDocument(file) {
  const form = new FormData()
  form.append('file', file)
  return client.post('/documents/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

export function listDocuments(page = 1, pageSize = 200) {
  return client.get('/documents', { params: { page, page_size: pageSize } })
}

export function deleteDocument(id) {
  return client.delete(`/documents/${id}`)
}
