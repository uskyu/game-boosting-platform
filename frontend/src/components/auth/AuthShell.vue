<script setup>
import AnimatedCharacters from './AnimatedCharacters.vue'

defineProps({
  eyebrow: { type: String, default: 'GAME BOOSTING PLATFORM' },
  title: { type: String, default: '让每一次游戏服务，都清晰可达。' },
  description: { type: String, default: '连接需求与专业服务者，订单、结算与钱包在同一条可靠链路上。' },
  spotlightLabel: { type: String, default: '' },
  spotlightTitle: { type: String, default: '' },
  spotlightMeta: { type: String, default: '' },
})

const benefits = ['大厅实时抢单', '完单自动结算', '提现人工处理']
</script>

<template>
  <div class="auth-stage">
    <div class="auth-stage__backdrop" aria-hidden="true"></div>
    <div class="auth-stage__inner shell-container">
      <section class="auth-story">
        <div class="auth-story__heading">
          <p class="auth-story__eyebrow">{{ eyebrow }}</p>
          <h1 class="auth-story__title">{{ title }}</h1>
          <p class="auth-story__copy">{{ description }}</p>
        </div>
        <AnimatedCharacters />
        <ul class="auth-story__benefits" aria-label="平台服务特点">
          <li v-for="benefit in benefits" :key="benefit">
            <span class="auth-story__check" aria-hidden="true">✓</span>
            <span>{{ benefit }}</span>
          </li>
        </ul>
      </section>
      <section class="auth-panel"><slot /></section>
    </div>
  </div>
</template>
