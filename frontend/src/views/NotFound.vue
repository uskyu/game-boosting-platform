<script setup>
import { PAGE_BACKGROUNDS } from '@/data/gameImages.js'
</script>

<template>
  <div class="page-shell">
    <section
      class="hero-panel scanline-overlay relative flex min-h-[70vh] items-center overflow-hidden p-8 sm:p-10 lg:p-14"
      :style="{
        backgroundImage: `url('${PAGE_BACKGROUNDS.notFound}')`,
        backgroundPosition: 'center',
        backgroundSize: 'cover',
      }"
    >
      <div class="media-scrim" aria-hidden="true"></div>

      <div class="relative z-10 mx-auto max-w-xl space-y-6 text-center">
        <div class="text-7xl font-bold tabular-nums leading-none text-ink-1 sm:text-8xl">
          404
        </div>
        <h2 class="section-title">页面未找到</h2>
        <p class="mx-auto max-w-md text-sm leading-7 text-ink-2">
          这个入口可能已经挪走了。回大厅看看，或者直接搜索你想要的服务。
        </p>
        <div class="flex flex-wrap items-center justify-center gap-4 pt-4">
          <router-link to="/" class="btn-primary">
            返回
          </router-link>
        </div>
      </div>
    </section>
  </div>
</template>
