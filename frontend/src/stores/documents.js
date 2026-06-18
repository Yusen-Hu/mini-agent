import { defineStore } from 'pinia'
import { ref } from 'vue'
import { uploadDocument, listDocuments, deleteDocument } from '../api/documents'
import { useToastStore } from './toast'

const ERROR_MESSAGES = {
  409: '文件已存在',
  413: '文件超过大小限制',
  422: '文件格式异常，可能不是有效文本文件',
  500: '服务器处理失败，请稍后重试',
}

export const useDocumentsStore = defineStore('documents', () => {
  const docs = ref([])
  const uploading = ref(false)
  const uploadError = ref('')

  async function fetchDocuments() {
    const { data } = await listDocuments()
    docs.value = data.documents
  }

  async function upload(file) {
    const toast = useToastStore()
    uploading.value = true
    uploadError.value = ''
    try {
      await uploadDocument(file)
      await fetchDocuments()
      toast.success('上传成功')
      return true
    } catch (err) {
      const status = err.response?.status
      const msg = ERROR_MESSAGES[status] || '上传失败，请稍后重试'
      uploadError.value = msg
      toast.error(msg)
      return false
    } finally {
      uploading.value = false
    }
  }

  async function remove(id) {
    const toast = useToastStore()
    try {
      await deleteDocument(id)
      docs.value = docs.value.filter(d => d.id !== id)
      toast.success('文档已删除')
    } catch {
      toast.error('删除失败，请稍后重试')
    }
  }

  return { docs, uploading, uploadError, fetchDocuments, upload, remove }
})
