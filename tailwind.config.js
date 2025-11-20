/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.html', // Path ini akan memindai SEMUA file .html di dalam folder app
    './app/**/*.js'    // Ini untuk memindai file JavaScript juga
  ],
  theme: {
    extend: {
 colors: {
        'purple-medium': '#9256E9', // Ganti dengan kode warna Anda
        'purple-dark': '#7A3EDA',   // Ganti dengan kode warna Anda
      }
    },
  },
  plugins: [],
}