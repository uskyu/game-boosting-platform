/** @type {import('tailwindcss').Config} */

/**
 * Design System v2（docs/DESIGN.md 为唯一视觉规范）
 * - 全部颜色引用 CSS 变量（:root 亮色 / html.dark 暗色），见 src/assets/main.css 2.1 节 token 表
 * - rgb(var(--x-rgb) / <alpha-value>) 形式让 `bg-primary/10` 等透明度语法可用
 */
export default {
  content: [
    './index.html',
    './src/**/*.{vue,js,ts,jsx,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // ── 语义色（文档 2.1 表）──
        primary: {
          DEFAULT: 'rgb(var(--primary-rgb) / <alpha-value>)',
          soft: 'var(--primary-soft)',
        },
        price: {
          DEFAULT: 'rgb(var(--price-rgb) / <alpha-value>)',
          soft: 'var(--price-soft)',
        },
        success: {
          DEFAULT: 'rgb(var(--success-rgb) / <alpha-value>)',
          soft: 'var(--success-soft)',
        },
        warning: {
          DEFAULT: 'rgb(var(--warning-rgb) / <alpha-value>)',
          soft: 'var(--warning-soft)',
        },
        danger: {
          DEFAULT: 'rgb(var(--danger-rgb) / <alpha-value>)',
          soft: 'var(--danger-soft)',
        },
        info: {
          DEFAULT: 'rgb(var(--info-rgb) / <alpha-value>)',
          soft: 'var(--info-soft)',
        },
        // ── 表面系列 ──
        page: 'rgb(var(--bg-rgb) / <alpha-value>)',
        surface: {
          DEFAULT: 'rgb(var(--surface-rgb) / <alpha-value>)',
          2: 'rgb(var(--surface-2-rgb) / <alpha-value>)',
          3: 'rgb(var(--surface-3-rgb) / <alpha-value>)',
        },
        elevated: 'rgb(var(--elevated-rgb) / <alpha-value>)',
        // ── 文字层级 ──
        ink: {
          1: 'rgb(var(--text-1-rgb) / <alpha-value>)',
          2: 'rgb(var(--text-2-rgb) / <alpha-value>)',
          3: 'rgb(var(--text-3-rgb) / <alpha-value>)',
        },
        // ── 分隔线 ──
        line: {
          1: 'var(--line-1)',
          2: 'var(--line-2)',
          // 兼容旧类名（soft/base/strong 映射到两级线）
          soft: 'var(--line-1)',
          base: 'var(--line-1)',
          strong: 'var(--line-2)',
        },
        // ── 实底色上的文字（按钮/徽章）──
        'on-primary': 'var(--on-primary)',
        // ── 开关滑块 ──
        knob: 'var(--knob)',
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"SF Pro Text"',
          '"PingFang SC"',
          '"Microsoft YaHei"',
          'system-ui',
          'sans-serif',
        ],
      },
      borderRadius: {
        // 文档第 4 节圆角体系（保留旧类名，改数值）
        field: '10px',   // 输入框
        tile: '14px',    // 小卡
        card: '18px',    // 卡片
        panel: '22px',   // 大面板 / 弹窗
        bubble: '18px',  // 聊天气泡
      },
      boxShadow: {
        // 阴影值由 CSS 变量提供，亮暗两态各自调校（文档 4 节）
        card: 'var(--shadow-card)',
        'card-hover': 'var(--shadow-card-hover)',
        panel: 'var(--shadow-panel)',
        pop: 'var(--shadow-pop)',
        // 兼容旧类名：统一收敛为轻阴影 / primary 细环
        glow: 'var(--shadow-card-hover)',
        'glow-neon': '0 0 0 3px var(--primary-soft)',
        'glow-pink': 'var(--shadow-card-hover)',
        'glow-info': '0 0 0 3px var(--info-soft)',
      },
      transitionDuration: {
        fast: '150ms',
        base: '200ms',
      },
      transitionTimingFunction: {
        smooth: 'cubic-bezier(0.4, 0, 0.2, 1)',
      },
      animation: {
        'fade-up': 'fadeUp 0.2s cubic-bezier(0.4, 0, 0.2, 1) both',
        'pop-in': 'popIn 0.2s cubic-bezier(0.4, 0, 0.2, 1) both',
        'scrim-in': 'scrimIn 0.2s ease-out both',
        'spin-soft': 'spin 1.1s linear infinite',
        'skeleton-shimmer': 'skeletonShimmer 1.6s ease-in-out infinite',
        'pulse-soft': 'pulse 2.4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
      keyframes: {
        fadeUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        popIn: {
          '0%': { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        scrimIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        skeletonShimmer: {
          '0%': { backgroundPosition: '160% 0' },
          '100%': { backgroundPosition: '-60% 0' },
        },
      },
    },
  },
  plugins: [],
}
