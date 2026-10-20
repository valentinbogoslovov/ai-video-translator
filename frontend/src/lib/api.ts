const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      ...(options?.headers || {}),
    },
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || res.statusText);
  }
  return res.json();
}

export const api = {
  // Projects
  listProjects: () => request<import("./types").Project[]>("/api/projects"),

  getProject: (id: string) => request<import("./types").Project>(`/api/projects/${id}`),

  createProject: (form: FormData) =>
    request<import("./types").Project>("/api/projects", { method: "POST", body: form }),

  deleteProject: (id: string) =>
    request<{ ok: boolean }>(`/api/projects/${id}`, { method: "DELETE" }),

  // Pipeline
  startPipeline: (projectId: string) =>
    request<import("./types").PipelineRun>(`/api/projects/${projectId}/pipeline/start`, { method: "POST" }),

  restartPipeline: (projectId: string, fromStage: string) =>
    request<import("./types").PipelineRun>(`/api/projects/${projectId}/pipeline/restart`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_stage: fromStage }),
    }),

  getPipelineStatus: (projectId: string) =>
    request<import("./types").PipelineStatus>(`/api/projects/${projectId}/pipeline/status`),

  // Segments
  listSegments: (projectId: string) =>
    request<import("./types").Segment[]>(`/api/projects/${projectId}/segments`),

  updateSegment: (projectId: string, segmentId: string, data: Partial<import("./types").Segment>) =>
    request<import("./types").Segment>(`/api/projects/${projectId}/segments/${segmentId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),

  regenerateSegment: (projectId: string, segmentId: string) =>
    request<import("./types").Segment>(`/api/projects/${projectId}/segments/${segmentId}/regenerate`, {
      method: "POST",
    }),

  // Speakers
  listSpeakers: (projectId: string) =>
    request<import("./types").Speaker[]>(`/api/projects/${projectId}/speakers`),

  updateSpeaker: (projectId: string, speakerId: string, data: Partial<import("./types").Speaker>) =>
    request<import("./types").Speaker>(`/api/projects/${projectId}/speakers/${speakerId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),

  // Review
  approveReview: (projectId: string) =>
    request<{ ok: boolean }>(`/api/projects/${projectId}/review/approve`, { method: "POST" }),

  rejectReview: (projectId: string, fromStage: string) =>
    request<import("./types").PipelineRun>(`/api/projects/${projectId}/review/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_stage: fromStage }),
    }),

  // Export
  triggerExport: (projectId: string) =>
    request<{ ok: boolean }>(`/api/projects/${projectId}/export`, { method: "POST" }),

  getExportUrl: (projectId: string) => `${API_URL}/api/projects/${projectId}/export/download`,
};
