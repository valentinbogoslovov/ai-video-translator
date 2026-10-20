"use client";

import { useEffect, useRef, useState } from "react";

interface StageStatus {
  stage: string;
  status: string;
}

interface PipelineWsMessage {
  run_id: string | null;
  run_status: string;
  stages: StageStatus[];
}

export function usePipelineStatus(projectId: string) {
  const [data, setData] = useState<PipelineWsMessage | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const wsUrl = `${(process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace("http", "ws")}/api/ws/projects/${projectId}/status`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        setData(JSON.parse(event.data));
      } catch {}
    };

    return () => {
      ws.close();
    };
  }, [projectId]);

  return data;
}
