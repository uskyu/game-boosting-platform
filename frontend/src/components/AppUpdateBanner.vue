<script setup>
import { useAppUpdateStore } from '@/stores/appUpdate'

const appUpdate = useAppUpdateStore()
</script>

<template>
  <div v-if="appUpdate.available" class="app-update-banner" role="alert">
    <p class="app-update-banner__text">系统已更新，请刷新页面后再操作。</p>
    <button type="button" class="btn-primary app-update-banner__button" @click="appUpdate.reload()">
      立即刷新
    </button>
  </div>
</template>

<style scoped>
.app-update-banner {
  position: fixed;
  left: 50%;
  bottom: calc(env(safe-area-inset-bottom, 0px) + 84px);
  z-index: 90;
  display: flex;
  width: min(92vw, 520px);
  transform: translateX(-50%);
  align-items: center;
  gap: 12px;
  border: 1px solid var(--line-1);
  border-radius: 14px;
  background: var(--surface);
  padding: 10px 14px;
  box-shadow: var(--shadow-pop);
}

.app-update-banner__text {
  flex: 1;
  min-width: 0;
  color: var(--text-1);
  font-size: 14px;
  line-height: 1.5;
}

.app-update-banner__button {
  flex-shrink: 0;
  padding: 8px 16px;
}

@media (min-width: 768px) {
  .app-update-banner {
    bottom: 24px;
  }
}
</style>
