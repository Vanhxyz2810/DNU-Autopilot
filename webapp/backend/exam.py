# -*- coding: utf-8 -*-
"""exam.py — Trích đề & nộp bài thi thật NGAY TRONG APP (không bookmarklet).

Luồng nghiệp vụ (mobile-friendly, không cần thanh Bookmark):
  1. ``open_exam(creds, course_id, lesson_id)``:
       - GET ``/fullscreen-view/{cid}/{lid}`` -> lấy URL iframe ``/quizStart/...``.
       - GET trang ``/quizStart/...`` (BƯỚC NÀY ĐỐT 1 LƯỢT THI THẬT ở server).
       - Parse ra: câu hỏi + lựa chọn (cho user tick) và toàn bộ hidden field
         của ``#quizForm`` (``_token``, ``quiz_random_value``, ``quiz_test_id``...)
         -> gói vào ``ExamContext`` để dựng payload khi nộp.
  2. ``build_submit_payload(ctx, answers)``: ghép hidden field + đáp án user chọn
     thành payload cho ``POST /quizSubmit``.
  3. ``submit_exam(creds, ctx, answers)``: POST /quizSubmit bằng cookie phiên.

Defensive programming (rule MMO):
  - Mọi request có timeout + bắt lỗi rõ ràng (mạng rớt, 302 = hết lượt/chưa mở).
  - Không hardcode selector rải rác: gom vào hằng số ở đầu file.
  - ExamContext tách biệt data (đề) khỏi logic (fetch/submit) -> dễ test/bảo trì.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("exam")

# ─────────────────────────── Hằng số (no magic strings) ─────────────────────
BASE = "https://online.dainam.edu.vn"
SUBMIT_URL = f"{BASE}/quizSubmit"
# Endpoint chấm TỪNG câu. Bắt buộc phải gọi trước /quizSubmit, nếu không server
# ghi nhận 0đ dù /quizSubmit cuối có đủ đáp án (đã xác minh từ capture 9.5đ).
SINGLE_SUBMIT_URL = f"{BASE}/singleQuizSubmit"
QUIZ_TEST_START_URL = f"{BASE}/quizTestStart"
RESULT_URL_TPL = f"{BASE}/fullscreen-view/{{course_id}}/{{lesson_id}}?quiz_result_id={{quiz_test_id}}"
DEFAULT_TIMEOUT = 30
# time_diff: capture thực cho giá trị nhỏ (-20); server không kiểm tra chặt.
DEFAULT_TIME_DIFF = "-20"
# Selector bảng điểm kết quả (fullscreen-view?quiz_result_id=...).
SEL_RESULT_HISTORY = "#historyDiv"
SEL_RESULT_WRAPPER = ".quiz_score_wrapper"

# Selector DOM của engine đề (đã xác minh từ captures/quizstart_live_full.html).
SEL_QUIZ_FORM = "#quizForm"
SEL_QUESTION = ".singleQuestion"
SEL_Q_TEXT = ".multypol_qustion p"
SEL_Q_NOTE = ".multypol_qustion small"
SEL_OPTION_LI = "ul.quiz_select li"
SEL_OPTION_INPUT = "input.quizAns"
SEL_OPTION_TITLE = ".radio-title, .checkbox-title"

# URL iframe đề nằm trong trang fullscreen-view, dạng data-src / src.
_IFRAME_QUIZ_RE = re.compile(r'(?:data-src|src)="([^"]*?/quizStart/[^"]+)"')


# ─────────────────────────── Kiểu dữ liệu (strict typing) ───────────────────
@dataclass
class ExamOption:
    value: str
    text: str


@dataclass
class ExamQuestion:
    qid: str                       # data-qus-id (vd "343")
    qtype: str                     # data-qus-type (vd "M" = multiple choice)
    input_name: str                # name thật của input (vd "ans[343][]")
    is_multi: bool                 # checkbox (nhiều đáp án) hay radio
    text: str
    note: str
    options: list[ExamOption]


@dataclass
class ExamContext:
    """Ngữ cảnh 1 lần mở đề — đủ để dựng payload nộp lại chính xác."""
    course_id: str
    lesson_id: str
    quiz_start_url: str
    hidden_fields: dict[str, str] = field(default_factory=dict)
    questions: list[ExamQuestion] = field(default_factory=list)
    # quiz_test_id do /quizTestStart cấp (bắt buộc để nộp, HTML để rỗng).
    quiz_test_id: str = ""

    def to_client(self) -> dict[str, Any]:
        """Payload gửi cho frontend (KHÔNG lộ hidden field nhạy cảm như _token)."""
        return {
            "course_id": self.course_id,
            "lesson_id": self.lesson_id,
            "count": len(self.questions),
            "questions": [
                {
                    "qid": q.qid,
                    "type": q.qtype,
                    "is_multi": q.is_multi,
                    "text": q.text,
                    "note": q.note,
                    "options": [{"value": o.value, "text": o.text} for o in q.options],
                }
                for q in self.questions
            ],
        }

    def as_clipboard_text(self) -> str:
        """Đề dạng text để user copy dán sang AI khác hỏi đáp án."""
        lines: list[str] = []
        for i, q in enumerate(self.questions, 1):
            lines.append(f"Câu {i}: {q.text}")
            for j, o in enumerate(q.options):
                letter = chr(ord("A") + j)
                lines.append(f"   {letter}. {o.text}")
            lines.append("")
        return "\n".join(lines).strip()


# ─────────────────────────── HTTP helpers (abstraction) ─────────────────────
def _session_from_creds(creds: dict) -> requests.Session:
    """Tạo requests.Session mang cookie + UA của phiên user (anti-detect: giữ UA thật)."""
    s = requests.Session()
    s.headers.update({
        "Cookie": creds["cookie"],
        "User-Agent": creds.get("user-agent", "Mozilla/5.0"),
    })
    return s


class ExamError(Exception):
    """Lỗi nghiệp vụ khi mở/nộp đề (thân thiện để hiển thị cho user)."""


# ─────────────────────────── Parse HTML đề ──────────────────────────────────
def parse_exam_html(html: str, course_id: str, lesson_id: str,
                    quiz_start_url: str) -> ExamContext:
    """Bóc câu hỏi + hidden field từ HTML trang /quizStart. Pure function -> dễ test."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one(SEL_QUIZ_FORM)
    if not form:
        raise ExamError("Không tìm thấy form đề (#quizForm). Có thể đề chưa mở hoặc hết lượt.")

    ctx = ExamContext(course_id=course_id, lesson_id=lesson_id,
                      quiz_start_url=quiz_start_url)

    # 1) Hidden field toàn cục (bỏ các input câu hỏi ans[]/type[]/question[]/assign[]).
    for inp in form.select("input[type=hidden]"):
        name = inp.get("name")
        if not name or re.match(r"^(ans|type|question|assign)\[", name):
            continue
        ctx.hidden_fields[name] = inp.get("value", "")

    # 2) Từng câu hỏi.
    for block in form.select(SEL_QUESTION):
        qid = block.get("data-qus-id", "?")
        qtype = block.get("data-qus-type", "M")
        qtext_el = block.select_one(SEL_Q_TEXT)
        qtext = qtext_el.get_text(" ", strip=True) if qtext_el else "(không đọc được đề)"
        note_el = block.select_one(SEL_Q_NOTE)
        note = note_el.get_text(" ", strip=True) if note_el else ""

        options: list[ExamOption] = []
        input_name = f"ans[{qid}][]"
        is_multi = False
        for li in block.select(SEL_OPTION_LI):
            inp = li.select_one(SEL_OPTION_INPUT)
            if not inp:
                continue
            input_name = inp.get("name", input_name)
            if (inp.get("type") or "radio").lower() == "checkbox":
                is_multi = True
            title_el = li.select_one(SEL_OPTION_TITLE)
            text = (title_el.get_text(" ", strip=True) if title_el
                    else li.get_text(" ", strip=True))
            options.append(ExamOption(value=inp.get("value", "?"), text=text))

        ctx.questions.append(ExamQuestion(
            qid=qid, qtype=qtype, input_name=input_name, is_multi=is_multi,
            text=qtext, note=note, options=options,
        ))
    return ctx


