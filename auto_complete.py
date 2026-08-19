#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_complete.py — Hoàn thành bài giảng trên online.dainam.edu.vn bằng cách
gọi THẲNG API, không cần xem video (bỏ qua cơ chế chống tua/skip).

QUY TRÌNH API (đã reverse từ grab_session.py):
  • Liệt kê bài học:  GET  /university/course-to-learn   -> JSON khoá→chương→bài
  • Đánh dấu xong:    POST /lesson-complete-ajax          body: course_id, lesson_id
                      header: X-CSRF-TOKEN + X-Requested-With: XMLHttpRequest
                      -> {"success":true,"new_lesson_complete":true|false}
                         new_lesson_complete=true  => bài VỪA được đánh dấu xong
                         new_lesson_complete=false => bài đã xong sẵn từ trước

XÁC THỰC: dùng cookie phiên + CSRF token lấy từ captures/creds.json
  (do grab_session.py sinh ra). Phiên Laravel sống ~2 giờ; hết hạn thì chạy lại
  grab_session.py để đăng nhập lấy phiên mới.

DÙNG:
  # Xem danh sách bài (không đụng gì):
  ./.venv/Scripts/python.exe auto_complete.py --list

  # Thử 1 bài để kiểm chứng:
  ./.venv/Scripts/python.exe auto_complete.py --course 794 --lessons 19138

  # Hoàn thành toàn bộ khoá đang học:
  ./.venv/Scripts/python.exe auto_complete.py --all

  # Chỉ in ra sẽ làm gì, chưa gửi:
  ./.venv/Scripts/python.exe auto_complete.py --all --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "https://online.dainam.edu.vn"
CREDS_FILE = "captures/creds.json"


def load_creds():
    p = Path(CREDS_FILE)
    if not p.exists():
        sys.exit(f"❌ Không thấy {CREDS_FILE}. Hãy chạy grab_session.py để đăng nhập trước.")
    c = json.loads(p.read_text(encoding="utf-8"))
    if not c.get("cookie") or not c.get("x-csrf-token"):
        sys.exit("❌ creds.json thiếu cookie hoặc x-csrf-token. Chạy lại grab_session.py.")
    return c


def _headers(creds, ajax=True, referer=BASE + "/student-dashboard"):
    h = {
        "Cookie": creds["cookie"],
        "User-Agent": creds.get("user-agent", "Mozilla/5.0"),
        "Origin": BASE,
        "Referer": referer,
        "Accept": "*/*",
    }
    if ajax:
        h["X-CSRF-TOKEN"] = creds["x-csrf-token"]
        h["X-Requested-With"] = "XMLHttpRequest"
    return h


def http(method, path, creds, data=None, referer=BASE + "/student-dashboard"):
    url = path if path.startswith("http") else BASE + path
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in _headers(creds, referer=referer).items():
        req.add_header(k, v)
    if data:
        req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check_session(creds) -> bool:
    """Trả về True nếu còn đăng nhập (course-to-learn trả JSON)."""
    st, body = http("GET", "/university/course-to-learn", creds)
    if st != 200:
        return False
    s = body.lstrip()
    return s.startswith("{") or s.startswith("[")


def enumerate_courses(creds) -> dict:
    st, body = http("GET", "/university/course-to-learn", creds)
    try:
        return json.loads(body)
    except Exception:
        return {}


def complete(creds, course_id, lesson_id):
    referer = f"{BASE}/fullscreen-view/{course_id}/{lesson_id}"
    st, body = http("POST", "/lesson-complete-ajax", creds,
                    data={"course_id": str(course_id), "lesson_id": str(lesson_id)},
                    referer=referer)
    try:
        j = json.loads(body)
    except Exception:
        j = None
    return st, j, body


def main():
    ap = argparse.ArgumentParser(description="Tự động hoàn thành bài giảng DNU E-Learning")
    ap.add_argument("--list", action="store_true", help="Chỉ liệt kê khoá/bài")
    ap.add_argument("--all", action="store_true", help="Hoàn thành mọi bài trong mọi khoá đang học")
    ap.add_argument("--course", help="ID khoá cụ thể (vd 794)")
    ap.add_argument("--lessons", help="Danh sách lesson_id, cách nhau dấu phẩy (vd 19138,19139)")
    ap.add_argument("--dry-run", action="store_true", help="Chỉ in ra, không gửi")
    ap.add_argument("--delay", type=float, default=0.8, help="Nghỉ giữa các request (giây)")
    args = ap.parse_args()

    creds = load_creds()
    if not check_session(creds):
        sys.exit("❌ Phiên đã hết hạn hoặc chưa đăng nhập. Chạy lại: grab_session.py")

    courses = enumerate_courses(creds)
    if not isinstance(courses, dict):
        courses = {}
    print(f"✅ Phiên hợp lệ. Thấy {len(courses)} khoá còn bài chưa xong.\n")
    if not courses:
        print("🎉 Không còn bài nào chưa hoàn thành.")
        return

    # --- chế độ liệt kê ---
    if args.list or (not args.all and not args.lessons):
        for cid, c in courses.items():
            title = (c.get("course_title") or {}).get("vi") or cid
            print(f"### Khoá {cid}: {title}")
            for chid, ch in c.get("chapters", {}).items():
                print(f"  [{ch.get('chapter_name')}]")
                for les in ch.get("lessons", []):
                    print(f"     {les['lesson_id']:<7} {les.get('lesson_title')}")
            print()
        if not args.list:
            print("→ Dùng --all để hoàn thành hết, hoặc --course <id> --lessons <ids>.")
        return

    # --- xác định danh sách (course_id, lesson_id) cần làm ---
    targets = []
    if args.lessons:
        cid = args.course or (next(iter(courses)) if courses else None)
        if not cid:
            sys.exit("❌ Cần --course khi dùng --lessons.")
        for lid in args.lessons.split(","):
            lid = lid.strip()
            if lid:
                targets.append((cid, lid))
    elif args.all:
        for cid, c in courses.items():
            if args.course and str(cid) != str(args.course):
                continue
            for ch in c.get("chapters", {}).values():
                for les in ch.get("lessons", []):
                    targets.append((cid, str(les["lesson_id"])))

    print(f"Sẽ xử lý {len(targets)} bài.\n")
    newly, already, failed = 0, 0, 0
    for cid, lid in targets:
        if args.dry_run:
            print(f"[dry-run] complete course={cid} lesson={lid}")
            continue
        st, j, raw = complete(creds, cid, lid)
        if j and j.get("success"):
            if j.get("new_lesson_complete"):
                newly += 1
                print(f"🟢 lesson {lid}: VỪA HOÀN THÀNH (new_lesson_complete=true)")
            else:
                already += 1
                print(f"⚪ lesson {lid}: đã xong từ trước (new_lesson_complete=false)")
        else:
            failed += 1
            print(f"🔴 lesson {lid}: THẤT BẠI (HTTP {st}) {str(raw)[:120]}")
        time.sleep(args.delay)

    if not args.dry_run:
        print(f"\n=== KẾT QUẢ: {newly} vừa xong | {already} đã xong sẵn | {failed} lỗi ===")
        if newly == 0 and already > 0 and failed == 0:
            print("Lưu ý: tất cả đều 'đã xong sẵn' — nếu bạn chắc có bài chưa học mà vẫn ra false,")
            print("nghĩa là server có thể yêu cầu /update-progress đủ 100% trước. Báo tôi để làm bước 2.")


if __name__ == "__main__":
    main()
