"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { Segment } from "@/lib/types";

interface Props {
  projectId: string;
  segments: Segment[];
  onUpdate: () => void;
}

export function TranscriptEditor({ projectId, segments, onUpdate }: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

  const startEdit = (seg: Segment) => {
    setEditingId(seg.id);
    setEditText(seg.translated_text || "");
  };

  const saveEdit = async (segId: string) => {
    await api.updateSegment(projectId, segId, { translated_text: editText });
    setEditingId(null);
    onUpdate();
  };

  if (segments.length === 0) {
    return <p className="text-gray-500">No segments yet. Run the pipeline first.</p>;
  }

  return (
    <div className="space-y-2">
      {segments.map((seg) => (
        <div key={seg.id} className="border rounded p-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>
              #{seg.index} | {(seg.start_ms / 1000).toFixed(1)}s – {(seg.end_ms / 1000).toFixed(1)}s
            </span>
            <span>{seg.speaker_id ? `Speaker: ${seg.speaker_id.slice(0, 6)}` : "No speaker"}</span>
          </div>
          <p className="text-sm text-gray-600 mb-1">{seg.original_text || "(no transcript)"}</p>

          {editingId === seg.id ? (
            <div className="flex gap-2">
              <input
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                className="flex-1 border rounded px-2 py-1 text-sm"
              />
              <button
                onClick={() => saveEdit(seg.id)}
                className="text-sm bg-blue-600 text-white px-3 py-1 rounded"
              >
                Save
              </button>
              <button
                onClick={() => setEditingId(null)}
                className="text-sm border px-3 py-1 rounded"
              >
                Cancel
              </button>
            </div>
          ) : (
            <div
              onClick={() => startEdit(seg)}
              className="text-sm cursor-pointer hover:bg-gray-50 p-1 rounded"
            >
              {seg.translated_text || <span className="text-gray-400 italic">Click to add translation</span>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
