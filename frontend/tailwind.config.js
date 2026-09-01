/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{vue,js,ts,jsx,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // ── 品牌主色（保留原有游戏感红色系）──
        primary: {
          50: '#fff5f4',
          100: '#ffe3de',
          200: '#ffc8c0',
          300: '#ff9f93',
          400: '#ff6a62',
          500: '#ff4655',
          600: '#e13745',
          700: '#b92d38',
          800: '#812129',
          900: '#56161b',
        },
        // ── 次级金属色（琥珀/香槟，用于价格与荣誉感元素）──
        accent: {
          50: '#fffaee',
          100: '#fdefcf',
          200: '#f6d79c',
          300: '#e7bd67',
          400: '#cfa45e',
          500: '#a98349',
          600: '#896936',
          700: '#67502a',
          800: '#45361d',
          900: '#2b2112',
        },
        neon: {
          pink: '#ff4655',
          'pink-light': '#ff7c71',
          purple: '#8e86b6',
          'purple-light': '#b1a7dd',
          blue: '#82b8ff',
        },
        // ── 语义色：成功 / 警告 / 危险 / 信息 ──
        success: {
          soft: 'rgba(52, 211, 153, 0.12)',
          border: 'rgba(52, 211, 153, 0.28)',
          DEFAULT: '#34d399',
          dim: '#10b981',
          text: '#a7f3d0',
        },
        warning: {
          soft: 'rgba(251, 191, 36, 0.12)',
          border: 'rgba(251, 191, 36, 0.3)',
          DEFAULT: '#fbbf24',
          text: '#fde68a',
        },
        danger: {
          soft: 'rgba(255, 70, 85, 0.14)',
          border: 'rgba(255, 70, 85, 0.3)',
          DEFAULT: '#ff4655',
          bright: '#ff6a62',
          text: '#ffd6d2',
        },
        info: {
          soft: 'rgba(125, 211, 252, 0.1)',
          border: 'rgba(125, 211, 252, 0.28)',
          DEFAULT: '#7dd3fc',
          deep: '#38bdf8',
          text: '#e0f2fe',
        },
        // ── 表面 / 背景色阶：页面底 → 卡片 → 浮层 ──
        surface: {
          0: '#08090b',
          1: '#0d0f13',
          2: '#12151a',
          3: '#191d24',
          raised: '#20242d',
        },
        line: {
          soft: 'rgba(255, 255, 255, 0.07)',
          base: 'rgba(255, 255, 255, 0.1)',
          strong: 'rgba(255, 255, 255, 0.16)',
        },
        dark: {
          base: '#070809',
          surface: '#111317',
          elevated: '#1a1d23',
        },
      },
      borderRadius: {
        // ── 统一圆角体系 ──
        field: '14px',   // 输入框 / 下拉
        tile: '16px',    // 信息小块
        card: '20px',    // 卡片
        panel: '26px',   // 大面板 / hero
        bubble: '18px',  // 聊天气泡
      },
      boxShadow: {
        // ── 阴影与 subtle glow ──
        card: '0 18px 44px rgba(0, 0, 0, 0.32)',
        'card-hover': '0 26px 60px rgba(0, 0, 0, 0.42)',
        pop: '0 40px 110px rgba(0, 0, 0, 0.55)',
        panel: '0 30px 90px rgba(0, 0, 0, 0.42)',
        glow: '0 18px 45px rgba(255, 70, 85, 0.18)',
        'glow-neon': '0 0 20px rgba(255, 70, 85, 0.18)',
        'glow-pink': '0 0 20px rgba(255, 70, 85, 0.2)',
        'glow-purple': '0 0 20px rgba(130, 184, 255, 0.14)',
        'glow-info': '0 0 18px rgba(125, 211, 252, 0.16)',
      },
      transitionDuration: {
        fast: '150ms',
        base: '200ms',
      },
      transitionTimingFunction: {
        smooth: 'cubic-bezier(0.22, 0.8, 0.28, 1)',
      },
      animation: {
        float: 'float 7s ease-in-out infinite',
        'fade-up': 'fadeUp 0.7s ease-out both',
        'pulse-soft': 'pulse 2.4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        glitch: 'glitch 2s infinite',
        'glitch-once': 'glitch 0.8s ease-out 1',
        'scanline-move': 'scanlineMove 8s linear infinite',
        'neon-pulse': 'neonPulse 2s ease-in-out infinite',
        'flow-line': 'flowLine 3s linear infinite',
        shimmer: 'shimmer 8s linear infinite',
        // ── 全局动效 token ──
        'skeleton-shimmer': 'skeletonShimmer 1.6s ease-in-out infinite',
        'pop-in': 'popIn 0.22s cubic-bezier(0.22, 0.8, 0.28, 1) both',
        'scrim-in': 'scrimIn 0.2s ease-out both',
        'spin-soft': 'spin 1.1s linear infinite',
      },
      keyframes: {
        float: {
          '0%, 100%': { transform: 'translateY(0px)' },
          '50%': { transform: 'translateY(-10px)' },
        },
        fadeUp: {
          '0%': { opacity: '0', transform: 'translateY(14px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        glitch: {
          '0%, 100%': { transform: 'translate(0)' },
          '20%': { transform: 'translate(-2px, 2px)' },
          '40%': { transform: 'translate(-2px, -2px)' },
          '60%': { transform: 'translate(2px, 2px)' },
          '80%': { transform: 'translate(2px, -2px)' },
        },
        scanlineMove: {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100%)' },
        },
        neonPulse: {
          '0%, 100%': { boxShadow: '0 0 15px rgba(255, 70, 85, 0.18)' },
          '50%': { boxShadow: '0 0 30px rgba(255, 70, 85, 0.28)' },
        },
        flowLine: {
          '0%': { backgroundPosition: '0% 0%' },
          '100%': { backgroundPosition: '200% 0%' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0%' },
          '100%': { backgroundPosition: '200% 0%' },
        },
        skeletonShimmer: {
          '0%': { backgroundPosition: '160% 0' },
          '100%': { backgroundPosition: '-60% 0' },
        },
        popIn: {
          '0%': { opacity: '0', transform: 'translateY(14px) scale(0.97)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        scrimIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
