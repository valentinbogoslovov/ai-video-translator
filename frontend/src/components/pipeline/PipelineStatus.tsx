"use client";

import { STAGE_ORDER } from "@/lib/types";
import { StageCard } from "./StageCard";

interface StageStatus {
  stage: string;
  status: string;
}

interface Props {
  wsStatus: {
    run_id: string | null;
    run_status: string;
    stages: StageStatus[];
  } | null;
}

export function PipelineStatus({ wsStatus }: Props) {
  if (!wsStatus || !wsStatus.run_id) {
    return (
      <div className="border rounded-lg p-6 text-center text-gray-500">
        No pipeline runs yet. Start the pipeline to begin processing.
      </div>
    );
  }

  const stageMap = new Map(wsStatus.stages.map((s) => [s.stage, s.status]));

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <span>Run: {wsStatus.run_id.slice(0, 8)}...</span>
        <span
          className={`px-2 py-0.5 rounded text-xs ${
            wsStatus.run_status === "running"
              ? "bg-yellow-100 text-yellow-800"
              : wsStatus.run_status === "completed"
                ? "bg-green-100 text-green-800"
                : "bg-red-100 text-red-800"
          }`}
        >
          {wsStatus.run_status}
        </span>
      </div>

      <div className="grid grid-cols-7 gap-2">
        {STAGE_ORDER.map((stage) => (
          <StageCard key={stage} name={stage} status={stageMap.get(stage) || "unknown"} />
        ))}
      </div>
    </div>
  );
}
