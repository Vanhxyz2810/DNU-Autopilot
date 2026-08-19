import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api } from "../api";

export default function LoginForm({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState([]);

  useEffect(() => {
    api.savedEmails().then((r) => setSaved(r.emails || [])).catch(() => {});
  }, []);

  async function submit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await api.login(email.trim(), password, remember);
      onLogin(res);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen grid lg:grid-cols-[1.1fr_1fr]">
      {/* Panel trái — thương hiệu */}
      <div className="relative hidden lg:flex flex-col justify-center items-center gap-10 overflow-hidden bg-ink-900 p-12">
        <div className="absolute inset-0 bg-grid bg-[size:32px_32px] opacity-60" />
        <div className="absolute -left-40 top-1/3 h-96 w-96 rounded-full bg-acid-500/10 blur-3xl" />

        <div className="relative w-full max-w-lg">
          <div className="flex items-center gap-3">
            <img src="/logo.jpg" alt="DNU Autopilot"
                 className="h-11 w-11 rounded-xl object-cover ring-1 ring-acid-500/30" />
            <div className="pill bg-acid-500/10 text-acid-400 border border-acid-500/20">
              <span className="h-1.5 w-1.5 rounded-full bg-acid-400 animate-pulse" />
              SYSTEM ONLINE
            </div>
          </div>

          <h1 className="mt-8 font-mono text-6xl font-bold leading-none tracking-tight text-zinc-100">
            DNU<br />AUTO<span className="text-acid-400">PILOT</span>
          </h1>
          <p className="mt-6 text-zinc-500">
            @Vanhxyz
          </p>

          {/* Ảnh minh hoạ dashboard — rộng full khối để lấp khoảng trống */}
          <div className="mt-8 overflow-hidden rounded-2xl border border-white/10 shadow-2xl">
            <img src="/login-hero.jpg" alt="Bảng điều khiển tự động hoá"
                 className="w-full object-cover" />
          </div>
        </div>

        <div className="relative flex items-center justify-between font-mono text-xs text-zinc-600 self-stretch">
          <div className="flex gap-4">
            <span>// 6-THREAD</span>
            <span>// SSE REALTIME</span>
            <span>// FERNET</span>
          </div>
          <a href="https://github.com/Vanhxyz2810" target="_blank" rel="noreferrer"
             className="text-acid-500/70 transition hover:text-acid-400">
            @Vanhxyz
          </a>
        </div>
      </div>

      {/* Panel phải — form */}
      <div className="flex items-center justify-center p-6 sm:p-12">
        <motion.form
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          onSubmit={submit}
          className="w-full max-w-sm space-y-5"
        >
          <div className="flex items-center gap-3">
            <img src="/logo.jpg" alt="Logo"
                 className="h-10 w-10 rounded-lg object-cover ring-1 ring-acid-500/30 lg:hidden" />
            <div>
              <h2 className="text-2xl font-semibold text-zinc-100">Đăng nhập</h2>
              <p className="mt-1 text-sm text-zinc-500">Dùng tài khoản Microsoft 365 của trường.</p>
            </div>
          </div>

          <div className="space-y-3">
            <input
              id="email"
              className="field"
              placeholder="mssv@dnu.edu.vn"
              value={email}
              list="saved-emails"
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <datalist id="saved-emails">
              {saved.map((em) => <option key={em} value={em} />)}
            </datalist>
            <input
              id="password"
              type="password"
              className="field"
              placeholder="Mật khẩu (bỏ trống nếu đã lưu)"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          <label className="flex items-center gap-2 text-sm text-zinc-400 select-none">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              className="h-4 w-4 rounded border-white/20 bg-ink-800 accent-acid-500"
            />
            Ghi nhớ mật khẩu
          </label>

          {error && (
            <div className="rounded-xl border border-signal-red/30 bg-signal-red/10 px-4 py-3 text-sm text-signal-red">
              {error}
            </div>
          )}

          <button type="submit" className="btn-primary w-full" disabled={loading}>
            {loading ? "Đang đăng nhập…" : "Kết nối →"}
          </button>

          <p className="text-center font-mono text-[11px] text-zinc-600">
            Nếu tài khoản bật MFA, hệ thống sẽ báo lỗi (không tự vượt).
          </p>
          <p className="text-center text-[11px] text-zinc-700">
            Made by{" "}
            <a href="https://github.com/Vanhxyz2810" target="_blank" rel="noreferrer"
               className="font-mono text-acid-500/70 transition hover:text-acid-400">
              @Vanhxyz
            </a>
          </p>
        </motion.form>
      </div>
    </div>
  );
}
