/**
 * Vue application entry point.
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'

import './assets/main.css'

// Create Vue application
const app = createApp(App)

// Install Pinia
const pinia = createPinia()
app.use(pinia)

// Install Vue Router
app.use(router)

// Mount application
app.mount('#app')
