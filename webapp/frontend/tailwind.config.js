/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0a0a0b",
          900: "#111113",
          850: "#17171a",
          800: "#1d1d21",
          700: "#2a2a30",
          600: "#3a3a42",
        },
        acid: {
          400: "#a3e635",
          500: "#84cc16",
          600: "#65a30d",
        },
        signal: {
          400: "#34d399",
          500: "#10b981",
          amber: "#f59e0b",
          red: "#f43f5e",
        },
      },
      fontFamily: {
        sans: ["Space Grotesk", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      boxShadow: {
        glass: "inset 0 1px 0 0 rgba(255,255,255,0.05), 0 20px 60px -20px rgba(0,0,0,0.7)",
        glow: "0 0 40px -8px rgba(132,204,22,0.35)",
      },
      backgroundImage: {
        grid: "linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px)",
      },
      keyframes: {
        shimmer: { "100%": { transform: "translateX(100%)" } },
      },
      animation: { shimmer: "shimmer 1.5s infinite" },
    },
  },
  plugins: [],
};
