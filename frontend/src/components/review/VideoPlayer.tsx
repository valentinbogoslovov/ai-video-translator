"use client";

interface Props {
  projectId: string;
}

export function VideoPlayer({ projectId }: Props) {
  // TODO: Implement video player with synced playback
  return (
    <div className="border rounded-lg p-4 bg-black aspect-video flex items-center justify-center">
      <p className="text-gray-400">Video Player — coming soon</p>
    </div>
  );
}
