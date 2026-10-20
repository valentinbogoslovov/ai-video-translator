"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Speaker } from "@/lib/types";

interface Props {
  projectId: string;
}

export function SpeakerPanel({ projectId }: Props) {
  const [speakers, setSpeakers] = useState<Speaker[]>([]);

  useEffect(() => {
    api.listSpeakers(projectId).then(setSpeakers).catch(() => {});
  }, [projectId]);

  const updateLabel = async (spk: Speaker, newLabel: string) => {
    const updated = await api.updateSpeaker(projectId, spk.id, { label: newLabel });
    setSpeakers((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
  };

  if (speakers.length === 0) {
    return (
      <div className="border rounded-lg p-4">
        <h3 className="font-semibold mb-2">Speakers</h3>
        <p className="text-sm text-gray-500">No speakers detected yet.</p>
      </div>
    );
  }

  return (
    <div className="border rounded-lg p-4">
      <h3 className="font-semibold mb-3">Speakers</h3>
      <div className="space-y-2">
        {speakers.map((spk) => (
          <div key={spk.id} className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: spk.color }} />
            <input
              defaultValue={spk.label}
              onBlur={(e) => updateLabel(spk, e.target.value)}
              className="flex-1 text-sm border rounded px-2 py-1"
            />
            <span className="text-xs text-gray-400">{spk.voice_id || "default"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
