/* global self, clients */
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
self.addEventListener('push', (event) => {
  let data = {}
  try { data = event.data ? event.data.json() : {} } catch { data = { body: event.data?.text() || '' } }
  const title = data.title || '游戏服务平台'
  const options = { body: data.body || data.message || '', icon: data.icon || '/pwa-192x192.png', badge: data.badge, data: { link: data.link || '/' }, tag: data.tag }
  event.waitUntil(self.registration.showNotification(title, options))
})
self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const link = event.notification.data?.link || '/'
  event.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    const target = new URL(link, self.location.origin).href
    const existing = list.find((client) => client.url === target)
    if (existing) return existing.focus()
    return clients.openWindow(target)
  }))
})
