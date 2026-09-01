/**
 * Vue Router configuration.
 */

import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/HomeView.vue'),
    meta: { title: '首页' },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', guest: true },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/RegisterView.vue'),
    meta: { title: '注册', guest: true },
  },
  {
    path: '/orders',
    name: 'orders',
    component: () => import('@/views/OrderList.vue'),
    meta: { title: '订单列表', requiresAuth: true },
  },
  {
    path: '/games',
    name: 'games',
    component: () => import('@/views/GameCategoryView.vue'),
    meta: { title: '游戏分类' },
  },
  {
    path: '/games/:id',
    name: 'game-zone',
    component: () => import('@/views/GameZoneView.vue'),
    meta: { title: '游戏专区' },
    props: true,
  },
  {
    path: '/services',
    name: 'services',
    component: () => import('@/views/ServiceListView.vue'),
    meta: { title: '陪玩服务' },
  },
  {
    path: '/services/:id',
    name: 'service-detail',
    component: () => import('@/views/ServiceDetailView.vue'),
    meta: { title: '服务详情' },
    props: true,
  },
  {
    path: '/booster/:id',
    name: 'booster-profile',
    component: () => import('@/views/BoosterProfileView.vue'),
    meta: { title: '代练主页' },
    props: true,
  },
  {
    path: '/search',
    name: 'search',
    component: () => import('@/views/SearchResultView.vue'),
    meta: { title: '搜索结果' },
  },
  {
    path: '/orders/create',
    name: 'order-create',
    component: () => import('@/views/OrderCreate.vue'),
    meta: { title: '发布订单', requiresAuth: true },
  },
  {
    path: '/orders/:id',
    name: 'order-detail',
    component: () => import('@/views/OrderDetail.vue'),
    meta: { title: '订单详情', requiresAuth: true },
    props: true,
  },
  {
    path: '/messages',
    name: 'message-center',
    component: () => import('@/views/MessageCenterView.vue'),
    meta: { title: '消息中心', requiresAuth: true },
  },
  {
    path: '/chat',
    name: 'chat-list',
    redirect: { name: 'message-center', query: { tab: 'chat' } },
  },
  {
    path: '/chat/:id',
    name: 'chat-detail',
    component: () => import('@/views/ChatDetailView.vue'),
    meta: { title: '聊天详情', requiresAuth: true },
    props: true,
  },
  {
    path: '/support',
    name: 'support',
    component: () => import('@/views/CustomerServiceView.vue'),
    meta: { title: 'AI客服', requiresAuth: true },
  },
  {
    path: '/notifications',
    name: 'notifications',
    redirect: { name: 'message-center', query: { tab: 'notifications' } },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/SettingsView.vue'),
    meta: { title: '设置', requiresAuth: true },
  },
  {
    path: '/profile',
    name: 'profile',
    component: () => import('@/views/ProfileView.vue'),
    meta: { title: '个人中心', requiresAuth: true },
  },
  {
    path: '/wallet',
    name: 'wallet',
    component: () => import('@/views/WalletView.vue'),
    meta: { title: '我的钱包', requiresAuth: true },
  },
  {
    path: '/admin',
    name: 'admin',
    component: () => import('@/views/AdminView.vue'),
    meta: { title: '管理台', requiresAuth: true, adminOnly: true },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFound.vue'),
    meta: { title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) {
      return savedPosition
    }

    if (to.hash) {
      return {
        el: to.hash,
        top: 96,
        behavior: 'smooth',
      }
    }

    return { top: 0 }
  },
})

router.beforeEach(async (to, from, next) => {
  document.title = `${to.meta.title || '游戏服务平台'} - 游戏服务平台`

  const authStore = useAuthStore()
  if (authStore.accessToken && !authStore.user) {
    await authStore.fetchCurrentUser()
  }

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    next({ name: 'login', query: { redirect: to.fullPath } })
    return
  }

  if (to.meta.adminOnly && !authStore.isAdmin) {
    next({ name: 'home' })
    return
  }

  if (to.meta.guest && authStore.isAuthenticated) {
    next({ name: 'home' })
    return
  }

  next()
})

export default router
