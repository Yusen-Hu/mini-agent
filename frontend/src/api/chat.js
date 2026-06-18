export function chatStream(message, sessionId, signal) {
  return fetch('/api/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${localStorage.getItem('access_token')}`,
    },
    body: JSON.stringify({ message, session_id: sessionId || undefined }),
    signal,
  })
}
