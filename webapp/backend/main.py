# -*- coding: utf-8 -*-
"""main.py — FastAPI app: login + autopilot + extract exam (multi-user safe).

Chạy:
    ./.venv/Scripts/python.exe -m uvicorn webapp.backend.main:app --reload --port 8000

Kiến trúc chống conflict/lag (yêu cầu #6):
  - Mỗi người dùng 1 UserSession UUID cô lập (session_store).
  - Blocking logic (requests + ThreadPoolExecutor) chạy trong run_in_executor
    -> KHÔNG block event loop -> nhiều SSE stream song song mượt.
  - Semaphore giới hạn số job đồng thời -> vượt quota thì xếp hàng, không sập.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import login_request as LR          # noqa: E402
from webapp.backend import exam as EXAM  # noqa: E402
from webapp.backend import runner   # noqa: E402
from webapp.backend.crypto import PasswordCipher  # noqa: E402
from webapp.backend.session_store import (  # noqa: E402
    JobState, JobStatus, registry,
)

logging.getLogger("login_request").setLevel(logging.WARNING)
logger = logging.getLogger("webapp")

app = FastAPI(title="DNU E-Learning Autopilot API", version="1.0.0")

# Localhost + Cloudflare Tunnel -> cho phép mọi origin (tool nội bộ).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Giới hạn số job autopilot chạy đồng thời (chống lag). 4 job x 6 luồng = 24.
MAX_CONCURRENT_JOBS = 4
_job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

# Nơi lưu mật khẩu đã mã hoá (Fernet) — key trong .env.
_cipher = PasswordCipher(env_path=str(_ROOT / ".env"))
_CRED_STORE = _ROOT / "captures" / "saved_logins.json"


# ─────────────────────────── Models ────────────────────────────────────────
class LoginBody(BaseModel):
    email: str
    password: str
    remember: bool = True


class RunBody(BaseModel):
    session_id: str
    course: str | None = None


class ExamStartBody(BaseModel):
    session_id: str
    course_id: str
    lesson_id: str


class ExamSubmitBody(BaseModel):
    session_id: str
    answers: dict[str, list[str]]
    # qid đã nộp thành công lúc user chọn (sync-on-answer) -> chốt bài chỉ
    # cần gửi bù các câu chưa có trong danh sách này.
    synced: list[str] = []


class ExamAnswerBody(BaseModel):
    session_id: str
    qid: str
    values: list[str]


class ExamResultBody(BaseModel):
    session_id: str


# ─────────────────────────── Lưu mật khẩu (mã hoá) ──────────────────────────
def _load_saved() -> dict:
    if _CRED_STORE.exists():
        try:
            return json.loads(_CRED_STORE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_password(email: str, password: str) -> None:
    """Lưu mật khẩu đã mã hoá Fernet (KHÔNG plaintext)."""
    data = _load_saved()
    data[email] = _cipher.encrypt(password)
    _CRED_STORE.parent.mkdir(exist_ok=True)
    _CRED_STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")


# ─────────────────────────── Endpoints ─────────────────────────────────────
@app.get("/api/saved-emails")
async def saved_emails() -> dict:
    """Danh sách email đã lưu (không trả mật khẩu)."""
    return {"emails": list(_load_saved().keys())}


@app.post("/api/login")
async def login(body: LoginBody) -> dict:
    """Đăng nhập SSO. Chạy blocking trong executor để không block event loop."""
    email = body.email.strip()
    password = body.password

    # Nếu password để trống nhưng đã lưu trước -> dùng lại (đã giải mã).
    if not password:
        saved = _load_saved().get(email)
        if saved:
            password = _cipher.decrypt(saved) or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="Thiếu email hoặc mật khẩu.")

    loop = asyncio.get_running_loop()

    def _do_login() -> dict:
        client = LR.DNUSSOClient()
        resp = client.authenticate(email, password)
        creds = client.build_creds(resp)
        if not creds:
            raise LR.LoginError("Không lấy được cookie/CSRF sau đăng nhập.")
        return creds

    try:
        creds = await loop.run_in_executor(None, _do_login)
    except LR.MFARequiredError as e:
        raise HTTPException(status_code=409, detail=f"MFA: {e}")
    except LR.LoginError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Lỗi mạng/SSO: {e}")

    if body.remember:
        _save_password(email, password)

    sess = await registry.create(email, creds)
    return {"session_id": sess.session_id, "email": email}


@app.post("/api/run")
async def run(body: RunBody) -> dict:
    """Khởi động job autopilot nền cho 1 phiên. Trả job_id ngay."""
    sess = await registry.get(body.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")

    if sess.job and sess.job.status in (JobStatus.RUNNING, JobStatus.QUEUED):
        return {"job_id": sess.job.job_id, "status": sess.job.status.value}

    job = JobState(job_id=sess.session_id)  # 1 job/phiên
    job.status = JobStatus.QUEUED
    sess.job = job

    asyncio.create_task(_run_job(sess, body.course))
    return {"job_id": job.job_id, "status": job.status.value}


async def _run_job(sess, course: str | None) -> None:
    """Chạy generator runner trong executor, đẩy sự kiện vào queue của job."""
    job = sess.job
    async with _job_semaphore:
        job.status = JobStatus.RUNNING
        loop = asyncio.get_running_loop()
        creds = sess.creds
        gen = runner.run_autopilot(creds, course)

        def _next(g):
            try:
                return next(g)
            except StopIteration:
                return None

        try:
            while True:
                event = await loop.run_in_executor(None, _next, gen)
                if event is None:
                    break
                if event.get("type") == "total":
                    job.total = event.get("total", 0)
                elif event.get("type") == "lesson":
                    job.completed += 1
                    job.results.append(event["payload"])
                elif event.get("type") == "done":
                    job.results = event.get("results", job.results)
                elif event.get("type") == "error":
                    job.error = event.get("message")
                await job.queue.put(event)
            job.status = JobStatus.ERROR if job.error else JobStatus.DONE
        except Exception as e:  # noqa: BLE001
            job.error = str(e)
            job.status = JobStatus.ERROR
            await job.queue.put({"type": "error", "message": str(e)})
        finally:
            await job.queue.put({"type": "_end"})  # sentinel đóng SSE


@app.get("/api/stream/{job_id}")
async def stream(job_id: str) -> StreamingResponse:
    """SSE: đẩy sự kiện tiến độ realtime cho client."""
    sess = await registry.get(job_id)
    if not sess or not sess.job:
        raise HTTPException(status_code=404, detail="Job không tồn tại.")
    job = sess.job

    async def event_gen():
        # Gửi snapshot hiện tại trước (để client reload lấy lại trạng thái).
        yield f"data: {json.dumps({'type': 'snapshot', 'payload': job.snapshot()})}\n\n"
        while True:
            event = await job.queue.get()
            if event.get("type") == "_end":
                yield f"data: {json.dumps({'type': 'end'})}\n\n"
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/api/result/{job_id}")
async def result(job_id: str) -> dict:
    """Lấy kết quả tổng hợp (checkpoint) — dùng khi client reload trang."""
    sess = await registry.get(job_id)
    if not sess or not sess.job:
        raise HTTPException(status_code=404, detail="Job không tồn tại.")
    return sess.job.snapshot()


@app.post("/api/exam/start")
async def exam_start(body: ExamStartBody) -> dict:
    """Mở đề & trích câu hỏi ngay trong app (mobile-friendly).

    CẢNH BÁO: bước này đốt 1 lượt thi thật ở server. Chạy blocking
    trong executor để không block event loop.
    """
    sess = await registry.get(body.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")

    loop = asyncio.get_running_loop()
    try:
        ctx = await loop.run_in_executor(
            None, EXAM.open_exam, sess.creds, body.course_id, body.lesson_id)
    except EXAM.ExamError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Loi mo de: {e}")

    sess.exam_ctx = ctx  # giữ để dựng payload khi nộp
    data = ctx.to_client()
    data["clipboard"] = ctx.as_clipboard_text()
    return data


@app.post("/api/exam/submit")
async def exam_submit(body: ExamSubmitBody) -> dict:
    """Nộp bài thật lên /quizSubmit bằng đáp án user đã chọn."""
    sess = await registry.get(body.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    if not sess.exam_ctx:
        raise HTTPException(status_code=409, detail="Chưa mở đề. Hãy bấm 'Trích đề' trước.")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, EXAM.submit_exam, sess.creds, sess.exam_ctx,
            body.answers, body.synced)
    except EXAM.ExamError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Loi nop bai: {e}")
    return result


@app.post("/api/exam/answer")
async def exam_answer(body: ExamAnswerBody) -> dict:
    """Nộp & chấm NGAY 1 câu khi user chọn đáp án (sync-on-answer).

    Đây là cách trang thi thật hoạt động: mỗi lần tick, JS gọi
    /singleQuizSubmit ngầm. Rải request theo nhịp bấm người thật -> tốt cho
    anti-detect và giúp bước 'Nộp bài' cuối gần như tức thì.

    Defensive: không raise nếu 1 câu lỗi mạng -> trả ok=false để frontend retry.
    """
    sess = await registry.get(body.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    if not sess.exam_ctx:
        raise HTTPException(status_code=409, detail="Chưa mở đề. Hãy bấm 'Trích đề' trước.")

    ctx = sess.exam_ctx
    q = next((x for x in ctx.questions if x.qid == body.qid), None)
    if q is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy câu {body.qid}.")

    loop = asyncio.get_running_loop()

    def _do() -> bool:
        s = EXAM._submit_session(sess.creds, ctx)
        return EXAM.submit_single_question(s, ctx, q, body.values)

    try:
        ok = await loop.run_in_executor(None, _do)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Loi luu cau: {e}")
    return {"ok": ok, "qid": body.qid}


@app.post("/api/exam/result")
async def exam_result(body: ExamResultBody) -> dict:
    """1 lần poll kết quả bài thi (điểm). Frontend gọi lặp mỗi 10s.

    Server chấm ở nền nên trả {pending: true} cho đến khi có điểm.
    """
    sess = await registry.get(body.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    if not sess.exam_ctx:
        raise HTTPException(status_code=409, detail="Chưa mở đề nên chưa có kết quả.")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, EXAM.fetch_result, sess.creds, sess.exam_ctx)
    except EXAM.ExamError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Loi lay ket qua: {e}")
    return result


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


# ───────────── Serve frontend build (SPA) ─────────────────────────
# Gộp FE + BE vào 1 origin -> chỉ cần 1 Cloudflare Tunnel, không dính CORS.
# Đặt ở CUỐI FILE để các route /api/* được ưu tiên khớp trước.
_DIST = _ROOT / "webapp" / "frontend" / "dist"
if _DIST.is_dir():
    # assets/ (JS, CSS có hash) -> serve trực tiếp.
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):  # noqa: ANN201
        """Trả file tĩnh nếu tồn tại, ngược lại trả index.html (SPA routing)."""
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_DIST / "index.html"))
else:
    logger.warning("Chưa có frontend/dist. Chạy 'npm run build' để serve web từ backend.")
