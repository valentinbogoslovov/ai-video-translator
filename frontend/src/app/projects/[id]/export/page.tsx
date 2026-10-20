"use client";

import { use } from "react";
import Link from "next/link";
import { useProject } from "@/hooks/useProject";
import { api } from "@/lib/api";

export default function ExportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { project, refetch } = useProject(id);

  if (!project) return <p className="p-8">Loading...</p>;

  const handleExport = async () => {
    await api.triggerExport(id);
    refetch();
  };

  return (
    <main className="max-w-2xl mx-auto p-8">
      <Link href={`/projects/${id}`} className="text-sm text-blue-600 hover:underline">
        &larr; Back to project
      </Link>
      <h1 className="text-3xl font-bold mt-2 mb-6">Export: {project.name}</h1>

      <div className="border rounded-lg p-6 space-y-4">
        <p>
          Status: <strong>{project.status}</strong>
        </p>

        {project.status === "approved" && (
          <button
            onClick={handleExport}
            className="bg-purple-600 text-white px-6 py-2 rounded hover:bg-purple-700"
          >
            Generate Final Video
          </button>
        )}

        {project.status === "exported" && (
          <a
            href={api.getExportUrl(id)}
            className="inline-block bg-green-600 text-white px-6 py-2 rounded hover:bg-green-700"
            download
          >
            Download Dubbed Video
          </a>
        )}

        {project.status === "exporting" && (
          <p className="text-yellow-600">Export in progress...</p>
        )}
      </div>
    </main>
  );
}
