# -*- coding: utf-8 -*-
"""runner.py — Điều phối autopilot cho 1 phiên, dạng GENERATOR yield tiến độ.

Refactor logic từ ``dnu.run_course`` để tái sử dụng 100% core:
  - ``AC.enumerate_courses`` / ``AC.complete``
  - ``RF.get_page`` / ``RF.quiz_answers``
  - Giữ nguyên ``ThreadPoolExecutor(6)`` (yêu cầu #5).

Điểm khác CLI:
  - Không in ra console; thay vào đó ``yield`` từng sự kiện (dict) để tầng web
    đẩy qua SSE.
  - Nhận ``creds`` như tham số (per-session) -> KHÔNG đọc file dùng chung.

Defensive: mỗi bài bọc try/except riêng -> 1 bài lỗi không làm sập cả job.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterator

# Cho phép import module core ở thư mục gốc dự án.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import auto_complete as AC   # noqa: E402
import run_flow as RF        # noqa: E402

MAX_WORKERS = 6              # giữ nguyên số luồng như CLI

KIND_LABEL = {
    "exam": "Thi thật",
    "quiz": "Quiz",
    "video": "Bài giảng",
    "unknown": "Không rõ",
}


def _classify_and_process(cid: str, les: dict, creds: dict) -> dict[str, Any]:
    """Xử lý trọn 1 bài trong thread riêng — không đụng state chung."""
    lid = str(les["lesson_id"])
    ltitle = les.get("lesson_title") or "(không tên)"

    st, html = RF.get_page(f"/fullscreen-view/{cid}/{lid}", creds)
    is_exam = bool(RF.EXAM_RE.search(html)) if st == 200 else False
    hashes = sorted(set(RF.SCORM_RE.findall(html))) if st == 200 and not is_exam else []
    answers = RF.quiz_answers(hashes[0]) if hashes else None

    if is_exam:
        kind = "exam"
    elif answers:
        kind = "quiz"
    elif hashes:
        kind = "video"
    else:
        kind = "unknown"

    comp = None
    if not is_exam:
        # KHÔNG BAO GIỜ bypass bài thi thật — chỉ complete bài giảng/quiz.
        _cst, cj, _craw = AC.complete(creds, cid, lid)
        comp = cj or {}

    if is_exam:
        result = "skipped"
    elif comp and comp.get("new_lesson_complete"):
        result = "completed"
    elif comp and comp.get("success"):
        result = "already"
    else:
        result = "error"

    return {
        "course_id": cid, "lesson_id": lid, "title": ltitle,
        "is_exam": is_exam, "kind": kind, "kind_label": KIND_LABEL[kind],
        "answers": answers, "result": result,
    }


def run_autopilot(creds: dict, course_filter: str | None = None) -> Iterator[dict[str, Any]]:
    """Generator: yield sự kiện tiến độ cho toàn bộ phiên autopilot.

    Loại sự kiện (``type``):
      - ``session``: thông tin phiên (tổng số khoá).
      - ``course_start``: bắt đầu 1 khoá.
      - ``total``: tổng số bài của toàn job (sau khi liệt kê xong).
      - ``lesson``: kết quả 1 bài (payload = dict từ _classify_and_process).
      - ``done``: kết thúc, kèm tổng kết.
      - ``error``: lỗi fatal.
    """
    try:
        courses = AC.enumerate_courses(creds)
        if not isinstance(courses, dict):
            courses = {}
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "message": f"Không liệt kê được khoá học: {e}"}
        return

    if course_filter:
        courses = {cid: c for cid, c in courses.items() if str(cid) == str(course_filter)}

    if not courses:
        yield {
            "type": "done",
            "results": [],
            "summary": {"total": 0},
            "message": "Tài khoản này đã hoàn thành tất cả bài — không còn gì để chạy.",
        }
        return

    # Gom toàn bộ (cid, lesson) để biết tổng số bài -> progress chính xác.
    all_lessons: list[tuple[str, dict]] = []
    for cid, c in courses.items():
        for ch in c.get("chapters", {}).values():
            for les in ch.get("lessons", []):
                all_lessons.append((cid, les))

    yield {"type": "total", "total": len(all_lessons), "courses": len(courses)}

    all_results: list[dict] = []
    # Đa luồng: xử lý song song nhưng giới hạn MAX_WORKERS.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_classify_and_process, cid, les, creds): (cid, les)
            for cid, les in all_lessons
        }
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001
                cid, les = futures[fut]
                r = {
                    "course_id": cid, "lesson_id": str(les.get("lesson_id")),
                    "title": les.get("lesson_title") or "(không tên)",
                    "is_exam": False, "kind": "unknown", "kind_label": "Không rõ",
                    "answers": None, "result": "error", "error": str(e),
                }
            all_results.append(r)
            yield {"type": "lesson", "payload": r}

    # Tổng kết
    done = sum(1 for r in all_results if r["result"] == "completed")
    already = sum(1 for r in all_results if r["result"] == "already")
    quizzes = sum(1 for r in all_results if r["answers"])
    exams = sum(1 for r in all_results if r["is_exam"])
    yield {
        "type": "done",
        "summary": {
            "total": len(all_results),
            "completed": done,
            "already": already,
            "quizzes": quizzes,
            "exams": exams,
        },
        "results": all_results,
    }
