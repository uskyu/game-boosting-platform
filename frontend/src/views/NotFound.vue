<script setup>
import { PAGE_BACKGROUNDS } from '@/data/gameImages.js'
</script>

<template>
  <div class="page-shell">
    <section
      class="hero-panel scanline-overlay flex min-h-[70vh] items-center p-8 sm:p-10 lg:p-14"
      :style="{
        backgroundImage: `linear-gradient(135deg, rgba(10,10,15,0.93), rgba(18,18,26,0.9)), url('${PAGE_BACKGROUNDS.notFound}')`,
        backgroundPosition: 'center',
        backgroundSize: 'cover',
      }"
    >
      <div class="mx-auto max-w-xl space-y-6 text-center">
        <div
          class="glitch-text cyber-gradient text-8xl font-black animate-glitch sm:text-9xl"
          data-text="404"
        >
          404
        </div>
        <h2 class="section-title neon-text">页面未找到</h2>
        <p class="mx-auto max-w-md text-sm leading-7 text-slate-400">
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
