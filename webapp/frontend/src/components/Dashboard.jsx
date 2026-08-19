import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { api } from "../api";
import { useSSE } from "../useSSE";
import ProgressTable from "./ProgressTable";
import ExamExtractor from "./ExamExtractor";

function Stat({ label, value, accent }) {
  return (
    <div className="glass p-4">
      <div className="font-mono text-[11px] uppercase tracking-wider text-zinc-500">{label}</div>
      <div className={`mt-1 font-mono text-3xl font-bold ${accent || "text-zinc-100"}`}>{value}</div>
    </div>
  );
}

export default function Dashboard({ session, onLogout }) {
  const [jobId, setJobId] = useState(null);
  const [starting, setStarting] = useState(false);
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [pickedExam, setPickedExam] = useState(null);
  const [notice, setNotice] = useState("");
  const examRef = useRef(null);
  const { events, snapshot, done } = useSSE(jobId);

  // Mobile: khi user bấm "Trích đề" → tự cuộn xuống khối "Làm bài gián tiếp".
  // Chỉ cuộn ở breakpoint < lg (1024px) vì desktop 2 cột đã nhìn thấy sẵn.
  useEffect(() => {
    if (!pickedExam) return;
    if (window.matchMedia("(min-width: 1024px)").matches) return;
    // đợi React render xong khối rồi mới cuộn.
    const id = requestAnimationFrame(() => {
      examRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return () => cancelAnimationFrame(id);
  }, [pickedExam]);

  // Nạp lại checkpoint nếu job đã tồn tại (reload trang).
  useEffect(() => {
    if (snapshot) {
      setRows(snapshot.results || []);
      setTotal(snapshot.total || 0);
    }
  }, [snapshot]);

  // Xử lý sự kiện realtime.
  useEffect(() => {
    if (!events.length) return;
    const last = events[events.length - 1];
    if (last.type === "total") setTotal(last.total);
    else if (last.type === "lesson") setRows((prev) => [...prev, last.payload]);
    else if (last.type === "done") {
      if (last.results) setRows(last.results);
      // Acc đã làm hết / job xong → báo rõ cho user (trước đây im lặng).
      if (last.message) setNotice(last.message);
      else if ((last.summary?.total ?? 0) === 0) {
        setNotice("Tài khoản này đã hoàn thành tất cả bài — không còn gì để chạy.");
      } else {
        const s = last.summary || {};
        setNotice(`Hoàn tất! ${s.completed ?? 0} bài vừa xong, ${s.already ?? 0} đã có sẵn.`);
      }
    }
  }, [events]);

  async function start() {
    setStarting(true);
    setRows([]);
    setNotice("");
    try {
      const r = await api.run(session.session_id);
      setJobId(null);
      setTimeout(() => setJobId(r.job_id), 0); // remount EventSource sạch
    } catch (e) {
      setNotice(e.message || "Không khởi động được. Thử lại.");
    } finally {
      setStarting(false);
    }
  }

  const stats = useMemo(() => {
    const completed = rows.filter((r) => r.result === "completed").length;
    const already = rows.filter((r) => r.result === "already").length;
    const exams = rows.filter((r) => r.is_exam).length;
    const quizzes = rows.filter((r) => r.answers).length;
    return { completed, already, exams, quizzes };
  }, [rows]);

  const progress = total ? Math.round((rows.length / total) * 100) : 0;
  const running = jobId && !done;

  return (
    <div className="min-h-screen bg-ink-950">
      <div className="absolute inset-x-0 top-0 h-64 bg-grid bg-[size:32px_32px] opacity-40" />
      <div className="relative mx-auto max-w-6xl px-5 py-8">
        {/* Header */}
        <header className="mb-8 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-acid-500 font-mono font-bold text-ink-950">
              DN
            </div>
            <div>
              <div className="font-mono text-sm font-semibold text-zinc-100">DNU AUTOPILOT</div>
              <div className="text-xs text-zinc-500">{session.email}</div>
            </div>
          </div>
          <button
            onClick={onLogout}
            className="rounded-xl border border-white/10 px-4 py-2 text-sm text-zinc-400 hover:border-signal-red/40 hover:text-signal-red"
          >
            Đăng xuất
          </button>
        </header>

        {/* Control bar */}
        <div className="glass mb-6 flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-zinc-100">Tự động hoàn thành bài giảng</h2>
            <p className="text-sm text-zinc-500">
              Bỏ qua video, đánh dấu hoàn thành mọi bài (trừ bài thi thật).
            </p>
          </div>
          <button className="btn-primary" onClick={start} disabled={starting || running}>
            {running ? "Đang chạy…" : starting ? "Khởi động…" : "Bắt đầu ▶"}
          </button>
        </div>

        {/* Thông báo (acc đã làm hết / job xong / lỗi) */}
        {notice && (
          <div className="mb-6 rounded-xl border border-acid-500/30 bg-acid-500/10 px-4 py-3 text-sm text-acid-300">
            {notice}
          </div>
        )}

        {/* Progress bar */}
        {(running || rows.length > 0) && (
          <div className="mb-6">
            <div className="mb-2 flex justify-between font-mono text-xs text-zinc-500">
              <span>{rows.length}/{total || "?"} bài</span>
              <span>{progress}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-ink-800">
              <motion.div
                className="h-full rounded-full bg-acid-500"
                animate={{ width: `${progress}%` }}
                transition={{ ease: "easeOut" }}
              />
            </div>
          </div>
        )}

        {/* Bento grid */}
        <div className="grid gap-4 lg:grid-cols-4">
          <Stat label="Vừa hoàn thành" value={stats.completed} accent="text-signal-400" />
          <Stat label="Đã xong sẵn" value={stats.already} accent="text-zinc-300" />
          <Stat label="Quiz có đáp án" value={stats.quizzes} accent="text-signal-amber" />
          <Stat label="Bài thi (tự làm)" value={stats.exams} accent="text-signal-red" />
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-[1.5fr_1fr]">
          <div className="glass p-5">
            <h3 className="mb-4 font-semibold text-zinc-100">Tiến độ chi tiết</h3>
            <ProgressTable
              rows={rows}
              onPickExam={(r) =>
                setPickedExam({ course_id: String(r.course_id), lesson_id: String(r.lesson_id), _t: Date.now() })
              }
            />
          </div>
          <div ref={examRef} className="glass p-5 scroll-mt-4">
            <ExamExtractor
              session={session}
              exam={pickedExam}
              onClear={() => setPickedExam(null)}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
