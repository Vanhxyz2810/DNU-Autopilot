import { useEffect, useRef, useState } from "react";
import { api } from "./api";

// Hook SSE: kết nối stream tiến độ 1 job, cập nhật state realtime.
export function useSSE(jobId) {
  const [events, setEvents] = useState([]);
  const [snapshot, setSnapshot] = useState(null);
  const [done, setDone] = useState(false);
  const esRef = useRef(null);

  useEffect(() => {
    if (!jobId) return;
    setEvents([]);
    setDone(false);
    const es = new EventSource(api.streamUrl(jobId));
    esRef.current = es;

    es.onmessage = (e) => {
      let data;
      try { data = JSON.parse(e.data); } catch { return; }
      if (data.type === "snapshot") { setSnapshot(data.payload); return; }
      if (data.type === "end") { setDone(true); es.close(); return; }
      setEvents((prev) => [...prev, data]);
    };
    es.onerror = () => { es.close(); };

    return () => es.close();
  }, [jobId]);

  return { events, snapshot, done };
}
