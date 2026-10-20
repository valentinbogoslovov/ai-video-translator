export interface Project {
  id: string;
  name: string;
  status: string;
  source_video_path: string | null;
  script_text: string | null;
  source_lang: string;
  target_lang: string;
  created_at: string;
  updated_at: string;
}

export interface StageResult {
  id: string;
  stage: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  error_detail: string | null;
}

export interface PipelineRun {
  id: string;
  project_id: string;
  run_number: number;
  trigger: string;
  start_stage: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  stage_results: StageResult[];
}

export interface PipelineStatus {
  current_run: PipelineRun | null;
  runs: PipelineRun[];
}

export interface Segment {
  id: string;
  project_id: string;
  index: number;
  start_ms: number;
  end_ms: number;
  speaker_id: string | null;
  original_text: string | null;
  translated_text: string | null;
  dub_audio_path: string | null;
  locked: boolean;
}

export interface Speaker {
  id: string;
  project_id: string;
  label: string;
  voice_id: string | null;
  color: string;
}

export type ProjectStatus =
  | "draft"
  | "running"
  | "awaiting_review"
  | "approved"
  | "exporting"
  | "exported"
  | "error";

export const STAGE_ORDER = [
  "ingest",
  "separate",
  "transcribe",
  "diarize",
  "align",
  "dub",
  "mix",
] as const;

export type StageName = (typeof STAGE_ORDER)[number];