def _extract_quiz_url(fullscreen_html: str) -> Optional[str]:
    """Tìm URL iframe /quizStart trong trang fullscreen-view."""
    m = _IFRAME_QUIZ_RE.search(fullscreen_html)
    return m.group(1) if m else None


def _post_quiz_test_start(sess: requests.Session, ctx: ExamContext) -> str:
    """Khởi tạo lượt thi -> lấy quiz_test_id (field bắt buộc cho /quizSubmit).

    Payload đúng theo capture thật:
      courseId&quizId&quizType&quiz_test_id=&random_value&focus_lost=0&lesson_id&_token
    Response: {"result": true, "data": {"id": <quiz_test_id>, ...}}
    """
    hf = ctx.hidden_fields
    payload = {
        "courseId": hf.get("courseId", ctx.course_id),
        "quizId": hf.get("quizId", ""),
        "quizType": hf.get("quizType", "2"),
        "quiz_test_id": "",
        "random_value": hf.get("quiz_random_value", ""),
        "focus_lost": "0",
        "lesson_id": hf.get("lesson_id", ctx.lesson_id),
        "_token": hf.get("_token", ""),
    }
    try:
        r = sess.post(QUIZ_TEST_START_URL, data=payload, timeout=DEFAULT_TIMEOUT,
                      headers={"X-Requested-With": "XMLHttpRequest",
                               "Referer": ctx.quiz_start_url})
    except requests.RequestException as e:
        raise ExamError(f"Lỗi mạng khi khởi tạo lượt thi: {e}") from e
    if r.status_code != 200:
        raise ExamError(f"/quizTestStart trả {r.status_code} (có thể đã hết lượt).")
    try:
        data = r.json()
    except ValueError as e:
        raise ExamError(f"/quizTestStart trả về không phải JSON: {e}") from e
    test_id = (data.get("data") or {}).get("id")
    if not test_id:
        raise ExamError(f"Không lấy được quiz_test_id từ /quizTestStart: {data}")
    return str(test_id)


