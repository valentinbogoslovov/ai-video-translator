"use client";

import { use } from "react";
import Link from "next/link";
import { useProject } from "@/hooks/useProject";
import { usePipelineStatus } from "@/hooks/usePipelineStatus";
import { PipelineStatus } from "@/components/pipeline/PipelineStatus";
import { api } from "@/lib/api";

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { project, loading, error, refetch } = useProject(id);
  const wsStatus = usePipelineStatus(id);

  if (loading) return <p className="p-8 text-gray-500">Loading...</p>;
  if (error || !project) return <p className="p-8 text-red-500">Error: {error || "Not found"}</p>;

  const handleStart = async () => {
    await api.startPipeline(id);
    refetch();
  };

  return (
    <main className="max-w-4xl mx-auto p-8">
      <div className="flex justify-between items-center mb-6">
        <div>
          <Link href="/" className="text-sm text-blue-600 hover:underline">
            &larr; Back
          </Link>
          <h1 className="text-3xl font-bold">{project.name}</h1>
          <p className="text-gray-500">
            {project.source_lang} → {project.target_lang} | Status: {project.status}
          </p>
        </div>
        <div className="flex gap-2">
          {project.status === "draft" && (
            <button
              onClick={handleStart}
              className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700"
            >
              Start Pipeline
            </button>
          )}
          {project.status === "awaiting_review" && (
            <Link
              href={`/projects/${id}/review`}
              className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"
            >
              Review
            </Link>
          )}
          {(project.status === "approved" || project.status === "exported") && (
            <Link
              href={`/projects/${id}/export`}
              className="bg-purple-600 text-white px-4 py-2 rounded hover:bg-purple-700"
            >
              Export
            </Link>
          )}
        </div>
      </div>

      <PipelineStatus wsStatus={wsStatus} />
    </main>
  );
}
