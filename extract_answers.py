#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_answers.py — Moi ĐÁP ÁN ĐÚNG của các bài "Câu hỏi kiểm tra" (quiz)
trên online.dainam.edu.vn.

CƠ CHẾ: các quiz này là gói Articulate Storyline (SCORM) đặt tại
  /public/uploads/scorm/{hash}/html5/data/js/data.js   (TẢI CÔNG KHAI, không cần login)
Storyline chấm điểm HOÀN TOÀN Ở CLIENT, nên toàn bộ câu hỏi + đáp án đúng
nằm sẵn trong data.js:
  - choices: [{id, lmstext:"A"/"B"/...}]
  - answers: [{status:"correct"/"incorrect", evaluate.statements[].choiceid}]
  => đáp án đúng = answer có status="correct" -> choiceid -> lmstext.

DÙNG:
  # Bằng URL data.js trực tiếp:
  ./.venv/Scripts/python.exe extract_answers.py --url https://online.dainam.edu.vn/public/uploads/scorm/6a462ea290ea9/html5/data/js/data.js

  # Hoặc chỉ cần hash gói:
  ./.venv/Scripts/python.exe extract_answers.py --hash 6a462ea290ea9
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "https://online.dainam.edu.vn"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_storyline(text: str) -> dict:
    """Bóc JSON từ window.globalProvideData('data','<json>') hoặc object literal."""
    i = text.find("{")
    j = text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("Không thấy JSON trong data.js")
    blob = text[i:j + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # trường hợp là chuỗi bị escape -> thử unescape
        blob2 = blob.encode().decode("unicode_escape")
        k, m = blob2.find("{"), blob2.rfind("}")
        return json.loads(blob2[k:m + 1])


def walk(node, found):
    """Tìm mọi 'interaction' có cả choices + answers."""
    if isinstance(node, dict):
        if "choices" in node and "answers" in node:
            found.append(node)
        for v in node.values():
            walk(v, found)
    elif isinstance(node, list):
        for v in node:
            walk(v, found)


def solve(interaction: dict):
    choice_map = {}
    for c in interaction.get("choices", []):
        if isinstance(c, dict) and c.get("id"):
            choice_map[c["id"]] = c.get("lmstext") or c.get("text") or c["id"]

    correct = []
    for a in interaction.get("answers", []):
        if not isinstance(a, dict):
            continue
        if a.get("status") == "correct" or a.get("points", 0) >= 1:
            for st in (a.get("evaluate", {}) or {}).get("statements", []):
                cid = st.get("choiceid")
                if cid:
                    cid = cid.split(".")[-1]  # 'choices.choice_X' -> 'choice_X'
                    correct.append(choice_map.get(cid, cid))
    return choice_map, correct


def main():
    ap = argparse.ArgumentParser(description="Moi đáp án quiz Storyline (DNU)")
    ap.add_argument("--url", help="URL data.js đầy đủ")
    ap.add_argument("--hash", help="Hash gói scorm (vd 6a462ea290ea9)")
    args = ap.parse_args()

    if args.url:
        url = args.url
    elif args.hash:
        url = f"{BASE}/public/uploads/scorm/{args.hash}/html5/data/js/data.js"
    else:
        sys.exit("Cần --url hoặc --hash")

    print(f"Tải: {url}")
    data = parse_storyline(fetch(url))
    interactions = []
    walk(data, interactions)
    print(f"Tìm thấy {len(interactions)} câu hỏi.\n")

    for n, it in enumerate(interactions, 1):
        choice_map, correct = solve(it)
        opts = ", ".join(f"{v}" for v in choice_map.values())
        print(f"Câu {n}:")
        print(f"   Lựa chọn : {opts}")
        print(f"   ĐÁP ÁN   : {', '.join(correct) if correct else '(không xác định)'}")
    print("\nGhi chú: lmstext là nhãn Storyline gán cho mỗi lựa chọn (A/B/C/D...).")
    print("Thứ tự hiển thị trên màn hình có thể xáo trộn — đối chiếu theo nội dung nếu cần.")


if __name__ == "__main__":
    main()
