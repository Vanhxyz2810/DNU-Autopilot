# DNU Autopilot

Web app tự động hoàn thành bài giảng E-Learning (API thuần, đa luồng) và trích
đề thi để **tự làm ngay trong app** — dùng được cả trên điện thoại.

> Made by [@Vanhxyz](https://github.com/Vanhxyz2810)

---

## Kiến trúc

```
webapp/
  backend/    FastAPI (login, autopilot, trích & nộp đề)
  frontend/   React + Vite + Tailwind
login_request.py, auto_complete.py, run_flow.py, extract_answers.py
            # module lõi backend dùng chung
```

## Yêu cầu
- Python 3.10+
- Node.js 18+

## Cài đặt

```bash
# 1. Backend
python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\activate
pip install -r requirements.txt

# 2. Cấu hình biến môi trường
cp .env.example .env
# sinh FERNET_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# dán vào FERNET_KEY trong .env

# 3. Frontend
cd webapp/frontend
npm install
```

## Chạy (dev)

```bash
# Terminal 1 — backend (cổng 8000)
python -m uvicorn webapp.backend.main:app --port 8000 --host 0.0.0.0

# Terminal 2 — frontend (cổng 5173)
cd webapp/frontend && npm run dev
```

## Deploy lên VPS (production)

```bash
# 1. Build frontend tĩnh
cd webapp/frontend
npm run build          # -> webapp/frontend/dist

# 2. Chạy backend bằng uvicorn (nhiều worker) hoặc gunicorn
python -m uvicorn webapp.backend.main:app --host 0.0.0.0 --port 8000

# 3. Nginx: serve dist/ và reverse-proxy /api -> 127.0.0.1:8000
```

Gợi ý dùng `systemd` hoặc `pm2` để giữ backend chạy nền, và cấu hình
`VITE_API_BASE` (frontend) trỏ đúng domain API khi build.

> [!WARNING]
> Công cụ dùng cho mục đích học tập/tự động hoá cá nhân. Không tự động nộp bài
> thi hộ — người dùng phải chủ động chọn đáp án và bấm nộp.
