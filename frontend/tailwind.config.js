/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,jsx,ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Warm paper palette — the heart of the redesign.
        background: 'var(--background)',
        surface: 'var(--surface)',
        'surface-2': 'var(--surface-2)',
        ink: {
          DEFAULT: 'var(--text)',
          dim: 'var(--text-dim)',
          faint: 'var(--text-faint)',
        },
        hairline: 'var(--border)',
        'hairline-strong': 'var(--border-strong)',
        // Status — desaturated, "calm" tones.
        ok: 'var(--ok)',
        warn: 'var(--warn)',
        danger: 'var(--danger)',
        info: 'var(--info)',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Cascadia Mono', 'Menlo', 'monospace'],
      },
      fontSize: {
        // Editorial type scale.
        'display-1': ['3.5rem', { lineHeight: '1.05', letterSpacing: '-0.025em', fontWeight: '500' }],
        'display-2': ['2.5rem', { lineHeight: '1.1',  letterSpacing: '-0.02em',  fontWeight: '500' }],
        'h1':         ['1.875rem', { lineHeight: '1.2',  letterSpacing: '-0.015em', fontWeight: '500' }],
        'h2':         ['1.5rem',   { lineHeight: '1.3',  letterSpacing: '-0.01em',  fontWeight: '500' }],
        'h3':         ['1.25rem',  { lineHeight: '1.35', letterSpacing: '-0.005em', fontWeight: '500' }],
        'body':       ['0.9375rem', { lineHeight: '1.55' }],
        'sm':         ['0.8125rem', { lineHeight: '1.5' }],
        'xs':         ['0.75rem',   { lineHeight: '1.4',  letterSpacing: '0.01em' }],
      },
      borderRadius: {
        sm: '4px',
        DEFAULT: '8px',
        md: '10px',
        lg: '12px',
        xl: '16px',
        '2xl': '20px',
      },
      boxShadow: {
        // A single soft elevation for floating surfaces.
        floating:
          '0 1px 2px rgba(28, 28, 28, 0.04), 0 8px 24px rgba(28, 28, 28, 0.06), 0 24px 48px -12px rgba(28, 28, 28, 0.08)',
        soft: '0 1px 2px rgba(28, 28, 28, 0.04)',
      },
      keyframes: {
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to:   { opacity: '1' },
        },
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
        'caret-blink': {
          '0%, 50%':   { opacity: '1' },
          '50.01%, 100%': { opacity: '0' },
        },
      },
      animation: {
        shimmer: 'shimmer 1.6s linear infinite',
        'fade-in': 'fade-in 180ms ease-out',
        'fade-up': 'fade-up 240ms cubic-bezier(0.16, 1, 0.3, 1)',
        'caret-blink': 'caret-blink 1s steps(1) infinite',
      },
    },
  },
  plugins: [],
};
