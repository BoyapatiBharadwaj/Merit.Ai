/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      // Every colour goes through `rgb(var(--x) / <alpha-value>)` rather than
      // a bare `var(--x)`. The <alpha-value> placeholder is what lets Tailwind
      // generate opacity modifiers (bg-primary/10, border-danger/30, ...);
      // with a bare var it cannot compute an alpha and silently emits no rule
      // at all. See the token block at the top of index.css for the full
      // story -- the vars are channel triplets ("37 99 235"), not hex, to
      // make this work.
      colors: {
        primary: {
          DEFAULT: "rgb(var(--primary) / <alpha-value>)",
          dark: "rgb(var(--primary-dark) / <alpha-value>)",
        },
        danger: "rgb(var(--danger) / <alpha-value>)",
        warning: "rgb(var(--warning) / <alpha-value>)",
        success: "rgb(var(--success) / <alpha-value>)",
        page: "rgb(var(--bg) / <alpha-value>)",
        surface: "rgb(var(--card-bg) / <alpha-value>)",
        input: "rgb(var(--input-bg) / <alpha-value>)",
        border: "rgb(var(--border) / <alpha-value>)",
        ink: "rgb(var(--text) / <alpha-value>)",
        muted: "rgb(var(--text-muted) / <alpha-value>)",
        // Spotlight surface for the security section. Unlike the
        // always-dark `inverse` token it replaces, this one inverts with the
        // theme like every other surface.
        accent: "rgb(var(--surface-accent) / <alpha-value>)",
      },
      borderRadius: { DEFAULT: "var(--radius)", card: "var(--radius)" },
      boxShadow: { card: "var(--shadow)", glow: "0 0 0 1px var(--border), 0 20px 60px -20px rgba(37,99,235,0.35)" },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "Helvetica", "Arial", "sans-serif"],
      },
      keyframes: {
        fadeIn: { "0%": { opacity: 0 }, "100%": { opacity: 1 } },
        slideUp: { "0%": { opacity: 0, transform: "translateY(14px)" }, "100%": { opacity: 1, transform: "translateY(0)" } },
        slideInRight: { "0%": { opacity: 0, transform: "translateX(20px)" }, "100%": { opacity: 1, transform: "translateX(0)" } },
        pulseSoft: { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.55 } },
        floatY: { "0%,100%": { transform: "translateY(0)" }, "50%": { transform: "translateY(-8px)" } },
      },
      animation: {
        "fade-in": "fadeIn .5s ease both",
        "slide-up": "slideUp .5s ease both",
        "slide-in-right": "slideInRight .4s ease both",
        "pulse-soft": "pulseSoft 2s ease-in-out infinite",
        float: "floatY 4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