# ─────────────────────────── Public API ────────────────────────────────────
def open_exam(creds: dict, course_id: str, lesson_id: str) -> ExamContext:
    """Mở đề & trích câu hỏi. CẢNH BÁO: server tính đây là 1 lượt thi thật."""
    sess = _session_from_creds(creds)

    # B1: lấy URL iframe đề từ trang fullscreen-view.
    fv_url = f"{BASE}/fullscreen-view/{course_id}/{lesson_id}"
    try:
        r = sess.get(fv_url, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        raise ExamError(f"Lỗi mạng khi mở trang bài thi: {e}") from e
    quiz_url = _extract_quiz_url(r.text)
    if not quiz_url:
        raise ExamError("Không tìm thấy đề thi trong bài (thiếu iframe /quizStart).")

    # B2: tải trang đề thật (ĐỐT LƯỢT).
    try:
        r2 = sess.get(quiz_url, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        raise ExamError(f"Lỗi mạng khi tải đề: {e}") from e
    if r2.status_code != 200:
        raise ExamError(f"Server trả {r2.status_code} khi mở đề (có thể đã hết lượt).")

    ctx = parse_exam_html(r2.text, course_id, lesson_id, quiz_url)
    if not ctx.questions:
        raise ExamError("Mở được đề nhưng không parse được câu hỏi nào.")

    # B3: khởi tạo lượt thi để lấy quiz_test_id (bắt buộc cho /quizSubmit).
    ctx.quiz_test_id = _post_quiz_test_start(sess, ctx)
    return ctx


def build_submit_payload(ctx: ExamContext,
                         answers: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Ghép hidden field + đáp án user chọn thành payload POST /quizSubmit.

    ``answers``: {qid: [option_value, ...]}. Dùng list[tuple] để hỗ trợ
    nhiều ``ans[qid][]`` (câu chọn nhiều đáp án).
    """
    payload: list[tuple[str, str]] = []
    for name, value in ctx.hidden_fields.items():
        # Override các field động mà HTML để rỗng (JS điền lúc chạy).
        if name == "quiz_test_id":
            value = ctx.quiz_test_id
        elif name == "time_diff":
            value = DEFAULT_TIME_DIFF
        elif name == "from" and not value:
            value = "course"  # HTML để rỗng; capture thật gửi "course".
        # start_at giữ RỖNG đúng như capture thật.
        payload.append((name, value))
    # Phòng khi HTML thiếu input quiz_test_id/time_diff -> bổ sung.
    names = {n for n, _ in payload}
    if "quiz_test_id" not in names:
        payload.append(("quiz_test_id", ctx.quiz_test_id))
    if "time_diff" not in names:
        payload.append(("time_diff", DEFAULT_TIME_DIFF))

    for q in ctx.questions:
        # Field bắt buộc mỗi câu (engine cần để chấm).
        payload.append((f"type[{q.qid}]", q.qtype))
        payload.append((f"question[{q.qid}]", q.qid))
        payload.append((f"assign[{q.qid}]", q.qid))
        for val in answers.get(q.qid, []):
            payload.append((q.input_name, val))
    return payload


def _submit_session(creds: dict, ctx: ExamContext) -> requests.Session:
    """Tạo session có cookie + header chuẩn cho các POST nộp bài.

    Tách riêng để cả ``submit_single_question`` (nộp từng câu khi chọn) lẫn
    ``submit_exam`` (chốt bài) dùng chung -> tránh lặp code, dễ bảo trì.
    """
    sess = _session_from_creds(creds)
    sess.headers.update({
        "Referer": ctx.quiz_start_url,
        "Origin": BASE,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    return sess


def submit_single_question(sess: requests.Session, ctx: ExamContext,
                           q: ExamQuestion, values: list[str]) -> bool:
    """Nộp & chấm 1 câu qua /singleQuizSubmit (bước BẮT BUỘC để có điểm).

    Payload đúng capture 9.5đ:
        quiz_test_id & assign_id=<qid> & type=<qtype> & ans[]=<value> & focus_lost=0 & _token

    Defensive: không raise để 1 câu lỗi không chặn cả bài; trả True/False theo status.
    """
    payload: list[tuple[str, str]] = [
        ("quiz_test_id", ctx.quiz_test_id),
        ("assign_id", q.qid),
        ("type", q.qtype),
    ]
    for val in values:
        payload.append(("ans[]", val))
    payload.append(("focus_lost", "0"))
    payload.append(("_token", ctx.hidden_fields.get("_token", "")))
    try:
        r = sess.post(SINGLE_SUBMIT_URL, data=payload, timeout=DEFAULT_TIMEOUT,
                      headers={"X-Requested-With": "XMLHttpRequest",
                               "Referer": ctx.quiz_start_url})
    except requests.RequestException as e:  # noqa: BLE001
        logger.warning("singleQuizSubmit qid=%s lỗi mạng: %s", q.qid, e)
        return False
    if r.status_code != 200:
        logger.warning("singleQuizSubmit qid=%s trả %s", q.qid, r.status_code)
        return False
    return True


def submit_exam(creds: dict, ctx: ExamContext,
                answers: dict[str, list[str]],
                already_synced: Optional[list[str]] = None) -> dict[str, Any]:
    """Chốt bài thật (/quizSubmit).

    Cơ chế mới (sync-on-answer): các câu đã được nộp ngay lúc user chọn qua
    ``/api/exam/answer`` -> ``already_synced`` chứa qid đã lưu thành công.
    Ở đây ta CHỈ gửi bù (/singleQuizSubmit) các câu CHƯA synced (defensive:
    mạng chậm, user nộp sớm) rồi mới POST /quizSubmit. Nếu mọi câu đã synced,
    bước chốt gần như tức thì.
    """
    sess = _submit_session(creds, ctx)
    synced = set(already_synced or [])

    # B1: GỬI BÙ các câu chưa kịp sync (an toàn, tránh mất điểm).
    single_total = 0
    single_ok = 0
    for q in ctx.questions:
        vals = answers.get(q.qid) or []
        if not vals:
            continue
        single_total += 1
        if q.qid in synced:
            single_ok += 1
            continue
        if submit_single_question(sess, ctx, q, vals):
            single_ok += 1
        time.sleep(random.uniform(0.2, 0.6))

    # B2: CHỐT bài.
    payload = build_submit_payload(ctx, answers)
    # DEBUG: lưu payload thật + đáp án user để soi khi bị 0đ.
    try:
        import json as _json, pathlib
        dbg = pathlib.Path(__file__).resolve().parents[2] / "captures" / "last_submit_payload.json"
        dbg.write_text(_json.dumps({
            "answers_from_user": answers,
            "quiz_test_id": ctx.quiz_test_id,
            "questions": [{"qid": q.qid, "input_name": q.input_name,
                           "options": [{"value": o.value, "text": o.text} for o in q.options]}
                          for q in ctx.questions],
            "payload": payload,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    try:
        r = sess.post(SUBMIT_URL, data=payload, timeout=DEFAULT_TIMEOUT,
                      allow_redirects=False)
    except requests.RequestException as e:
        raise ExamError(f"Lỗi mạng khi nộp bài: {e}") from e

    ok = r.status_code in (200, 302)
    return {
        "ok": ok,
        "status": r.status_code,
        "redirect": r.headers.get("Location"),
        "answered": sum(1 for q in ctx.questions if answers.get(q.qid)),
        "total": len(ctx.questions),
        "single_ok": single_ok,
        "single_total": single_total,
    }


# ─────────────────────── Lấy kết quả (poll) ─────────────────────
def parse_result_html(html: str) -> dict[str, Any]:
    """Bóc điểm từ trang kết quả. Pure function -> dễ test.

    DOM thật (đã xác minh từ last_result_poll.html):
      .quiz_score_wrapper > .quiz_test_body > .score_view_wrapper chứa:
        - 2 <li> câu trả lời ĐÚNG / SAI (số nằm trong .checkbox-title).
        - cột "Điểm" -> <h4> "x /10".
      Khi CHƯA chấm xong: KHÔNG có .score_view_wrapper (chỉ có dòng
      "Hệ thống đang chấm điểm ở nền...").

    Trả về:
      {"pending": True}  hoặc
      {"pending": False, "score": "0", "max": "10", "correct": 0,
       "wrong": 20, "total": 20, "percent": "0%", "rating": "...",
       "start": "...", "end": "...", "duration": "..."}
    """
    soup = BeautifulSoup(html, "html.parser")
    view = soup.select_one(".score_view_wrapper")
    if not view:
        # Chưa chấm xong (hoặc trang chưa có bảng điểm).
        return {"pending": True}

    text = view.get_text(" ", strip=True)

    def _num_before(label: str) -> Optional[int]:
        # Bắt "<số> ... <label>" (số câu đúng/sai đứng trước nhãn).
        m = re.search(r"(\d+)\s*" + re.escape(label), text, re.IGNORECASE)
        return int(m.group(1)) if m else None

    correct = _num_before("Câu trả lời đúng")
    wrong = _num_before("Câu trả lời sai")

    # Điểm "x /10": lấy cụm số/10 cuối cùng trong wrapper.
    score = max_score = None
    ms = re.search(r"(\d+(?:[.,]\d+)?)\s*/\s*(\d+)", text)
    if ms:
        score, max_score = ms.group(1), ms.group(2)

    # Thời gian bắt đầu/kết thúc (định dạng YYYY-MM-DD HH:MM:SS).
    times = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text)
    dur = re.search(r"\(Ph[uú]t\)\s*(\d+(?:[.,]\d+)?)", text)

    total = None
    if correct is not None and wrong is not None:
        total = correct + wrong
    percent = ""
    if score is not None and max_score:
        try:
            percent = f"{round(float(score.replace(',', '.')) / float(max_score) * 100)}%"
        except (ValueError, ZeroDivisionError):
            percent = ""

    return {
        "pending": False,
        "score": score or "",
        "max": max_score or "10",
        "correct": correct,
        "wrong": wrong,
        "total": total,
        "percent": percent,
        "start": times[0] if len(times) > 0 else "",
        "end": times[1] if len(times) > 1 else "",
        "duration": dur.group(1) if dur else "",
    }


def fetch_result(creds: dict, ctx: ExamContext) -> dict[str, Any]:
    """GET trang kết quả & parse điểm. Gọi lặp (poll) từ phía main/frontend.

    Không tự sleep/loop ở đây (single responsibility): mỗi lần gọi = 1 lần poll.
    """
    if not ctx.quiz_test_id:
        raise ExamError("Chưa có quiz_test_id để tra kết quả (chưa mở đề?).")
    sess = _session_from_creds(creds)
    url = RESULT_URL_TPL.format(course_id=ctx.course_id, lesson_id=ctx.lesson_id,
                                quiz_test_id=ctx.quiz_test_id)
    try:
        r = sess.get(url, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        raise ExamError(f"Lỗi mạng khi lấy kết quả: {e}") from e
    if r.status_code != 200:
        raise ExamError(f"Trang kết quả trả {r.status_code}.")
    # DEBUG: lưu HTML poll gần nhất để soi khi parser trả pending mãi.
    try:
        import pathlib
        dbg = pathlib.Path(__file__).resolve().parents[2] / "captures" / "last_result_poll.html"
        dbg.write_text(r.text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return parse_result_html(r.text)
