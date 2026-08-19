import { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "../api";

/*
 * "Làm bài gián tiếp" — phiên bản IN-APP (chạy được trên điện thoại).
 *
 * Không còn bookmarklet. Luồng:
 *   1. User bấm "Trích đề" ở bảng tiến độ  → props.exam = {course_id, lesson_id}.
 *   2. Component gọi /api/exam/start  → backend mở đề (đốt 1 lượt) & trả câu hỏi.
 *   3. User tick đáp án trên UI này.
 *   4. Bấm "Nộp bài" → /api/exam/submit  → backend POST /quizSubmit.
 *
 * Không tự nộp: user phải chủ động bấm "Nộp bài".
 */
export default function ExamExtractor({ session, exam, onClear }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState(null); // {questions, clipboard, ...}
  const [answers, setAnswers] = useState({}); // {qid: Set(value)}
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [score, setScore] = useState(null);   // {pending, score, percent, rating, date}
  const [copied, setCopied] = useState(false);
  // Trạng thái đồng bộ từng câu khi user chọn (sync-on-answer):
  //   idle | saving | saved | error
  const [syncState, setSyncState] = useState({}); // {qid: status}
  const debounceRef = useRef({});   // {qid: timeoutId} - gộp các cú tick liên tiếp
  const answersRef = useRef({});    // bản mới nhất của answers cho closure debounce

  // Khi user chọn 1 bài thi mới ở bảng tiến độ → reset & hỏi xác nhận đốt lượt.
  useEffect(() => {
    if (exam) {
      setData(null);
      setAnswers({});
      setResult(null);
      setScore(null);
      setError("");
      setSyncState({});
      answersRef.current = {};
      setConfirming(true);
    }
  }, [exam]);

  // Giữ answersRef luôn đồng bộ để hàm debounce đọc được giá trị mới nhất.
  useEffect(() => { answersRef.current = answers; }, [answers]);

  // Dọn timer debounce khi unmount để tránh memory leak / setState sau unmount.
  useEffect(() => () => {
    Object.values(debounceRef.current).forEach(clearTimeout);
  }, []);

  // Sau khi nộp thành công → tự poll điểm mỗi 10s (server chấm ở nền).
  // Defensive: dừng khi có điểm hoặc sau tối đa POLL_MAX lần để không spam server.
  useEffect(() => {
    if (!result?.ok) return undefined;
    const POLL_INTERVAL_MS = 10_000;
    const POLL_MAX = 30;              // ~5 phút là quá đủ cho việc chấm nền.
    let attempts = 0;
    let cancelled = false;
    setScore({ pending: true });

    async function poll() {
      if (cancelled) return;
      attempts += 1;
      try {
        const res = await api.examResult(session.session_id);
        if (cancelled) return;
        if (!res.pending) {
          setScore(res);            // đã có điểm → dừng poll.
          return;
        }
        setScore({ pending: true, attempts });
      } catch (e) {
        if (cancelled) return;
        setScore({ pending: true, attempts, warn: e.message });
      }
      if (attempts < POLL_MAX) {
        timer = setTimeout(poll, POLL_INTERVAL_MS);
      }
    }
    let timer = setTimeout(poll, POLL_INTERVAL_MS);
    poll();  // gọi ngay lần đầu, không đợi 10s.
    return () => { cancelled = true; clearTimeout(timer); };
  }, [result, session.session_id]);

  async function startExam() {
    setConfirming(false);
    setLoading(true);
    setError("");
    try {
      const res = await api.examStart(session.session_id, exam.course_id, exam.lesson_id);
      setData(res);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  // Gửi 1 câu lên server (sync-on-answer) + retry 1 lần nếu lỗi mạng.
  // Defensive programming (rule MMO): mạng có thể rớt -> thử lại với backoff nhẹ.
  async function syncQuestion(qid) {
    const set = answersRef.current[qid];
    const values = set ? Array.from(set) : [];
    if (values.length === 0) {
      // Bỏ chọn hết -> không gửi (server giữ câu trống), reset trạng thái.
      setSyncState((s) => ({ ...s, [qid]: "idle" }));
      return;
    }
    setSyncState((s) => ({ ...s, [qid]: "saving" }));
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const res = await api.examAnswer(session.session_id, qid, values);
        setSyncState((s) => ({ ...s, [qid]: res.ok ? "saved" : "error" }));
        return;
      } catch (e) {
        if (attempt === 0) {
          await new Promise((r) => setTimeout(r, 600)); // backoff trước khi retry.
          continue;
        }
        setSyncState((s) => ({ ...s, [qid]: "error" }));
      }
    }
  }

  // Debounce 400ms: gộp các cú tick liên tiếp của cùng 1 câu -> 1 request.
  function scheduleSync(qid) {
    clearTimeout(debounceRef.current[qid]);
    debounceRef.current[qid] = setTimeout(() => syncQuestion(qid), 400);
  }

  function toggle(qid, value, isMulti) {
    setAnswers((prev) => {
      const cur = new Set(prev[qid] || []);
      if (isMulti) {
        cur.has(value) ? cur.delete(value) : cur.add(value);
      } else {
        cur.clear();
        cur.add(value);
      }
      const next = { ...prev, [qid]: cur };
      answersRef.current = next;   // cập nhật ngay cho closure debounce.
      return next;
    });
    scheduleSync(qid);
  }

  async function submit() {
    setSubmitting(true);
    setError("");
    try {
      const payload = {};
      for (const [qid, set] of Object.entries(answers)) {
        payload[qid] = Array.from(set);
      }
      // Danh sách câu đã sync thành công -> backend chỉ gửi bù câu còn thiếu.
      const synced = Object.entries(syncState)
        .filter(([, st]) => st === "saved")
        .map(([qid]) => qid);
      const res = await api.examSubmit(session.session_id, payload, synced);
      setResult(res);
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  function copyClipboard() {
    if (!data?.clipboard) return;
    navigator.clipboard.writeText(data.clipboard).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    });
  }

  const answeredCount = useMemo(
    () => Object.values(answers).filter((s) => s && s.size > 0).length,
    [answers]
  );

  // ─────────────────────────── Empty state ───────────────────────────
  if (!exam && !data) {
    return (
      <div className="space-y-3">
        <h3 className="font-semibold text-zinc-100">Làm bài gián tiếp</h3>
        <p className="text-xs text-zinc-500">
          Bấm <span className="text-acid-400">“Trích đề”</span> ở một bài thi thật trong
          bảng bên trái. Đề sẽ hiện ngay tại đây để bạn tự chọn đáp án rồi nộp — không cần
          cài đặt gì, dùng được cả trên điện thoại.
        </p>
        <div className="rounded-xl border border-amber-400/25 bg-amber-400/10 px-4 py-3 text-xs text-amber-300">
          ⚠ Mở đề = dùng 1 lượt thi thật. Chỉ trích khi bạn đã sẵn sàng làm.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-zinc-100">Làm bài gián tiếp</h3>
        {(data || error) && (
          <button
            onClick={() => { setData(null); setError(""); setResult(null); onClear?.(); }}
            className="rounded-lg border border-white/10 px-2.5 py-1 text-xs text-zinc-400 hover:text-zinc-200"
          >
            Đóng
          </button>
        )}
      </div>

      {/* Modal xác nhận đốt lượt */}
      <AnimatePresence>
        {confirming && (
          <motion.div
            initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="rounded-xl border border-signal-red/30 bg-signal-red/10 p-4"
          >
            <div className="mb-1 font-semibold text-signal-red">Xác nhận trích đề</div>
            <p className="mb-3 text-xs text-zinc-300">
              Mở đề bài <span className="font-mono">#{exam?.lesson_id}</span> sẽ{" "}
              <b>đốt 1 lượt thi thật</b> ở server. Chắc chắn tiếp tục?
            </p>
            <div className="flex gap-2">
              <button onClick={startExam} className="btn-primary px-4 py-2 text-sm">
                Trích đề (dùng 1 lượt)
              </button>
              <button
                onClick={() => { setConfirming(false); onClear?.(); }}
                className="rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-400"
              >
                Huỷ
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {loading && (
        <div className="py-10 text-center text-sm text-zinc-500">Đang mở đề…</div>
      )}

      {error && (
        <div className="rounded-xl border border-signal-red/30 bg-signal-red/10 px-4 py-3 text-xs text-signal-red">
          {error}
        </div>
      )}

      {/* Kết quả nộp — tiến trình chấm từng câu */}
      {result && (
        <motion.div
          initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
          className={`flex items-center gap-3 rounded-xl border px-4 py-3 text-sm ${
            result.ok
              ? "border-acid-500/30 bg-acid-500/10 text-acid-300"
              : "border-signal-red/30 bg-signal-red/10 text-signal-red"
          }`}
        >
          <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-base ${
            result.ok ? "bg-acid-500/20" : "bg-signal-red/20"
          }`}>
            {result.ok ? "\u2713" : "!"}
          </span>
          <div className="leading-tight">
            {result.ok ? (
              <>
                <div className="font-semibold">Đã nộp bài thành công</div>
                <div className="text-xs text-acid-300/70">
                  Đã chấm {result.single_ok ?? result.answered}/
                  {result.single_total ?? result.total} câu lên hệ thống.
                </div>
              </>
            ) : (
              <div>Nộp chưa thành công. Kiểm tra lại trên trang trường.</div>
            )}
          </div>
        </motion.div>
      )}

      {/* Diem thi (auto-poll 10s) */}
      {result?.ok && score && (
        <AnimatePresence mode="wait">
          {score.pending ? (
            <motion.div
              key="pending"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="rounded-2xl border border-amber-400/20 bg-amber-400/[0.06] px-4 py-5"
            >
              <div className="mb-3 text-[11px] font-medium uppercase tracking-widest text-amber-400/70">
                Điểm thi của bạn
              </div>
              <div className="flex items-center gap-3 text-sm text-amber-300">
                <span className="relative flex h-3 w-3">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
                  <span className="relative inline-flex h-3 w-3 rounded-full bg-amber-400" />
                </span>
                <span>Hệ thống đang chấm ở nền, tự cập nhật mỗi 10s…</span>
                {score.attempts ? (
                  <span className="ml-auto font-mono text-xs text-amber-400/50">#{score.attempts}</span>
                ) : null}
              </div>
            </motion.div>
          ) : (
            <motion.div
              key="done"
              initial={{ opacity: 0, y: 10, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ type: "spring", stiffness: 260, damping: 22 }}
              className="overflow-hidden rounded-2xl border border-acid-500/25 bg-gradient-to-br from-acid-500/[0.12] via-white/[0.03] to-transparent"
            >
              <div className="flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className="mb-1 text-[11px] font-medium uppercase tracking-widest text-acid-400/70">
                    Điểm thi của bạn
                  </div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-5xl font-bold leading-none text-acid-300 sm:text-6xl">
                      {score.score || "\u2014"}
                    </span>
                    <span className="text-xl text-zinc-500">/ {score.max}</span>
                  </div>
                  {score.duration ? (
                    <div className="mt-2 text-xs text-zinc-500">
                      ⏱ Làm trong {score.duration} phút
                    </div>
                  ) : null}
                </div>

                {score.percent ? (
                  <ScoreRing percent={parseInt(score.percent, 10) || 0} />
                ) : null}
              </div>

              {(score.correct != null || score.wrong != null || score.total != null) && (
                <div className="grid grid-cols-3 gap-px border-t border-white/5 bg-white/5">
                  <Stat label="Câu đúng" value={score.correct} tone="acid" />
                  <Stat label="Câu sai" value={score.wrong} tone="red" />
                  <Stat label="Tổng câu" value={score.total} tone="zinc" />
                </div>
              )}

              {(score.start || score.end) && (
                <div className="flex flex-col gap-1 border-t border-white/5 px-5 py-3 text-[11px] text-zinc-500 sm:flex-row sm:justify-between">
                  {score.start && <span>Bắt đầu: <span className="font-mono text-zinc-400">{score.start}</span></span>}
                  {score.end && <span>Kết thúc: <span className="font-mono text-zinc-400">{score.end}</span></span>}
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      )}

      {/* Danh sách câu hỏi */}
      {data && !result && (
        <>
          <div className="flex items-center justify-between text-xs text-zinc-500">
            <span>{data.count} câu · đã chọn {answeredCount}/{data.count}</span>
            <button
              onClick={copyClipboard}
              className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-zinc-300 hover:bg-white/10"
            >
              {copied ? "✓ Đã copy đề" : "Copy đề (hỏi AI khác)"}
            </button>
          </div>

          <div className="max-h-[52vh] space-y-3 overflow-auto pr-1">
            {data.questions.map((q, qi) => (
              <div key={q.qid} className="rounded-xl border border-white/5 bg-white/[0.02] p-3">
                <div className="mb-2 flex items-start justify-between gap-2">
                  <div className="text-sm font-medium text-zinc-100">
                    <span className="text-acid-400">Câu {qi + 1}.</span> {q.text}
                  </div>
                  <SyncBadge status={syncState[q.qid]} onRetry={() => syncQuestion(q.qid)} />
                </div>
                {q.note && <div className="mb-2 text-[11px] text-zinc-500">{q.note}</div>}
                <div className="space-y-1">
                  {q.options.map((o) => {
                    const checked = answers[q.qid]?.has(o.value) || false;
                    return (
                      <label
                        key={o.value}
                        className={`flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2 text-sm transition ${
                          checked ? "bg-acid-500/10 text-acid-200" : "text-zinc-300 hover:bg-white/[0.03]"
                        }`}
                      >
                        <input
                          type={q.is_multi ? "checkbox" : "radio"}
                          name={`q-${q.qid}`}
                          checked={checked}
                          onChange={() => toggle(q.qid, o.value, q.is_multi)}
                          className="mt-0.5 accent-acid-500"
                        />
                        <span>{o.text}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          <button
            onClick={submit}
            disabled={submitting || answeredCount === 0}
            className="btn-primary w-full py-3 disabled:opacity-40"
          >
            {submitting ? "Đang nộp…" : `Nộp bài (${answeredCount}/${data.count} câu)`}
          </button>
          <p className="text-center text-[11px] text-zinc-600">
            Kiểm tra kỹ trước khi nộp — thao tác này gửi bài thi thật lên trường.
          </p>
        </>
      )}
    </div>
  );
}

/* ─────────────── Helper components (tách UI thuần → dễ tái sử dụng) ─────────────── */

/** Chỉ báo trạng thái lưu từng câu (sync-on-answer). */
function SyncBadge({ status, onRetry }) {
  if (!status || status === "idle") return null;
  if (status === "saving") {
    return (
      <span className="flex shrink-0 items-center gap-1 whitespace-nowrap text-[11px] text-zinc-500">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-400" />
        Đang lưu…
      </span>
    );
  }
  if (status === "saved") {
    return (
      <span className="flex shrink-0 items-center gap-1 whitespace-nowrap text-[11px] text-acid-400">
        ✓ Đã lưu
      </span>
    );
  }
  // error → cho bấm thử lại.
  return (
    <button
      onClick={onRetry}
      className="flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md border border-signal-red/30 bg-signal-red/10 px-1.5 py-0.5 text-[11px] text-signal-red hover:bg-signal-red/20"
    >
      ⚠ Lỗi · thử lại
    </button>
  );
}

/** Vòng tròn phần trăm (SVG, tự co giãn theo container). */
function ScoreRing({ percent }) {
  const R = 34;
  const C = 2 * Math.PI * R;
  const clamped = Math.max(0, Math.min(100, percent));
  const offset = C - (clamped / 100) * C;
  return (
    <div className="relative h-24 w-24 shrink-0 self-center sm:self-auto">
      <svg viewBox="0 0 80 80" className="h-full w-full -rotate-90">
        <circle cx="40" cy="40" r={R} fill="none" strokeWidth="7"
                className="stroke-white/10" />
        <motion.circle
          cx="40" cy="40" r={R} fill="none" strokeWidth="7" strokeLinecap="round"
          className="stroke-acid-400"
          strokeDasharray={C}
          initial={{ strokeDashoffset: C }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 1, ease: "easeOut" }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center">
        <span className="text-lg font-bold text-acid-300">{clamped}%</span>
      </div>
    </div>
  );
}

/** Ô thống kê (đúng / sai / tổng). */
function Stat({ label, value, tone }) {
  const toneClass = {
    acid: "text-acid-300",
    red: "text-signal-red",
    zinc: "text-zinc-200",
  }[tone] || "text-zinc-200";
  return (
    <div className="bg-ink-950/40 px-3 py-3 text-center">
      <div className={`text-xl font-bold ${toneClass}`}>
        {value != null ? value : "—"}
      </div>
      <div className="mt-0.5 text-[10px] uppercase tracking-wide text-zinc-500">
        {label}
      </div>
    </div>
  );
}
