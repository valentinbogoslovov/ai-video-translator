"use client";

interface Props {
  projectId: string;
}

export function MixControls({ projectId }: Props) {
  // TODO: Volume/balance sliders for dub vs background mix
  return (
    <div className="border rounded-lg p-4">
      <h3 className="font-semibold mb-2">Mix Controls</h3>
      <p className="text-sm text-gray-500">Volume and balance controls — coming soon</p>
    </div>
  );
}
