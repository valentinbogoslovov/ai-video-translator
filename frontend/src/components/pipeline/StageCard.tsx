"use client";

const statusColors: Record<string, string> = {
  pending: "bg-gray-100 border-gray-300",
  running: "bg-yellow-50 border-yellow-400 animate-pulse",
  completed: "bg-green-50 border-green-400",
  failed: "bg-red-50 border-red-400",
  skipped: "bg-gray-50 border-gray-200 opacity-50",
};

const statusIcons: Record<string, string> = {
  pending: "---",
  running: "...",
  completed: "OK",
  failed: "ERR",
  skipped: "SKIP",
};

interface Props {
  name: string;
  status: string;
}

export function StageCard({ name, status }: Props) {
  return (
    <div
      className={`border-2 rounded-lg p-3 text-center ${statusColors[status] || "bg-gray-100 border-gray-300"}`}
    >
      <p className="text-xs font-mono uppercase tracking-wider">{name}</p>
      <p className="text-sm font-semibold mt-1">{statusIcons[status] || status}</p>
    </div>
  );
}
