import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ["Space Grotesk", "Segoe UI", "sans-serif"],
        body: ["Manrope", "Segoe UI", "sans-serif"],
      },
      colors: {
        ink: "#f0f6ff",
        paper: "#020617",
        ocean: "#3B82F6",
        mint: "#00FFB2",
        amber: "#F59E0B",
        coral: "#FF2E5B",
        violet: "#8B5CF6",
        ice: "#06B6D4",
      },
      boxShadow: {
        panel: "0 30px 80px rgba(0, 0, 0, 0.45)",
      },
    },
  },
  plugins: [],
} satisfies Config;
