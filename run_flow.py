#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_flow.py — FLOW ĐẦY ĐỦ tự động hoá 1 khoá trên online.dainam.edu.vn.

Các bước (dùng lại creds.json do grab_session.py sinh ra):
  1. Kiểm tra phiên đăng nhập.
  2. Liệt kê bài CHƯA xong:            GET  /university/course-to-learn
  3. Với từng bài:
       a. Mở trang học (không follow redirect): GET /fullscreen-view/{c}/{l}
          -> lấy hash gói SCORM Storyline (uploads/scorm/{hash}/)
       b. Phát hiện bài THI THẬT (engine /quizStart/, vd "KT giữa kỳ") ->
          KHÔNG đụng tới, bỏ qua hoàn toàn (không bypass lock, không
          complete) — các bài này giới hạn số lượt thật ở server, phải để
          người dùng tự làm qua browser thật (xem grab_session.py).
       c. Còn lại: tải data.js của gói SCORM Storyline -> phân loại:
            • có câu hỏi  => QUIZ  -> MOI ĐÁP ÁN ĐÚNG
            • không       => VIDEO/bài giảng
       d. Đánh dấu hoàn thành:          POST /lesson-complete-ajax
  4. Xuất báo cáo: captures/flow_report.md  +  captures/answers.json
     (liệt kê rõ những bài thi thật nào bị bỏ qua, cần tự làm)

