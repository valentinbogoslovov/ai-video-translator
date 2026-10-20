"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useProject } from "@/hooks/useProject";
import { useSegments } from "@/hooks/useSegments";
import { TranscriptEditor } from "@/components/review/TranscriptEditor";
import { SpeakerPanel } from "@/components/review/SpeakerPanel";
import { api } from "@/lib/api";
import { STAGE_ORDER } from "@/lib/types";

export default function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { project, refetch: refetchProject } = useProject(id);
  const { segments, refetch: refetchSegments } = useSegments(id);
  const [restartStage, setRestartStage] = useState("dub");

  const handleApprove = async () => {
    await api.approveReview(id);
    refetchProject();
  };

  const handleReject = async () => {
    await api.rejectReview(id, restartStage);
    refetchProject();
  };

  if (!project) return <p className="p-8">Loading...</p>;

  return (
    <main className="max-w-6xl mx-auto p-8">
      <Link href={`/projects/${id}`} className="text-sm text-blue-600 hover:underline">
        &larr; Back to project
      </Link>
      <h1 className="text-3xl font-bold mt-2 mb-6">Review: {project.name}</h1>

      <div className="grid grid-cols-3 gap-8">
        <div className="col-span-2 space-y-4">
          <h2 className="text-xl font-semibold">Transcript & Translation</h2>
          <TranscriptEditor projectId={id} segments={segments} onUpdate={refetchSegments} />
        </div>

        <div className="space-y-6">
          <SpeakerPanel projectId={id} />

          <div className="border rounded-lg p-4 space-y-3">
            <h3 className="font-semibold">Review Actions</h3>
            <button
              onClick={handleApprove}
              className="w-full bg-green-600 text-white py-2 rounded hover:bg-green-700"
            >
              Approve
            </button>

            <div>
              <label className="block text-sm mb-1">Restart from stage:</label>
              <select
                value={restartStage}
                onChange={(e) => setRestartStage(e.target.value)}
                className="w-full border rounded px-2 py-1 mb-2"
              >
                {STAGE_ORDER.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <button
                onClick={handleReject}
                className="w-full bg-red-600 text-white py-2 rounded hover:bg-red-700"
              >
                Reject & Restart
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
