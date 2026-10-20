"use client";

interface Props {
  projectId: string;
}

export function Timeline({ projectId }: Props) {
  // TODO: Implement waveform timeline with segment markers
  return (
    <div className="border rounded-lg p-4 h-24 bg-gray-50 flex items-center justify-center">
      <p className="text-gray-400 text-sm">Waveform Timeline — coming soon</p>
    </div>
  );
}
