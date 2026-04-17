/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/hiive_monitor/web/templates/**/*.html",
    "./src/hiive_monitor/web/templates/*.html",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
      colors: {
        neutral: {
          50: "#fafafa",
          100: "#f5f5f5",
          200: "#e5e5e5",
          300: "#d4d4d4",
          400: "#a3a3a3",
          500: "#737373",
          600: "#525252",
          700: "#404040",
          800: "#262626",
          900: "#171717",
        },
        accent: {
          DEFAULT: "#2563eb",
          hover: "#1d4ed8",
        },
        alert: {
          DEFAULT: "#dc2626",
          hover: "#b91c1c",
        },
      },
    },
  },
  plugins: [],
};
