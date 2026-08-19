import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,                      // lắng nghe mọi interface -> tunnel chọc vào được
    // Cho phép domain đi qua Cloudflare Tunnel (Vite mặc định chặn host lạ).
    allowedHosts: ['ezdnu.vanhxyz.dev', '.vanhxyz.dev'],
  },
})
