<template>
  <div class="auth-ecosystem" aria-hidden="true">
    <svg class="auth-ecosystem__svg" viewBox="0 0 620 470" fill="none" xmlns="http://www.w3.org/2000/svg" role="img">
      <title>游戏服务平台订单生态</title>
      <defs>
        <filter id="auth-soft-shadow" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="14" stdDeviation="18" flood-color="var(--primary)" flood-opacity=".12" />
        </filter>
      </defs>
      <path class="ecosystem-line ecosystem-line--one" d="M54 175C126 72 212 76 286 142S428 242 566 122" />
      <path class="ecosystem-line ecosystem-line--two" d="M32 332C134 382 194 284 274 301s145 97 314 30" />
      <path class="ecosystem-line ecosystem-line--three" d="M113 61C176 123 180 203 140 267s-27 117 54 151" />
      <circle cx="54" cy="175" r="5" class="ecosystem-dot ecosystem-dot--primary" />
      <circle cx="566" cy="122" r="5" class="ecosystem-dot ecosystem-dot--success" />
      <circle cx="346" cy="347" r="5" class="ecosystem-dot ecosystem-dot--price" />

      <g class="ecosystem-card ecosystem-card--order" filter="url(#auth-soft-shadow)">
        <rect x="57" y="108" width="152" height="112" rx="20" class="ecosystem-panel" />
        <path d="M83 139h55M83 158h83M83 177h47" class="ecosystem-stroke ecosystem-stroke--muted" />
        <rect x="83" y="190" width="55" height="10" rx="5" class="ecosystem-fill ecosystem-fill--primary-soft" />
        <circle cx="181" cy="139" r="9" class="ecosystem-fill ecosystem-fill--success" />
        <path d="m177 139 3 3 5-6" class="ecosystem-stroke ecosystem-stroke--surface" />
      </g>

      <g class="ecosystem-card ecosystem-card--controller" filter="url(#auth-soft-shadow)">
        <rect x="218" y="177" width="184" height="126" rx="30" class="ecosystem-panel" />
        <path d="M262 239h28M276 225v28" class="ecosystem-stroke ecosystem-stroke--primary" />
        <circle cx="352" cy="227" r="7" class="ecosystem-fill ecosystem-fill--price" />
        <circle cx="371" cy="246" r="7" class="ecosystem-fill ecosystem-fill--success" />
        <path d="M249 278c18 10 34 10 48 0M327 278c17 10 35 10 49 0" class="ecosystem-stroke ecosystem-stroke--muted" />
      </g>

      <g class="ecosystem-card ecosystem-card--shield" filter="url(#auth-soft-shadow)">
        <path d="M449 77 530 107v64c0 58-34 87-81 104-47-17-81-46-81-104v-64l81-30Z" class="ecosystem-panel" />
        <path d="m415 168 23 23 47-53" class="ecosystem-stroke ecosystem-stroke--success ecosystem-stroke--wide" />
      </g>

      <g class="ecosystem-card ecosystem-card--wallet" filter="url(#auth-soft-shadow)">
        <rect x="407" y="315" width="144" height="87" rx="20" class="ecosystem-panel" />
        <path d="M407 340h144" class="ecosystem-stroke ecosystem-stroke--muted" />
        <path d="M483 359h44a10 10 0 0 1 10 10v9h-54a10 10 0 0 1 0-19Z" class="ecosystem-fill ecosystem-fill--primary-soft" />
        <circle cx="498" cy="368" r="4" class="ecosystem-fill ecosystem-fill--primary" />
      </g>
      <path d="m402 362 30 0m-13-11 13 11-13 11" class="ecosystem-stroke ecosystem-stroke--primary ecosystem-stroke--wide" />
    </svg>
    <div class="auth-ecosystem__caption"><span class="auth-ecosystem__status"></span>平台服务链路稳定运行</div>
  </div>
</template>
