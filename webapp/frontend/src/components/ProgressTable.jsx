import { motion } from "framer-motion";

const KIND_STYLE = {
  exam: "bg-signal-red/10 text-signal-red border-signal-red/20",
  quiz: "bg-signal-amber/10 text-signal-amber border-signal-amber/20",
  video: "bg-acid-500/10 text-acid-400 border-acid-500/20",
  unknown: "bg-white/5 text-zinc-500 border-white/10",
};

const RESULT_STYLE = {
  completed: "text-signal-400",
  already: "text-zinc-500",
  skipped: "text-signal-amber",
  error: "text-signal-red",
};

const RESULT_LABEL = {
  completed: "✓ Vừa xong",
  already: "• Đã xong",
  skipped: "⚠ Bỏ qua (thi)",
  error: "✕ Lỗi",
};

export default function ProgressTable({ rows, onPickExam }) {
  if (!rows.length) {
    return (
      <div className="flex h-full items-center justify-center py-16 text-sm text-zinc-600">
        Chưa có dữ liệu. Nhấn “Bắt đầu” để chạy.
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-xl border border-white/5">
      <table className="w-full text-left text-sm">
        <thead className="bg-ink-850 font-mono text-[11px] uppercase tracking-wider text-zinc-500">
          <tr>
            <th className="px-4 py-3">Bài học</th>
            <th className="px-4 py-3 w-28">Loại</th>
            <th className="px-4 py-3 w-32">Kết quả</th>
            <th className="px-4 py-3 w-24"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {rows.map((r, i) => (
            <motion.tr
              key={`${r.lesson_id}-${i}`}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              className="hover:bg-white/[0.02]"
            >
              <td className="px-4 py-3">
                <div className="text-zinc-200">{r.title}</div>
                <div className="font-mono text-[11px] text-zinc-600">#{r.lesson_id}</div>
              </td>
              <td className="px-4 py-3">
                <span className={`pill border ${KIND_STYLE[r.kind] || KIND_STYLE.unknown}`}>
                  {r.kind_label}
                </span>
              </td>
              <td className={`px-4 py-3 font-mono text-xs ${RESULT_STYLE[r.result] || ""}`}>
                {RESULT_LABEL[r.result] || r.result}
              </td>
              <td className="px-4 py-3">
                {r.is_exam && (
                  <button
                    onClick={() => onPickExam?.(r)}
                    className="rounded-lg border border-white/10 px-2.5 py-1 font-mono text-[11px] text-zinc-400 hover:border-acid-500/40 hover:text-acid-400"
                  >
                    Trích đề
                  </button>
                )}
                {r.answers && (
                  <span className="font-mono text-[11px] text-signal-amber">có đáp án</span>
                )}
              </td>
            </motion.tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
