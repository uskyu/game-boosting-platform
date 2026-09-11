import api from '@/utils/api'

export function base64ToUint8Array(value) {
  const padding = '='.repeat((4 - (value.length % 4)) % 4)
  const binary = atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(binary, (char) => char.charCodeAt(0))
}

export function pushCapability() {
  return typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

const vapidKey = import.meta.env.VITE_VAPID_PUBLIC_KEY || ''

export async function getPushStatus() {
  if (!pushCapability()) return { supported: false, permission: 'unsupported', subscribed: false }
  const registration = await navigator.serviceWorker.ready
  const subscription = await registration.pushManager.getSubscription()
  return { supported: true, permission: Notification.permission, subscribed: !!subscription, subscription }
}

export async function subscribeToPush() {
  if (!pushCapability()) throw new Error('当前浏览器不支持后台通知')
  if (Notification.permission !== 'granted') {
    const permission = await Notification.requestPermission()
    if (permission !== 'granted') throw new Error('通知权限未开启')
  }
  const keyResponse = await api.get('/push/public-key')
  const publicKey = keyResponse.data?.public_key || keyResponse.data?.publicKey || keyResponse.data?.key
  if (!publicKey) throw new Error('后台通知尚未配置')
  const registration = await navigator.serviceWorker.ready
  const existing = await registration.pushManager.getSubscription()
  const subscription = existing || await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: base64ToUint8Array(publicKey) })
  await api.post('/push/subscribe', { subscription: subscription.toJSON() })
  return subscription
}

export async function restorePushSubscription(options = {}) {
  const isCurrent = typeof options.isCurrent === 'function' ? options.isCurrent : () => true
  const status = await getPushStatus()
  if (!isCurrent()) return { ...status, stale: true }
  if (status.subscribed) {
    await api.post('/push/subscribe', { subscription: status.subscription.toJSON() })
    if (!isCurrent()) return { ...status, stale: true }
  }
  return status
}

export async function unsubscribeFromPush() {
  if (!pushCapability()) return
  const registration = await navigator.serviceWorker.ready
  const subscription = await registration.pushManager.getSubscription()
  if (!subscription) return
  try { await api.delete('/push/subscribe', { data: { endpoint: subscription.endpoint } }) } finally { await subscription.unsubscribe() }
}
