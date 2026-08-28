module.exports = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Semantic tokens resolve to CSS variables so the whole UI flips
        // between dark/light via the `dark` class on <html> (see index.css
        // and the theme toggle in main.tsx).
        bg: "var(--kb-bg)", panel: "var(--kb-panel)", border: "var(--kb-border)",
        ink: "var(--kb-ink)", mute: "var(--kb-mute)", accent: "var(--kb-accent)",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "Segoe UI", "Inter", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