DÙNG:
  ./.venv/Scripts/python.exe run_flow.py --dry-run   # chỉ đọc + moi đáp án, KHÔNG đánh dấu
  ./.venv/Scripts/python.exe run_flow.py             # làm thật (đánh dấu hoàn thành)
  ./.venv/Scripts/python.exe run_flow.py --course 794 --skip-quiz   # bỏ qua quiz
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import auto_complete as AC
import extract_answers as EA

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "https://online.dainam.edu.vn"
SCORM_RE = re.compile(r"uploads/scorm/([0-9a-fA-F]+)/")
# Engine "AdvanceQuiz" = bài thi THẬT (có thể giám sát webcam, giới hạn số
# lượt làm bài enforce ở server — đã kiểm chứng: KT giữa kỳ TTHCM chỉ cho
# đúng 2 lượt). KHÔNG BAO GIỜ tự động bypass các mục này — để người dùng tự
# làm thật qua browser (grab_session.py), tránh lỡ đốt lượt thi thật.
EXAM_RE = re.compile(r"/quizStart/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def get_page(path, creds):
    req = urllib.request.Request(BASE + path)
    req.add_header("Cookie", creds["cookie"])
    req.add_header("User-Agent", creds.get("user-agent", "Mozilla/5.0"))
    try:
        r = _opener.open(req, timeout=30)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""       # 302 (khoá) rơi vào đây
    except Exception as e:
        return -1, repr(e)


def quiz_answers(scorm_hash):
    """Trả về list[list[str]] đáp án đúng mỗi câu, hoặc None nếu không phải quiz."""
    url = f"{BASE}/public/uploads/scorm/{scorm_hash}/html5/data/js/data.js"
    try:
        data = EA.parse_storyline(EA.fetch(url))
    except Exception:
        return None
    inter = []
    EA.walk(data, inter)
    if not inter:
        return None
    out = []
    for it in inter:
        _, correct = EA.solve(it)
        out.append(correct)
    return out


def main():
    ap = argparse.ArgumentParser(description="Flow tự động hoàn thành khoá DNU")
    ap.add_argument("--course", help="Chỉ làm 1 khoá (vd 794)")
    ap.add_argument("--dry-run", action="store_true", help="Chỉ đọc + moi đáp án, KHÔNG đánh dấu")
    ap.add_argument("--skip-quiz", action="store_true", help="Không đánh dấu các bài quiz")
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args()

    creds = AC.load_creds()
    if not AC.check_session(creds):
        sys.exit("❌ Phiên hết hạn — chạy lại grab_session.py để đăng nhập.")
    courses = AC.enumerate_courses(creds)
    if not isinstance(courses, dict):
        courses = {}
    print(f"✅ Phiên OK. {len(courses)} khoá còn bài chưa xong.\n")
    if not courses:
        print("🎉 Không còn bài nào chưa hoàn thành.")
        return

    results = []
    for cid, c in courses.items():
        if args.course and str(cid) != str(args.course):
            continue
        title = (c.get("course_title") or {}).get("vi") or cid
        print(f"═══ Khoá {cid}: {title} ═══")
        lessons = [(l, ch.get("chapter_name"))
                   for ch in c.get("chapters", {}).values()
                   for l in ch.get("lessons", [])]
        print(f"  {len(lessons)} bài chưa xong.\n")

        for les, chap in lessons:
            lid = str(les["lesson_id"])
            ltitle = les.get("lesson_title") or "(không tên)"
            st, html = get_page(f"/fullscreen-view/{cid}/{lid}", creds)
            is_exam = bool(EXAM_RE.search(html)) if st == 200 else False
            hashes = sorted(set(SCORM_RE.findall(html))) if st == 200 and not is_exam else []
            answers = quiz_answers(hashes[0]) if hashes else None

            if is_exam:
                kind = "🎓 THI THẬT (bỏ qua, tự làm)"
            elif st != 200 and not hashes:
                kind = "🔒 khoá/redirect"
            elif answers:
                kind = f"📝 QUIZ ({len(answers)} câu)"
            elif hashes:
                kind = "🎬 video/bài giảng"
            else:
                kind = "❔ không rõ"

            # đánh dấu hoàn thành (KHÔNG BAO GIỜ bypass bài thi thật)
            comp = None
            is_quiz = bool(answers)
            if is_exam:
                pass
            elif not args.dry_run and not (is_quiz and args.skip_quiz):
                cst, cj, craw = AC.complete(creds, cid, lid)
                comp = cj or {"http": cst, "raw": str(craw)[:80]}

            line = f"  [{lid}] {kind}  — {ltitle[:60]}"
            if is_exam:
                line += "  ⏭️ ĐÃ BỎ QUA — hãy tự làm bài thi này thật"
            elif comp:
                if comp.get("new_lesson_complete"):
                    line += "  ✅ VỪA XONG"
                elif comp.get("success"):
                    line += "  ⚪ đã xong"
                else:
                    line += f"  🔴 lỗi {comp}"
            elif args.dry_run:
                line += "  (dry-run)"
            print(line)
            if answers:
                flat = [", ".join(a) if a else "?" for a in answers]
                print("       Đáp án: " + " | ".join(f"C{i+1}:{v}" for i, v in enumerate(flat)))

            results.append({
                "course_id": cid, "lesson_id": lid, "chapter": chap,
                "title": ltitle, "page_status": st, "scorm_hash": hashes[0] if hashes else None,
                "kind": kind, "is_exam": is_exam, "n_questions": len(answers) if answers else 0,
                "answers": answers, "complete": comp,
            })
            time.sleep(args.delay)
        print()

    # ---- lưu báo cáo ----
    outdir = Path("captures")
    outdir.mkdir(exist_ok=True)
    (outdir / "answers.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# Flow report\n"]
    done = sum(1 for r in results if (r.get("complete") or {}).get("new_lesson_complete"))
    already = sum(1 for r in results if (r.get("complete") or {}).get("success")
                  and not (r.get("complete") or {}).get("new_lesson_complete"))
    quizzes = [r for r in results if r["n_questions"]]
    exams = [r for r in results if r.get("is_exam")]
    md.append(f"- Tổng bài xử lý: **{len(results)}** | vừa hoàn thành: **{done}** | "
              f"đã xong sẵn: **{already}** | quiz có đáp án: **{len(quizzes)}** | "
              f"⏭️ bài thi thật bị bỏ qua: **{len(exams)}**\n")
    if exams:
        md.append("**Các bài thi thật cần tự làm (chưa đụng tới):**")
        for r in exams:
            md.append(f"  - [{r['lesson_id']}] {r['title']} — `/fullscreen-view/{r['course_id']}/{r['lesson_id']}`")
        md.append("")
    for r in results:
        md.append(f"## [{r['lesson_id']}] {r['title']}")
        md.append(f"- Loại: {r['kind']} | trang: {r['page_status']} | hash: `{r['scorm_hash']}`")
        if r["answers"]:
            for i, a in enumerate(r["answers"], 1):
                md.append(f"  - Câu {i}: **{', '.join(a) if a else '?'}**")
        md.append(f"- Hoàn thành: `{r['complete']}`\n")
    (outdir / "flow_report.md").write_text("\n".join(md), encoding="utf-8")

    print("═" * 60)
    print(f"XONG. Vừa hoàn thành {done} | đã xong sẵn {already} | quiz {len(quizzes)} | "
          f"⏭️ bài thi bỏ qua {len(exams)}")
    if exams:
        print("   → Các bài thi thật cần TỰ LÀM:")
        for r in exams:
            print(f"     [{r['lesson_id']}] {r['title']}")
    print(f"Báo cáo: captures/flow_report.md | Đáp án: captures/answers.json")
    print("═" * 60)


if __name__ == "__main__":
    main()
