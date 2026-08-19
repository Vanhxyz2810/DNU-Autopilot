// api.js — Lớp giao tiếp backend (trừu tượng hoá, dễ swap base URL).
// Mặc định "" = path tương đối -> gọi API ngay trên chính origin đang mở
// (khi backend serve luôn frontend build → cùng domain, không dính CORS).
// Dev tách 2 server: đặt VITE_API_BASE=http://localhost:8000 trong .env.local.
const BASE = import.meta.env.VITE_API_BASE ?? "";

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Lỗi ${res.status}`);
  return data;
}

async function get(path) {
  const res = await fetch(`${BASE}${path}`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Lỗi ${res.status}`);
  return data;
}

export const api = {
  base: BASE,
  login: (email, password, remember) => post("/api/login", { email, password, remember }),
  run: (session_id, course) => post("/api/run", { session_id, course }),
  result: (jobId) => get(`/api/result/${jobId}`),
  savedEmails: () => get("/api/saved-emails"),
  examStart: (session_id, course_id, lesson_id) =>
    post("/api/exam/start", { session_id, course_id, lesson_id }),
  examAnswer: (session_id, qid, values) =>
    post("/api/exam/answer", { session_id, qid, values }),
  examSubmit: (session_id, answers, synced) =>
    post("/api/exam/submit", { session_id, answers, synced }),
  examResult: (session_id) =>
    post("/api/exam/result", { session_id }),
  streamUrl: (jobId) => `${BASE}/api/stream/${jobId}`,
};
