"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import CaptionPreview from "./CaptionPreview";

type StepTiming = { name: string; seconds: number };

type QueueItem = {
  jobId: string;
  status: JobStatus;
  fileName: string;
  fileSize: number;
  videoUrl: string; // local blob URL for preview
  selectedPreviewLang: string | null;
  // Snapshot of the form settings used to submit this job, so a Retry
  // button can re-POST the exact same payload.
  source: {
    file: File;
    scriptFile: File | null;
    scriptText: string;
    captionScriptFile: File | null;
    captionScriptText: string;
    captionSource: "auto" | "custom";
    languages: string[];
    captionBox: { x: number; y: number; scale: number; rotation: number };
    captionStyle: CaptionStyle;
    bgEntries: { url: string; lang: string }[];
    voEntries: { file: File; lang: string }[];
  };
};

type CaptionStyle = {
  enabled: boolean;
  font_name: string;
  bold: boolean;
  primary_color: string;
  outline_color: string;
  outline_width: number;
  shadow_color: string;
  shadow: number;
  shadow_alpha: number;
  shadow_blur: number;
  letter_spacing: number; // em-units of font_size, -0.10 to 0.50
  line_spacing: number;   // multiplier of font_size, 0.8 to 2.0
  // Caption breaks (Captions-AI style packing). Width-wrap is implicit and
  // always on (driven by caption box width); we no longer expose a hard
  // word-count cap per line.
  max_lines_per_caption: number; // 1-3
  break_on_punct: boolean;
  break_on_pause_ms: number;     // 0-500, 0 disables
  // Animation: minimal fade+scale "pop" entrance per caption line.
  pop_animation: boolean;
  glow: boolean;
  glow_color: string;
  glow_strength: number;
};

const DEFAULT_CAPTION_STYLE: CaptionStyle = {
  enabled: true,
  font_name: "Rubik",
  bold: true,
  primary_color: "#FFFFFF",
  outline_color: "#000000",
  outline_width: 0.5,
  shadow_color: "#000000",
  shadow: 0.5,
  shadow_alpha: 1,
  shadow_blur: 5,
  letter_spacing: 0,
  line_spacing: 0.8,
  max_lines_per_caption: 2,
  break_on_punct: true,
  break_on_pause_ms: 80,
  pop_animation: true,
  glow: false,
  glow_color: "#FFD166",
  glow_strength: 0,
};

const FONT_CHOICES = ["Rubik", "Inter", "Montserrat", "Oswald", "Anton"];

// Fuzzy filename → language code (mirrors backend script_parser._header_lang).
// We accept ISO codes, common abbreviations, and English names.
const FILENAME_LANG_LOOKUP: Record<string, string> = {
  EN: "en", ENG: "en", ENGLISH: "en",
  DE: "de", GER: "de", GERMAN: "de", DEUTSCH: "de",
  FR: "fr", FRA: "fr", FRENCH: "fr", FRANCAIS: "fr", "FRANÇAIS": "fr",
  ES: "es", SPA: "es", SPANISH: "es", ESPANOL: "es", "ESPAÑOL": "es",
  IT: "it", ITA: "it", ITALIAN: "it", ITALIANO: "it",
  PT: "pt", POR: "pt", PORTUGUESE: "pt", PORTUGUES: "pt",
  NL: "nl", DUT: "nl", DUTCH: "nl", NEDERLANDS: "nl",
  PL: "pl", POL: "pl", POLISH: "pl", POLSKI: "pl",
  RO: "ro", ROM: "ro", ROMANIAN: "ro", ROMANA: "ro",
  BG: "bg", BUL: "bg", BULGARIAN: "bg",
  EL: "el", GR: "el", GRE: "el", GREEK: "el",
  HR: "hr", CRO: "hr", CROATIAN: "hr", HRVATSKI: "hr",
  HU: "hu", HUN: "hu", HUNGARIAN: "hu", MAGYAR: "hu",
  LV: "lv", LAT: "lv", LATVIAN: "lv",
  LT: "lt", LIT: "lt", LITHUANIAN: "lt",
  RS: "sr", SR: "sr", SRB: "sr", SERBIAN: "sr", SRPSKI: "sr",
  SI: "sl", SL: "sl", SLO: "sl", SLOVENIAN: "sl",
  CZ: "cs", CS: "cs", CZE: "cs", CZECH: "cs",
  SK: "sk", SVK: "sk", SLOVAK: "sk",
  DK: "da", DA: "da", DAN: "da", DANISH: "da", DANSK: "da",
  SE: "sv", SV: "sv", SWE: "sv", SWEDISH: "sv", SVENSKA: "sv",
};

/** Parse pasted text for language headers client-side (mirrors backend logic). */
function detectLangsFromText(text: string): string[] {
  const seen: string[] = [];
  const norm = (s: string) => s.replace(/[\*\[\]\(\):.\-_/\\]+/g, "").trim().toUpperCase();
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.length > 40) continue;
    const whole = norm(line);
    if (FILENAME_LANG_LOOKUP[whole]) {
      if (!seen.includes(FILENAME_LANG_LOOKUP[whole])) seen.push(FILENAME_LANG_LOOKUP[whole]);
      continue;
    }
    // Try first token (inline header like "EN: Hello...")
    const first = line.split(/[\s:\-—]+/)[0];
    const hit = FILENAME_LANG_LOOKUP[norm(first)];
    if (hit && !seen.includes(hit)) seen.push(hit);
  }
  return seen;
}

function langFromFilename(filename: string): string | null {
  if (!filename) return null;
  const stem = filename.replace(/\.[^.]+$/, "");
  const norm = (s: string) => s.replace(/[\*\[\]\(\):.\-_/\\]+/g, "").trim().toUpperCase();
  const whole = norm(stem);
  if (FILENAME_LANG_LOOKUP[whole]) return FILENAME_LANG_LOOKUP[whole];
  for (const piece of stem.split(/[\s()\[\]\-—:/,|]+/)) {
    const hit = FILENAME_LANG_LOOKUP[norm(piece)];
    if (hit) return hit;
  }
  return null;
}

type JobStatus = {
  job_id: string;
  status: string;
  current_step: string;
  current_language: string;
  languages_done: string[];
  languages_total: string[];
  progress: number;
  error: string | null;
  step_history: StepTiming[];
  current_step_started_at: number;
};

function fmtSeconds(s: number): string {
  if (!isFinite(s) || s < 0) return "0s";
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s - m * 60);
  return `${m}m ${rem}s`;
}

const LANGUAGES: Record<string, string> = {
  en: "English", es: "Spanish", fr: "French", de: "German",
  it: "Italian", pt: "Portuguese", nl: "Dutch", pl: "Polish",
  sv: "Swedish", da: "Danish",
  cs: "Czech", ro: "Romanian", bg: "Bulgarian", el: "Greek",
  hr: "Croatian", hu: "Hungarian", lv: "Latvian", lt: "Lithuanian",
  sr: "Serbian", sl: "Slovenian", sk: "Slovak",
};

// Default language batch (all supported languages)
const DEFAULT_LANGUAGES: string[] = Object.keys(LANGUAGES);

const API = "http://localhost:8000";

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [scriptFile, setScriptFile] = useState<File | null>(null);
  const [scriptText, setScriptText] = useState("");
  const [scriptTextModalOpen, setScriptTextModalOpen] = useState(false);
  const [captionScriptFile, setCaptionScriptFile] = useState<File | null>(null);
  const [captionScriptText, setCaptionScriptText] = useState("");
  // Where caption text comes from. 'auto' = derived server-side from the
  // translation output (default; matches the legacy behaviour). 'custom' =
  // explicit per-language text supplied here, sent to the backend as
  // caption_script_text / caption_script.
  const [captionSource, setCaptionSource] = useState<"auto" | "custom">("auto");
  // 'translate' (default) runs the full translate+dub+caption flow against
  // /api/translate. 'background-swap' keeps the original audio and only
  // replaces the visual background, hitting /api/background-swap. The mode
  // gates which form fields are visible AND which endpoint we POST to.
  const [mode, setMode] = useState<"translate" | "background-swap">("translate");
  const [bgEntries, setBgEntries] = useState<{ url: string; lang: string }[]>([]);
  const [voEntries, setVoEntries] = useState<{ file: File; lang: string }[]>([]);
  const [voModalOpen, setVoModalOpen] = useState(false);
  const [selectedLangs, setSelectedLangs] = useState<string[]>(DEFAULT_LANGUAGES);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [captionStyle, setCaptionStyle] = useState<CaptionStyle>(DEFAULT_CAPTION_STYLE);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [bgModalOpen, setBgModalOpen] = useState(false);
  const [captionsExpanded, setCaptionsExpanded] = useState(false);
  const [previewPlaying, setPreviewPlaying] = useState(true);
  const [previewMuted, setPreviewMuted] = useState(true);
  const previewVideoRef = useRef<HTMLVideoElement | null>(null);
  // Mirror of previewVideoRef.current as state so child components (the ASS
  // caption preview) can react when the video element mounts/unmounts —
  // refs alone don't trigger re-renders.
  const [previewVideoEl, setPreviewVideoEl] = useState<HTMLVideoElement | null>(null);
  const setPreviewVideoRef = useCallback((el: HTMLVideoElement | null) => {
    previewVideoRef.current = el;
    setPreviewVideoEl(el);
  }, []);
  const previewSlotRef = useRef<HTMLDivElement | null>(null);
  // Drag state for the caption box overlay (move + resize via corner handle).
  const dragRef = useRef<{
    mode: "move" | "resize";
    startX: number;
    startY: number;
    startBox: { x: number; y: number; scale: number; rotation: number };
    rect: DOMRect;
  } | null>(null);

  useEffect(() => {
    function onMove(e: MouseEvent) {
      const d = dragRef.current;
      if (!d) return;
      const dxPct = ((e.clientX - d.startX) / d.rect.width) * 100;
      const dyPct = ((e.clientY - d.startY) / d.rect.height) * 100;
      if (d.mode === "move") {
        setCaptionBox({
          ...d.startBox,
          x: Math.max(0, Math.min(100, d.startBox.x + dxPct)),
          y: Math.max(0, Math.min(100, d.startBox.y + dyPct)),
        });
      } else {
        // Resize: drive scale by horizontal delta from the center.
        setCaptionBox({
          ...d.startBox,
          scale: Math.max(5, Math.min(100, d.startBox.scale + dxPct * 2)),
        });
      }
    }
    function onUp() { dragRef.current = null; }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const startCaptionDrag = (e: React.MouseEvent, mode: "move" | "resize") => {
    if (!previewSlotRef.current) return;
    e.preventDefault();
    e.stopPropagation();
    dragRef.current = {
      mode,
      startX: e.clientX,
      startY: e.clientY,
      startBox: { ...captionBox },
      rect: previewSlotRef.current.getBoundingClientRect(),
    };
  };
  const [nowTs, setNowTs] = useState<number>(() => Date.now() / 1000);
  useEffect(() => {
    if (!queue.some((q) => q.status.status === "processing" || q.status.status === "queued")) return;
    const t = setInterval(() => setNowTs(Date.now() / 1000), 500);
    return () => clearInterval(t);
  }, [queue]);

  // Caption box overlay (percent units relative to video frame)
  const [captionBox, setCaptionBox] = useState({
    x: 50,        // center X (%)
    y: 71,        // center Y (%)
    scale: 20,    // width as % of video width
    rotation: 0,  // degrees
  });
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file) { setVideoUrl(null); return; }
    const url = URL.createObjectURL(file);
    setVideoUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  // Poll status for every queue item that's still processing.
  useEffect(() => {
    const active = queue.filter((q) => q.status.status === "processing" || q.status.status === "queued");
    if (active.length === 0) return;
    const interval = setInterval(async () => {
      const updates: Record<string, JobStatus> = {};
      await Promise.all(active.map(async (q) => {
        try {
          const res = await fetch(`${API}/api/status/${q.jobId}`);
          if (res.ok) updates[q.jobId] = await res.json();
        } catch { /* ignore */ }
      }));
      if (Object.keys(updates).length === 0) return;
      setQueue((cur) => cur.map((q) => (updates[q.jobId] ? { ...q, status: updates[q.jobId] } : q)));
    }, 750);
    return () => clearInterval(interval);
  }, [queue]);

  const toggleLang = (code: string) => {
    setSelectedLangs((prev) =>
      prev.includes(code) ? prev.filter((l) => l !== code) : [...prev, code]
    );
  };

  const selectAll = () => {
    setSelectedLangs(
      selectedLangs.length === DEFAULT_LANGUAGES.length ? [] : DEFAULT_LANGUAGES
    );
  };

  const allInGroupSelected = (group: string[]) =>
    group.every((c) => selectedLangs.includes(c));

  const toggleGroup = (group: string[]) => {
    setSelectedLangs((prev) =>
      allInGroupSelected(group)
        ? prev.filter((c) => !group.includes(c))
        : Array.from(new Set([...prev, ...group]))
    );
  };

  const renderLangButtons = (codes: string[]) => (
    <div className="flex flex-wrap gap-1.5">
      {codes.map((code) => (
        <button
          key={code}
          onClick={() => toggleLang(code)}
          className="px-3 py-1.5 text-[12px] rounded-md font-medium transition-all"
          style={{
            background: selectedLangs.includes(code) ? "var(--accent)" : "var(--surface-2)",
            color: selectedLangs.includes(code) ? "var(--bg)" : "var(--text-secondary)",
            border: `1px solid ${selectedLangs.includes(code) ? "var(--accent)" : "var(--border)"}`,
          }}
        >
          {LANGUAGES[code]}
        </button>
      ))}
    </div>
  );

  const handleSubmit = async () => {
    if (!file) return;
    if (!scriptFile && !scriptText.trim() && selectedLangs.length === 0) return;
    setUploading(true);
    setError(null);
    const formData = new FormData();
    formData.append("video", file);
    if (scriptFile) {
      formData.append("script", scriptFile);
      formData.append("languages", "");
    } else if (scriptText.trim()) {
      formData.append("script_text", scriptText);
      formData.append("languages", "");
    } else if (voMode) {
      // VO-mode: translate only to languages that actually have a voiceover
      // file. The picker is hidden in this mode, so selectedLangs would
      // otherwise leak its default (all 21 default languages) to the backend.
      formData.append("languages", voLangs.join(","));
    } else {
      formData.append("languages", selectedLangs.join(","));
    }
    if (voMode && captionSource === "custom") {
      if (captionScriptFile) {
        formData.append("caption_script", captionScriptFile);
      } else if (captionScriptText.trim()) {
        formData.append("caption_script_text", captionScriptText);
      }
    }
    formData.append("caption_box", JSON.stringify(captionBox));
    formData.append("caption_style", JSON.stringify(captionStyle));
    // Background blogs are only meaningful in Swap background mode —
    // suppress them in pure Translate mode so a stale bgEntries list
    // can't accidentally trigger the swap step.
    if (mode === "background-swap") {
      const validBgEntries = bgEntries.filter(e => e.url.trim() && e.lang.trim());
      if (validBgEntries.length > 0) {
        formData.append("background_urls", JSON.stringify(validBgEntries));
      }
    }
    // Voiceover audio files: rename to "<lang>.<ext>" so the backend's
    // filename → language inference is unambiguous.
    for (const v of voEntries) {
      if (!v.lang) continue;
      const ext = (v.file.name.match(/\.[^.]+$/)?.[0] || ".wav").toLowerCase();
      const renamed = new File([v.file], `${v.lang}${ext}`, { type: v.file.type });
      formData.append("voiceovers", renamed);
    }
    try {
      const res = await fetch(`${API}/api/translate`, { method: "POST", body: formData });
      if (!res.ok) {
        let msg = "Upload failed";
        try { const data = await res.json(); msg = data.detail || msg; } catch { msg = `Server error (${res.status})`; }
        throw new Error(msg);
      }
      const data = await res.json();
      // Hand the local blob URL ownership over to the queue item so it
      // survives reset() / setFile(null) below.
      const persistedUrl = file ? URL.createObjectURL(file) : "";
      const item: QueueItem = {
        jobId: data.job_id,
        fileName: file!.name,
        fileSize: file!.size,
        videoUrl: persistedUrl,
        selectedPreviewLang: null,
        source: {
          file: file!,
          scriptFile,
          scriptText,
          captionScriptFile,
          captionScriptText,
          captionSource,
          languages: [...selectedLangs],
          captionBox: { ...captionBox },
          captionStyle: { ...captionStyle },
          bgEntries: bgEntries.map((e) => ({ ...e })),
          voEntries: voEntries.map((v) => ({ ...v })),
        },
        status: {
          job_id: data.job_id,
          status: "queued",
          current_step: "Waiting in queue...",
          current_language: "",
          languages_done: [],
          languages_total: data.languages || selectedLangs,
          progress: 0,
          error: null,
          step_history: [],
          current_step_started_at: 0,
        },
      };
      setQueue((q) => [...q, item]);
      // Clear the upload form so the user can start the next one immediately.
      setFile(null);
      setScriptFile(null);
      setCaptionScriptFile(null);
      setCaptionScriptText("");
      setCaptionSource("auto");
      setBgEntries([]);
      setVoEntries([]);
      setCaptionsExpanded(false);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  };

  const cancelJob = async (jobId: string) => {
    try {
      await fetch(`${API}/api/cancel/${jobId}`, { method: "POST" });
    } catch { /* ignore */ }
  };

  const retryJob = async (jobId: string) => {
    const item = queue.find((x) => x.jobId === jobId);
    if (!item) return;
    // If still processing, cancel first so we don't pile up two runs of the
    // same source.
    if (item.status.status === "processing") {
      try { await fetch(`${API}/api/cancel/${jobId}`, { method: "POST" }); } catch {}
    }
    const src = item.source;
    const fd = new FormData();
    fd.append("video", src.file);
    if (src.scriptFile) {
      fd.append("script", src.scriptFile);
      fd.append("languages", "");
    } else if (src.scriptText.trim()) {
      fd.append("script_text", src.scriptText);
      fd.append("languages", "");
    } else {
      fd.append("languages", src.languages.join(","));
    }
    if (src.voEntries.length > 0 && src.captionSource === "custom") {
      if (src.captionScriptFile) {
        fd.append("caption_script", src.captionScriptFile);
      } else if (src.captionScriptText.trim()) {
        fd.append("caption_script_text", src.captionScriptText);
      }
    }
    fd.append("caption_box", JSON.stringify(src.captionBox));
    fd.append("caption_style", JSON.stringify(src.captionStyle));
    // Retry replays whatever the job actually shipped; if it had bg URLs,
    // it was a swap-background run by definition, so we don't re-gate on mode.
    const validBg = src.bgEntries.filter((e) => e.url.trim() && e.lang.trim());
    if (validBg.length > 0) fd.append("background_urls", JSON.stringify(validBg));
    for (const v of src.voEntries) {
      if (!v.lang) continue;
      const ext = (v.file.name.match(/\.[^.]+$/)?.[0] || ".wav").toLowerCase();
      const renamed = new File([v.file], `${v.lang}${ext}`, { type: v.file.type });
      fd.append("voiceovers", renamed);
    }

    try {
      const res = await fetch(`${API}/api/translate`, { method: "POST", body: fd });
      if (!res.ok) {
        let msg = "Retry failed";
        try { const d = await res.json(); msg = d.detail || msg; } catch {}
        throw new Error(msg);
      }
      const data = await res.json();
      // Replace the queue item in place with the new job_id + fresh status.
      setQueue((q) => q.map((x) => x.jobId !== jobId ? x : ({
        ...x,
        jobId: data.job_id,
        selectedPreviewLang: null,
        status: {
          job_id: data.job_id,
          status: "queued",
          current_step: "Waiting in queue...",
          current_language: "",
          languages_done: [],
          languages_total: data.languages || src.languages,
          progress: 0,
          error: null,
          step_history: [],
          current_step_started_at: 0,
        },
      })));
    } catch (e: any) {
      setError(e.message || "Retry failed");
    }
  };

  const removeFromQueue = (jobId: string) => {
    setQueue((q) => {
      const item = q.find((x) => x.jobId === jobId);
      if (item?.videoUrl) URL.revokeObjectURL(item.videoUrl);
      return q.filter((x) => x.jobId !== jobId);
    });
  };

  const setQueueItemPreviewLang = (jobId: string, lang: string | null) => {
    setQueue((q) => q.map((x) => (x.jobId === jobId ? { ...x, selectedPreviewLang: lang } : x)));
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f && f.type === "video/mp4") setFile(f);
  }, []);

  const reset = () => {
    setFile(null);
    setScriptFile(null);
    setScriptText("");
    setCaptionScriptFile(null);
    setCaptionScriptText("");
    setCaptionSource("auto");
    setBgEntries([]);
    setVoEntries([]);
    setSelectedLangs(DEFAULT_LANGUAGES);
    setError(null);
    setCaptionStyle(DEFAULT_CAPTION_STYLE);
    setAddMenuOpen(false);
    setBgModalOpen(false);
    setVoModalOpen(false);
    setCaptionsExpanded(false);
  };


  // --- Upload Screen ---
  const scriptMode = !!scriptFile || !!scriptText.trim();
  const voMode = voEntries.length > 0;
  // In VO-mode the language set is derived from voEntries[*].lang, not the
  // (default 21-strong) language picker. Submit + button text both read this.
  const voLangs = Array.from(new Set(voEntries.map(v => v.lang).filter(Boolean)));
  const effectiveLangCount = voMode ? voLangs.length : selectedLangs.length;
  const validBgEntriesCount = bgEntries.filter((e) => e.url.trim() && e.lang.trim()).length;
  // Swap-background mode is just translate + bg-swap, so the gating is
  // identical: need video + at least one language source. Bg URLs are
  // optional even in swap mode (the user can toggle into it just to expose
  // the UI without actually adding URLs yet).
  const canSubmit = !!file && !uploading && (scriptMode || voMode || selectedLangs.length > 0);

  return (
    <div className="max-w-7xl mx-auto px-6 py-14 space-y-8">
      {/* Mode selector — translate vs background-swap. Two-pill toggle */}
      <div className="animate-in mx-auto w-full max-w-7xl flex justify-center">
        <div
          className="inline-flex p-1 rounded-full"
          style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
        >
          {([
            { key: "translate",        label: "Translate" },
            { key: "background-swap",  label: "Swap background" },
          ] as { key: "translate" | "background-swap"; label: string }[]).map((opt) => {
            const active = mode === opt.key;
            return (
              <button
                key={opt.key}
                type="button"
                onClick={() => setMode(opt.key)}
                className="px-4 py-1.5 rounded-full text-[12px] font-medium transition-all"
                style={{
                  background: active ? "var(--accent)" : "transparent",
                  color: active ? "var(--bg)" : "var(--text-secondary)",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Header */}
      <div className="animate-in">
        <div
          className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-[11px] font-medium uppercase tracking-[0.15em]"
          style={{ background: "var(--accent-dim)", color: "var(--accent)", border: "1px solid var(--border)" }}
        >
          <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: "var(--accent)" }} />
          Ready
        </div>
        <h1
          className="text-3xl font-bold tracking-tight mt-4"
          style={{ fontFamily: "'Space Grotesk', sans-serif", color: "var(--text-primary)" }}
        >
          Video Translator
        </h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          {scriptFile
            ? "Script mode — translation will be sourced from your .docx"
            : scriptText.trim()
              ? "Script mode — translation will be sourced from your pasted text"
              : "Raw translate — pick languages and let the pipeline transcribe & translate"}
        </p>
      </div>

      {/* Hero: drop zone / video preview (left) + vertical languages list (right) */}
      <div className="animate-in grid grid-cols-1 md:grid-cols-[3fr_1fr] gap-4" style={{ animationDelay: "80ms" }}>
        <input
          id="file-input" type="file" accept="video/mp4" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) setFile(f); e.target.value = ""; }}
        />
        {file && videoUrl ? (
          // === Video preview slot (replaces dropzone after upload) ===
          <div
            ref={previewSlotRef}
            className="relative rounded-2xl overflow-hidden select-none"
            style={{ background: "#000", border: "1px solid var(--border)", aspectRatio: "16 / 9" }}
          >
            <video
              ref={setPreviewVideoRef}
              src={videoUrl}
              muted={previewMuted}
              autoPlay
              loop
              playsInline
              className="absolute inset-0 w-full h-full object-contain"
            />
            {/* Center guides */}
            <div
              className="absolute top-0 bottom-0 pointer-events-none transition-opacity"
              style={{ left: "50%", width: "1px", background: "var(--accent)", opacity: captionBox.x === 50 ? 0.9 : 0.15, transform: "translateX(-0.5px)" }}
            />
            <div
              className="absolute left-0 right-0 pointer-events-none transition-opacity"
              style={{ top: "50%", height: "1px", background: "var(--accent)", opacity: captionBox.y === 50 ? 0.9 : 0.15, transform: "translateY(-0.5px)" }}
            />
            {/* Caption text is rendered by JASSUB (libass) using the same ASS
                file the backend generates for the final burn — guarantees
                preview parity. The drag-handle box below sits ON TOP and
                lets the user move/resize the caption region; the box is
                purely an editor affordance, not a renderer. */}
            {captionStyle.enabled && (
              <CaptionPreview
                videoEl={previewVideoEl}
                captionBox={captionBox}
                captionStyle={captionStyle}
                apiBase={API}
              />
            )}
            {captionStyle.enabled && (
            <div
              onMouseDown={(e) => startCaptionDrag(e, "move")}
              className="absolute cursor-move"
              style={{
                left: `${captionBox.x}%`,
                top: `${captionBox.y}%`,
                width: `${captionBox.scale}%`,
                aspectRatio: "6 / 1",
                transform: `translate(-50%, -50%) rotate(${captionBox.rotation}deg)`,
                border: "2px dashed var(--accent)",
                borderRadius: "6px",
                background: "transparent",
              }}
            >
              {/* Resize handle (bottom-right) */}
              <div
                onMouseDown={(e) => startCaptionDrag(e, "resize")}
                className="absolute -bottom-1.5 -right-1.5 w-3 h-3 rounded-sm cursor-nwse-resize"
                style={{ background: "var(--accent)", border: "1px solid var(--bg)" }}
              />
            </div>
            )}

            {/* Top-right: replace */}
            <button
              type="button"
              onClick={() => document.getElementById("file-input")?.click()}
              className="absolute top-2 right-2 px-2.5 py-1 rounded-md text-[11px]"
              style={{ background: "rgba(0,0,0,0.55)", color: "#fff", border: "1px solid rgba(255,255,255,0.2)", fontFamily: "'JetBrains Mono', monospace", backdropFilter: "blur(6px)" }}
            >
              replace
            </button>

            {/* Bottom-left: filename */}
            <div
              className="absolute bottom-2 left-2 px-2 py-1 rounded text-[10px]"
              style={{ background: "rgba(0,0,0,0.55)", color: "#fff", fontFamily: "'JetBrains Mono', monospace", backdropFilter: "blur(6px)" }}
            >
              {file.name} · {(file.size / 1024 / 1024).toFixed(1)} MB
            </div>

            {/* Bottom-right: play/pause + mute */}
            <div className="absolute bottom-2 right-2 flex gap-1.5">
              <button
                type="button"
                onClick={() => {
                  const v = previewVideoRef.current;
                  if (!v) return;
                  if (v.paused) { v.play(); setPreviewPlaying(true); }
                  else { v.pause(); setPreviewPlaying(false); }
                }}
                className="w-8 h-8 rounded-md flex items-center justify-center"
                style={{ background: "rgba(0,0,0,0.55)", border: "1px solid rgba(255,255,255,0.2)", color: "#fff", backdropFilter: "blur(6px)" }}
                aria-label={previewPlaying ? "Pause" : "Play"}
              >
                {previewPlaying ? (
                  <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>
                ) : (
                  <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
                )}
              </button>
              <button
                type="button"
                onClick={() => setPreviewMuted((m) => !m)}
                className="w-8 h-8 rounded-md flex items-center justify-center"
                style={{ background: "rgba(0,0,0,0.55)", border: "1px solid rgba(255,255,255,0.2)", color: "#fff", backdropFilter: "blur(6px)" }}
                aria-label={previewMuted ? "Unmute" : "Mute"}
              >
                {previewMuted ? (
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15zM17 9l4 4m0-4l-4 4"/></svg>
                ) : (
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" d="M15.536 8.464a5 5 0 010 7.072M18.364 5.636a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z"/></svg>
                )}
              </button>
            </div>
          </div>
        ) : (
          // === Drop zone (no file yet) ===
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => document.getElementById("file-input")?.click()}
            className="rounded-2xl text-center cursor-pointer transition-all duration-200 flex flex-col items-center justify-center"
            style={{
              background: dragOver ? "var(--accent-dim)" : "var(--bg-card)",
              border: `1px dashed ${dragOver ? "var(--accent)" : "var(--border)"}`,
              aspectRatio: "16 / 9",
            }}
          >
            <div className="space-y-3">
              <div
                className="inline-flex items-center justify-center w-14 h-14 rounded-xl mx-auto"
                style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}
              >
                <svg className="w-6 h-6" style={{ color: "var(--text-muted)" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                </svg>
              </div>
              <p className="text-base font-medium" style={{ color: "var(--text-secondary)" }}>
                Drop your MP4 here, or click to browse
              </p>
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                Add a script, background blogs, or caption styling from the bar below
              </p>
            </div>
          </div>
        )}

        {/* When the language context comes from uploaded files, show a
            compact summary card instead of the picker. */}
        {(scriptMode || voEntries.length > 0) && (
          <div
            className="rounded-2xl p-4 flex flex-col"
            style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
          >
            <h2 className="text-[11px] font-medium uppercase tracking-[0.15em] mb-3" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
              Languages from files
            </h2>
            {voEntries.length > 0 ? (
              <div className="flex flex-col gap-1.5">
                {voEntries.map((v, i) => {
                  const valid = !!v.lang && !!LANGUAGES[v.lang];
                  return (
                    <div
                      key={i}
                      className="px-3 py-2 text-[12px] rounded-md flex items-center justify-between"
                      style={{
                        background: valid ? "var(--accent)" : "var(--surface-2)",
                        color: valid ? "var(--bg)" : "var(--error)",
                        border: `1px solid ${valid ? "var(--accent)" : "var(--border)"}`,
                      }}
                    >
                      <span className="truncate">{valid ? LANGUAGES[v.lang] : `unrecognised: ${v.file.name}`}</span>
                      <span className="text-[10px] opacity-70" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                        {valid ? v.lang.toUpperCase() : "?"}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : scriptText.trim() && detectLangsFromText(scriptText).length > 0 ? (
              <div className="flex flex-col gap-1.5">
                {detectLangsFromText(scriptText).map((code) => (
                  <div
                    key={code}
                    className="px-3 py-2 text-[12px] rounded-md flex items-center justify-between"
                    style={{
                      background: "var(--accent)",
                      color: "var(--bg)",
                      border: "1px solid var(--accent)",
                    }}
                  >
                    <span className="truncate">{LANGUAGES[code] || code}</span>
                    <span className="text-[10px] opacity-70" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                      {code.toUpperCase()}
                    </span>
                  </div>
                ))}
              </div>
            ) : scriptText.trim() ? (
              <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                No language headers detected yet. Use headers like <b>EN</b>, <b>DE</b>, <b>GR</b>.
              </p>
            ) : (
              <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                Languages will be detected from the script&apos;s headers.
              </p>
            )}
          </div>
        )}
        {mode === "background-swap" && (
          <div
            className="rounded-2xl p-4 flex flex-col"
            style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
          >
            <div className="flex items-center justify-between mb-3">
              <h2
                className="text-[11px] font-medium uppercase tracking-[0.15em]"
                style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}
              >
                Backgrounds ({bgEntries.length})
              </h2>
              <button
                onClick={() => setBgModalOpen(true)}
                className="text-[10px] font-medium uppercase tracking-wider"
                style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}
              >
                {bgEntries.length === 0 ? "Add" : "Edit"}
              </button>
            </div>
            {bgEntries.length === 0 ? (
              <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                Add one or more blog URLs. Each becomes a separate output video — the
                speaker matted over the new background, original audio preserved.
              </p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {bgEntries.map((e, i) => (
                  <div
                    key={i}
                    className="px-3 py-2 text-[12px] rounded-md flex items-center justify-between"
                    style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}
                  >
                    <span className="truncate" style={{ color: "var(--text-secondary)" }}>{e.lang || "?"}</span>
                    <span className="text-[10px] opacity-70 truncate ml-2" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
                      {(() => { try { return new URL(e.url).host; } catch { return e.url; } })()}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {!scriptMode && voEntries.length === 0 && (
          <div
            className="rounded-2xl p-4 flex flex-col"
            style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
          >
            <div className="flex items-center justify-between mb-3">
              <h2
                className="text-[11px] font-medium uppercase tracking-[0.15em]"
                style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}
              >
                Languages ({selectedLangs.length})
              </h2>
              <button
                onClick={() => toggleGroup(DEFAULT_LANGUAGES)}
                className="text-[10px] font-medium uppercase tracking-wider"
                style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}
              >
                {allInGroupSelected(DEFAULT_LANGUAGES) ? "Deselect" : "Select all"}
              </button>
            </div>
            <div className="flex flex-col gap-1.5 overflow-y-auto pr-1" style={{ maxHeight: "440px" }}>
              {[...DEFAULT_LANGUAGES]
                .sort((a, b) => (LANGUAGES[a] || a).localeCompare(LANGUAGES[b] || b))
                .map((code) => {
                  const active = selectedLangs.includes(code);
                  return (
                    <button
                      key={code}
                      onClick={() => toggleLang(code)}
                      className="px-3 py-2 text-[12px] rounded-md font-medium transition-all text-left flex items-center justify-between"
                      style={{
                        background: active ? "var(--accent)" : "var(--surface-2)",
                        color: active ? "var(--bg)" : "var(--text-secondary)",
                        border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                      }}
                    >
                      <span>{LANGUAGES[code]}</span>
                      <span className="text-[10px] opacity-70" style={{ fontFamily: "'JetBrains Mono', monospace" }}>{code.toUpperCase()}</span>
                    </button>
                  );
                })}
            </div>
          </div>
        )}
      </div>

      {/* Tools bar (centered below the hero) */}
      <div className="animate-in mx-auto w-full max-w-7xl" style={{ animationDelay: "120ms" }}>
        <div className="relative flex items-center gap-3">
          {/* Plus button */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setAddMenuOpen((v) => !v)}
              className="w-16 h-16 rounded-2xl flex items-center justify-center transition-all"
              style={{
                background: "var(--bg-card)",
                border: "1px solid var(--border)",
                color: "var(--text-secondary)",
              }}
              aria-label="Add"
            >
              <svg
                className="w-7 h-7 transition-transform"
                style={{ transform: addMenuOpen ? "rotate(45deg)" : "rotate(0)" }}
                fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14M5 12h14" />
              </svg>
            </button>
            {addMenuOpen && (
              <div
                className="absolute left-0 bottom-[72px] z-20 rounded-lg overflow-hidden min-w-[220px] animate-in"
                style={{ background: "var(--bg-card)", border: "1px solid var(--border)", boxShadow: "0 10px 30px rgba(0,0,0,0.4)" }}
              >
                <button
                  type="button"
                  onClick={() => {
                    setAddMenuOpen(false);
                    document.getElementById("script-input")?.click();
                  }}
                  className="w-full text-left px-3 py-2.5 text-sm flex items-center gap-2 transition-colors"
                  style={{ color: "var(--text-primary)" }}
                >
                  <svg className="w-4 h-4" style={{ color: "var(--text-muted)" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  Script (.docx)
                  {scriptFile && <span className="ml-auto text-[10px]" style={{ color: "var(--accent)" }}>added</span>}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setAddMenuOpen(false);
                    setScriptTextModalOpen(true);
                  }}
                  className="w-full text-left px-3 py-2.5 text-sm flex items-center gap-2 transition-colors"
                  style={{ color: "var(--text-primary)", borderTop: "1px solid var(--border)" }}
                >
                  <svg className="w-4 h-4" style={{ color: "var(--text-muted)" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                  </svg>
                  Paste translation
                  {scriptText.trim() && <span className="ml-auto text-[10px]" style={{ color: "var(--accent)" }}>added</span>}
                </button>
                <button
                  type="button"
                  onClick={() => { setAddMenuOpen(false); setVoModalOpen(true); }}
                  className="w-full text-left px-3 py-2.5 text-sm flex items-center gap-2 transition-colors"
                  style={{ color: "var(--text-primary)", borderTop: "1px solid var(--border)" }}
                >
                  <svg className="w-4 h-4" style={{ color: "var(--text-muted)" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-14 0m7 0v4m-4 0h8m-4-12a3 3 0 00-3 3v6a3 3 0 106 0V6a3 3 0 00-3-3z"/>
                  </svg>
                  Voiceover audio
                  {voEntries.length > 0 && <span className="ml-auto text-[10px]" style={{ color: "var(--accent)" }}>{voEntries.length}</span>}
                </button>
                {mode === "background-swap" && (
                <button
                  type="button"
                  onClick={() => { setAddMenuOpen(false); setBgModalOpen(true); }}
                  className="w-full text-left px-3 py-2.5 text-sm flex items-center gap-2 transition-colors"
                  style={{ color: "var(--text-primary)", borderTop: "1px solid var(--border)" }}
                >
                  <svg className="w-4 h-4" style={{ color: "var(--text-muted)" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064" />
                  </svg>
                  Background blogs
                  {bgEntries.length > 0 && <span className="ml-auto text-[10px]" style={{ color: "var(--accent)" }}>{bgEntries.length}</span>}
                </button>
                )}
              </div>
            )}
          </div>

          <input
            id="script-input"
            type="file"
            accept=".docx"
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) { setScriptFile(f); setScriptText(""); } }}
          />
          <input
            id="caption-script-input"
            type="file"
            accept=".docx"
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) { setCaptionScriptFile(f); setCaptionScriptText(""); setCaptionSource("custom"); } }}
          />

          {/* Customise captions button (occupies the rest of the bar) */}
          <button
            type="button"
            disabled={!file}
            onClick={() => file && setCaptionsExpanded((v) => !v)}
            className="flex-1 h-16 px-5 rounded-2xl text-base font-medium flex items-center justify-center gap-2.5 transition-all disabled:cursor-not-allowed"
            style={{
              background: file ? "var(--bg-card)" : "var(--surface-2)",
              border: `1px solid ${file ? "var(--border)" : "var(--border)"}`,
              color: file ? "var(--text-primary)" : "var(--text-muted)",
              opacity: file ? 1 : 0.55,
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 8h18M3 12h12M3 16h18" />
            </svg>
            Customise captions
            <svg className="w-4 h-4 ml-1 transition-transform" style={{ transform: captionsExpanded ? "rotate(180deg)" : "rotate(0)" }} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7"/></svg>
          </button>
        </div>

        {/* === Inline expanding caption customization panel === */}
        {captionsExpanded && file && (
          <div
            className="mt-3 rounded-2xl p-5 space-y-5 animate-in"
            style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
          >
            <div className="flex items-center justify-between rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
              <div>
                <p className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>Captions {captionStyle.enabled ? "enabled" : "disabled"}</p>
                <p className="text-[10px]" style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}>
                  {captionStyle.enabled ? "Burned into the final video. Live preview above." : "Final video will have no captions burned in."}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setCaptionStyle({ ...captionStyle, enabled: !captionStyle.enabled })}
                className="px-4 py-1.5 rounded-md text-[11px] font-medium"
                style={{
                  background: captionStyle.enabled ? "var(--accent)" : "var(--bg-card)",
                  color: captionStyle.enabled ? "var(--bg)" : "var(--text-secondary)",
                  border: "1px solid var(--border)",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                {captionStyle.enabled ? "ON" : "OFF"}
              </button>
            </div>

            {/* Caption text source — only meaningful with uploaded VO. In
                script mode the script already provides the text; in default
                (transcribe + translate) mode captions are auto-derived. So
                show this picker exclusively when voMode is active. */}
            {voMode && (
            <div>
              <h4 className="text-[11px] font-medium uppercase tracking-[0.15em] mb-2" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
                Caption text
              </h4>
              <div className="grid grid-cols-2 gap-2">
                {([
                  { key: "auto",   label: "Auto",   sub: "Derived from translation" },
                  { key: "custom", label: "Custom", sub: "I'll provide the text" },
                ] as { key: "auto" | "custom"; label: string; sub: string }[]).map((opt) => {
                  const active = captionSource === opt.key;
                  return (
                    <button
                      key={opt.key}
                      type="button"
                      onClick={() => setCaptionSource(opt.key)}
                      className="rounded-lg px-3 py-2.5 text-left transition-all"
                      style={{
                        background: active ? "var(--accent-dim)" : "var(--surface-2)",
                        border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                        color: active ? "var(--accent)" : "var(--text-secondary)",
                      }}
                    >
                      <div className="text-[12px] font-medium">{opt.label}</div>
                      <div className="text-[10px] mt-0.5" style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}>{opt.sub}</div>
                    </button>
                  );
                })}
              </div>
              {captionSource === "custom" && (
                <div className="mt-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="text-[10px]" style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}>
                      Use language headers (<b>EN</b>, <b>DE</b>, <b>GR</b>) to separate sections — same format as the script.
                    </p>
                    <div className="flex gap-2">
                      {(scriptText.trim() || captionScriptText.trim()) && (
                        <button
                          type="button"
                          onClick={() => {
                            // Seed the caption textarea from the translation script paste,
                            // so the user can tweak rather than retype.
                            if (scriptText.trim() && !captionScriptText.trim()) {
                              setCaptionScriptText(scriptText);
                              setCaptionScriptFile(null);
                            }
                          }}
                          className="text-[10px] underline"
                          style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}
                          title="Copy the translation paste into the caption editor"
                        >
                          {scriptText.trim() && !captionScriptText.trim() ? "Copy from script paste" : ""}
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => document.getElementById("caption-script-input")?.click()}
                        className="text-[10px] underline"
                        style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}
                      >
                        {captionScriptFile ? `replace .docx (${captionScriptFile.name})` : "or upload .docx"}
                      </button>
                    </div>
                  </div>
                  {captionScriptFile ? (
                    <div
                      className="rounded-lg p-3 flex items-center justify-between"
                      style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}
                    >
                      <div className="text-[12px]" style={{ color: "var(--accent)", fontFamily: "'JetBrains Mono', monospace" }}>
                        {captionScriptFile.name}
                      </div>
                      <button
                        type="button"
                        onClick={() => setCaptionScriptFile(null)}
                        className="text-[11px]"
                        style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}
                      >
                        clear
                      </button>
                    </div>
                  ) : (
                    <textarea
                      value={captionScriptText}
                      onChange={(e) => setCaptionScriptText(e.target.value)}
                      placeholder={"EN\nHello, welcome to our channel.\n\nDE\nHallo, willkommen auf unserem Kanal."}
                      className="w-full rounded-lg p-3 text-sm resize-y"
                      style={{
                        background: "var(--surface-2)",
                        border: "1px solid var(--border)",
                        color: "var(--text-primary)",
                        fontFamily: "'JetBrains Mono', monospace",
                        minHeight: "160px",
                      }}
                    />
                  )}
                </div>
              )}
            </div>
            )}

            {/* Position sliders */}
            <div>
              <h4 className="text-[11px] font-medium uppercase tracking-[0.15em] mb-2" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Position</h4>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {[
                  { key: "x",        label: "X",        min: 0,    max: 100, step: 0.5, suffix: "%" },
                  { key: "y",        label: "Y",        min: 0,    max: 100, step: 0.5, suffix: "%" },
                  { key: "scale",    label: "Scale",    min: 5,    max: 100, step: 0.5, suffix: "%" },
                  { key: "rotation", label: "Rotation", min: -180, max: 180, step: 1,   suffix: "°" },
                ].map((c) => {
                  const val = (captionBox as any)[c.key] as number;
                  return (
                    <div key={c.key} className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>{c.label}</span>
                        <span className="text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}>{val.toFixed(c.step < 1 ? 1 : 0)}{c.suffix}</span>
                      </div>
                      <input
                        type="range" min={c.min} max={c.max} step={c.step} value={val}
                        onChange={(e) => {
                          let next = parseFloat(e.target.value);
                          if ((c.key === "x" || c.key === "y") && Math.abs(next - 50) < 2) next = 50;
                          if (c.key === "rotation" && Math.abs(next) < 3) next = 0;
                          setCaptionBox((b) => ({ ...b, [c.key]: next }));
                        }}
                        className="w-full"
                        style={{ accentColor: "var(--accent)" }}
                      />
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Style controls */}
            <div>
              <h4 className="text-[11px] font-medium uppercase tracking-[0.15em] mb-2" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Style</h4>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                {/* Font */}
                <div className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Font</span>
                  </div>
                  <select
                    value={captionStyle.font_name}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, font_name: e.target.value })}
                    className="w-full px-2 py-1.5 text-sm rounded-md"
                    style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  >
                    {FONT_CHOICES.map((f) => <option key={f} value={f} style={{ fontFamily: f }}>{f}</option>)}
                  </select>
                </div>
                {/* Bold */}
                <div className="rounded-lg px-3 py-2.5 flex items-center justify-between" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Bold</span>
                  <button
                    type="button"
                    onClick={() => setCaptionStyle({ ...captionStyle, bold: !captionStyle.bold })}
                    className="px-3 py-1 rounded text-[11px] font-medium"
                    style={{
                      background: captionStyle.bold ? "var(--accent)" : "var(--bg-card)",
                      color: captionStyle.bold ? "var(--bg)" : "var(--text-secondary)",
                      border: "1px solid var(--border)",
                      fontFamily: "'JetBrains Mono', monospace",
                    }}
                  >
                    {captionStyle.bold ? "ON" : "OFF"}
                  </button>
                </div>
                {/* Fill color */}
                <div className="rounded-lg px-3 py-2.5 flex items-center justify-between gap-3" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Fill</span>
                  <input
                    type="color"
                    value={captionStyle.primary_color}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, primary_color: e.target.value })}
                    className="w-10 h-7 rounded cursor-pointer"
                    style={{ background: "transparent", border: "1px solid var(--border)" }}
                  />
                </div>
                {/* Stroke color */}
                <div className="rounded-lg px-3 py-2.5 flex items-center justify-between gap-3" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Stroke color</span>
                  <input
                    type="color"
                    value={captionStyle.outline_color}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, outline_color: e.target.value })}
                    className="w-10 h-7 rounded cursor-pointer"
                    style={{ background: "transparent", border: "1px solid var(--border)" }}
                  />
                </div>
                {/* Stroke width */}
                <div className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Stroke width</span>
                    <span className="text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}>{captionStyle.outline_width.toFixed(1)}</span>
                  </div>
                  <input
                    type="range" min={0} max={6} step={0.5}
                    value={captionStyle.outline_width}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, outline_width: parseFloat(e.target.value) })}
                    className="w-full"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
                {/* Shadow color */}
                <div className="rounded-lg px-3 py-2.5 flex items-center justify-between gap-3" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Shadow color</span>
                  <input
                    type="color"
                    value={captionStyle.shadow_color}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, shadow_color: e.target.value })}
                    className="w-10 h-7 rounded cursor-pointer"
                    style={{ background: "transparent", border: "1px solid var(--border)" }}
                  />
                </div>
                {/* Shadow offset */}
                <div className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Shadow offset</span>
                    <span className="text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}>{captionStyle.shadow.toFixed(1)}</span>
                  </div>
                  <input
                    type="range" min={0} max={8} step={0.5}
                    value={captionStyle.shadow}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, shadow: parseFloat(e.target.value) })}
                    className="w-full"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
                {/* Shadow opacity */}
                <div className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Shadow opacity</span>
                    <span className="text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}>{Math.round(captionStyle.shadow_alpha * 100)}%</span>
                  </div>
                  <input
                    type="range" min={0} max={1} step={0.05}
                    value={captionStyle.shadow_alpha}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, shadow_alpha: parseFloat(e.target.value) })}
                    className="w-full"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
                {/* Shadow softness (blur) */}
                <div className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Shadow softness</span>
                    <span className="text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}>{captionStyle.shadow_blur.toFixed(1)}</span>
                  </div>
                  <input
                    type="range" min={0} max={10} step={0.5}
                    value={captionStyle.shadow_blur}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, shadow_blur: parseFloat(e.target.value) })}
                    className="w-full"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
              </div>
            </div>

            {/* Spacing — letter (tracking) and line (leading) */}
            <div>
              <h4 className="text-[11px] font-medium uppercase tracking-[0.15em] mb-2" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Spacing</h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {/* Letter spacing (tracking) */}
                <div className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Tracking</span>
                    <span className="text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}>{(captionStyle.letter_spacing * 1000).toFixed(0)}</span>
                  </div>
                  <input
                    type="range" min={-0.05} max={0.30} step={0.005}
                    value={captionStyle.letter_spacing}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, letter_spacing: parseFloat(e.target.value) })}
                    className="w-full"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
                {/* Line spacing (leading) */}
                <div className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Leading</span>
                    <span className="text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}>{captionStyle.line_spacing.toFixed(2)}×</span>
                  </div>
                  <input
                    type="range" min={0.8} max={2.0} step={0.05}
                    value={captionStyle.line_spacing}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, line_spacing: parseFloat(e.target.value) })}
                    className="w-full"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
              </div>
            </div>

            {/* Breaks — how words pack into lines, and lines into captions */}
            <div>
              <h4 className="text-[11px] font-medium uppercase tracking-[0.15em] mb-2" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Breaks</h4>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {/* Pop animation toggle (replaces former Words / line slider —
                    line width is now driven implicitly by the caption box). */}
                <div className="rounded-lg px-3 py-2.5 flex items-center justify-between" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Pop animation</span>
                  <button
                    type="button"
                    onClick={() => setCaptionStyle({ ...captionStyle, pop_animation: !captionStyle.pop_animation })}
                    className="px-3 py-1 rounded text-[11px] font-medium"
                    style={{
                      background: captionStyle.pop_animation ? "var(--accent)" : "var(--bg-card)",
                      color: captionStyle.pop_animation ? "var(--bg)" : "var(--text-secondary)",
                      border: "1px solid var(--border)",
                      fontFamily: "'JetBrains Mono', monospace",
                    }}
                  >
                    {captionStyle.pop_animation ? "ON" : "OFF"}
                  </button>
                </div>
                {/* Lines per caption */}
                <div className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Lines / caption</span>
                    <span className="text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}>{captionStyle.max_lines_per_caption} max</span>
                  </div>
                  <input
                    type="range" min={1} max={3} step={1}
                    value={captionStyle.max_lines_per_caption}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, max_lines_per_caption: parseInt(e.target.value, 10) })}
                    className="w-full"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
                {/* Pause break (ms) */}
                <div className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Pause break</span>
                    <span className="text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}>{captionStyle.break_on_pause_ms <= 0 ? "off" : `${captionStyle.break_on_pause_ms} ms`}</span>
                  </div>
                  <input
                    type="range" min={0} max={500} step={10}
                    value={captionStyle.break_on_pause_ms}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, break_on_pause_ms: parseInt(e.target.value, 10) })}
                    className="w-full"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
                {/* Break on punctuation toggle */}
                <div className="rounded-lg px-3 py-2.5 flex items-center justify-between" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Break on .,;:!?</span>
                  <button
                    type="button"
                    onClick={() => setCaptionStyle({ ...captionStyle, break_on_punct: !captionStyle.break_on_punct })}
                    className="px-3 py-1 rounded text-[11px] font-medium"
                    style={{
                      background: captionStyle.break_on_punct ? "var(--accent)" : "var(--bg-card)",
                      color: captionStyle.break_on_punct ? "var(--bg)" : "var(--text-secondary)",
                      border: "1px solid var(--border)",
                      fontFamily: "'JetBrains Mono', monospace",
                    }}
                  >
                    {captionStyle.break_on_punct ? "ON" : "OFF"}
                  </button>
                </div>
              </div>
            </div>

            {/* Glow */}
            <div>
              <h4 className="text-[11px] font-medium uppercase tracking-[0.15em] mb-2" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Glow (optional)</h4>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-lg px-3 py-2.5 flex items-center justify-between" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Enable</span>
                  <button
                    type="button"
                    onClick={() => setCaptionStyle({ ...captionStyle, glow: !captionStyle.glow })}
                    className="px-3 py-1 rounded text-[11px] font-medium"
                    style={{
                      background: captionStyle.glow ? "var(--accent)" : "var(--bg-card)",
                      color: captionStyle.glow ? "var(--bg)" : "var(--text-secondary)",
                      border: "1px solid var(--border)",
                      fontFamily: "'JetBrains Mono', monospace",
                    }}
                  >
                    {captionStyle.glow ? "ON" : "OFF"}
                  </button>
                </div>
                <div className="rounded-lg px-3 py-2.5 flex items-center justify-between gap-3" style={{ background: "var(--surface-2)", border: "1px solid var(--border)", opacity: captionStyle.glow ? 1 : 0.55 }}>
                  <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Color</span>
                  <input
                    type="color"
                    value={captionStyle.glow_color}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, glow_color: e.target.value })}
                    disabled={!captionStyle.glow}
                    className="w-10 h-7 rounded cursor-pointer"
                    style={{ background: "transparent", border: "1px solid var(--border)" }}
                  />
                </div>
                <div className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)", border: "1px solid var(--border)", opacity: captionStyle.glow ? 1 : 0.55 }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>Strength</span>
                    <span className="text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}>{captionStyle.glow_strength.toFixed(1)}</span>
                  </div>
                  <input
                    type="range" min={0} max={10} step={0.5}
                    value={captionStyle.glow_strength}
                    onChange={(e) => setCaptionStyle({ ...captionStyle, glow_strength: parseFloat(e.target.value) })}
                    disabled={!captionStyle.glow}
                    className="w-full"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
              </div>
            </div>

            <div className="flex items-center justify-between pt-1">
              <button
                onClick={() => {
                  setCaptionBox({ x: 50, y: 85, scale: 60, rotation: 0 });
                  setCaptionStyle(DEFAULT_CAPTION_STYLE);
                }}
                className="text-[11px] uppercase tracking-wider underline"
                style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}
              >
                Reset all
              </button>
              <button
                onClick={() => setCaptionsExpanded(false)}
                className="px-4 py-1.5 text-xs rounded-md"
                style={{ background: "var(--accent)", color: "var(--bg)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                Done
              </button>
            </div>
          </div>
        )}

        {/* Active extras pill row (centered) */}
        {(scriptFile || scriptText.trim() || bgEntries.length > 0 || voEntries.length > 0) && (
          <div className="mt-3 flex flex-wrap gap-2 justify-center">
            {scriptFile && (
              <span
                className="inline-flex items-center gap-2 px-2.5 py-1 rounded-md text-[11px]"
                style={{ background: "var(--accent-dim)", color: "var(--accent)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                script: {scriptFile.name}
                <button onClick={() => setScriptFile(null)} className="opacity-70 hover:opacity-100">×</button>
              </span>
            )}
            {!scriptFile && scriptText.trim() && (
              <span
                className="inline-flex items-center gap-2 px-2.5 py-1 rounded-md text-[11px]"
                style={{ background: "var(--accent-dim)", color: "var(--accent)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                pasted translation
                <button onClick={() => setScriptText("")} className="opacity-70 hover:opacity-100">×</button>
              </span>
            )}
            {voEntries.length > 0 && (
              <span
                className="inline-flex items-center gap-2 px-2.5 py-1 rounded-md text-[11px]"
                style={{ background: "var(--accent-dim)", color: "var(--accent)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                {voEntries.length} voiceover{voEntries.length > 1 ? "s" : ""} ({voEntries.map(v => v.lang || "?").join(", ")})
                <button onClick={() => setVoEntries([])} className="opacity-70 hover:opacity-100">×</button>
              </span>
            )}
            {bgEntries.length > 0 && (
              <span
                className="inline-flex items-center gap-2 px-2.5 py-1 rounded-md text-[11px]"
                style={{ background: "var(--accent-dim)", color: "var(--accent)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                {bgEntries.length} background{bgEntries.length > 1 ? "s" : ""}
                <button onClick={() => setBgEntries([])} className="opacity-70 hover:opacity-100">×</button>
              </span>
            )}
          </div>
        )}
      </div>

      {/* (legacy two-column layout below is hidden — kept commented for safety) */}
      <div className="hidden">
      <div className="animate-in grid grid-cols-1 md:grid-cols-[2fr_1fr] gap-4" style={{ animationDelay: "80ms" }}>
        {/* Video drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => document.getElementById("file-input")?.click()}
          className="rounded-xl p-10 text-center cursor-pointer transition-all duration-200"
          style={{
            background: dragOver ? "var(--accent-dim)" : "var(--bg-card)",
            border: `1px dashed ${dragOver ? "var(--accent)" : "var(--border)"}`,
          }}
        >
          <input
            id="file-input" type="file" accept="video/mp4" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) setFile(f); }}
          />
          {file ? (
            <div className="space-y-1">
              <div
                className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg mb-2"
                style={{ background: "var(--accent-dim)", border: "1px solid var(--border)" }}
              >
                <svg className="w-4 h-4" style={{ color: "var(--accent)" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                <span className="text-sm font-medium" style={{ color: "var(--accent)" }}>{file.name}</span>
              </div>
              <p className="text-xs" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
                {(file.size / 1024 / 1024).toFixed(1)} MB
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              <div
                className="inline-flex items-center justify-center w-12 h-12 rounded-lg mx-auto"
                style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}
              >
                <svg className="w-5 h-5" style={{ color: "var(--text-muted)" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                </svg>
              </div>
              <p className="text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
                Drop MP4 here or click to browse
              </p>
            </div>
          )}
        </div>

        {/* Optional script .docx field */}
        <div
          onClick={() => document.getElementById("script-input")?.click()}
          className="rounded-xl p-6 text-center cursor-pointer transition-all duration-200 flex flex-col justify-center"
          style={{
            background: scriptMode ? "var(--accent-dim)" : "var(--bg-card)",
            border: `1px dashed ${scriptMode ? "var(--accent)" : "var(--border)"}`,
          }}
        >
          <input
            id="script-input"
            type="file"
            accept=".docx"
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) { setScriptFile(f); setScriptText(""); } }}
          />
          <p className="text-[11px] font-medium uppercase tracking-[0.15em] mb-2"
             style={{ fontFamily: "'JetBrains Mono', monospace", color: scriptMode ? "var(--accent)" : "var(--text-muted)" }}>
            Optional script (.docx)
          </p>
          {scriptFile ? (
            <>
              <p className="text-sm font-medium truncate" style={{ color: "var(--accent)" }}>{scriptFile.name}</p>
              <button
                onClick={(e) => { e.stopPropagation(); setScriptFile(null); }}
                className="text-[11px] mt-2 underline"
                style={{ color: "var(--text-muted)" }}
              >
                remove
              </button>
            </>
          ) : (
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              Bold language headers (e.g. <b>EN</b>, <b>DE</b>, <b>GR</b>) define sections.
              When provided, languages come from the script.
            </p>
          )}
        </div>

        {/* Per-language background URL list */}
        <div
          className="rounded-xl p-4 flex flex-col"
          style={{
            background: bgEntries.length > 0 ? "var(--accent-dim)" : "var(--bg-card)",
            border: `1px dashed ${bgEntries.length > 0 ? "var(--accent)" : "var(--border)"}`,
          }}
        >
          <p className="text-[11px] font-medium uppercase tracking-[0.15em] mb-3"
             style={{ fontFamily: "'JetBrains Mono', monospace", color: bgEntries.length > 0 ? "var(--accent)" : "var(--text-muted)" }}>
            Optional background blogs (per language)
          </p>

          <div className="flex flex-col gap-2">
            {bgEntries.map((entry, i) => (
              <div key={i} className="flex gap-2 items-center">
                <input
                  type="url"
                  placeholder="https://example.com/blog-post"
                  value={entry.url}
                  onChange={(e) => {
                    const next = [...bgEntries];
                    next[i] = { ...next[i], url: e.target.value };
                    setBgEntries(next);
                  }}
                  className="flex-1 px-3 py-2 text-sm rounded-md"
                  style={{
                    background: "var(--bg-card)",
                    border: "1px solid var(--border)",
                    color: "var(--text)",
                  }}
                />
                <select
                  value={entry.lang}
                  onChange={(e) => {
                    const next = [...bgEntries];
                    next[i] = { ...next[i], lang: e.target.value };
                    setBgEntries(next);
                  }}
                  className="px-2 py-2 text-sm rounded-md"
                  style={{
                    background: "var(--bg-card)",
                    border: "1px solid var(--border)",
                    color: "var(--text)",
                  }}
                >
                  <option value="">lang</option>
                  {Object.entries(LANGUAGES).map(([code, name]) => (
                    <option key={code} value={code}>{code.toUpperCase()} — {name}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => setBgEntries(bgEntries.filter((_, j) => j !== i))}
                  className="px-2 py-2 text-sm rounded-md"
                  style={{ color: "var(--text-muted)", border: "1px solid var(--border)" }}
                  aria-label="Remove"
                >
                  ×
                </button>
              </div>
            ))}
          </div>

          <button
            type="button"
            onClick={() => setBgEntries([...bgEntries, { url: "", lang: "" }])}
            className="mt-3 self-start px-3 py-1.5 text-xs rounded-md flex items-center gap-1"
            style={{
              background: "var(--bg-card)",
              border: "1px dashed var(--border)",
              color: "var(--text-muted)",
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            <span style={{ fontSize: 14, lineHeight: 1 }}>+</span> add background
          </button>

          {bgEntries.length === 0 && (
            <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
              Each entry replaces the visual background for that language's dub
              with a screen-record of the URL, scrolled to match the original
              video's pacing.
            </p>
          )}
        </div>
      </div>

      {/* Caption-box preview */}
      {videoUrl && (
        <div className="animate-in space-y-3" style={{ animationDelay: "120ms" }}>
          <h2
            className="text-[11px] font-medium uppercase tracking-[0.15em]"
            style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}
          >
            Caption box preview
          </h2>

          <div
            className="relative w-full rounded-xl overflow-hidden"
            style={{ background: "#000", border: "1px solid var(--border)", aspectRatio: "16 / 9" }}
          >
            <video
              src={videoUrl}
              muted
              loop
              autoPlay
              playsInline
              className="absolute inset-0 w-full h-full object-contain"
            />
            {/* Center guide lines — highlight when X/Y snapped to 50 */}
            <div
              className="absolute top-0 bottom-0 pointer-events-none transition-opacity"
              style={{
                left: "50%",
                width: "1px",
                background: "var(--accent)",
                opacity: captionBox.x === 50 ? 0.9 : 0.15,
                transform: "translateX(-0.5px)",
              }}
            />
            <div
              className="absolute left-0 right-0 pointer-events-none transition-opacity"
              style={{
                top: "50%",
                height: "1px",
                background: "var(--accent)",
                opacity: captionBox.y === 50 ? 0.9 : 0.15,
                transform: "translateY(-0.5px)",
              }}
            />

            {/* Overlay box (percent-positioned, transform-centered) */}
            <div
              className="absolute pointer-events-none"
              style={{
                left: `${captionBox.x}%`,
                top: `${captionBox.y}%`,
                width: `${captionBox.scale}%`,
                aspectRatio: "6 / 1",
                transform: `translate(-50%, -50%) rotate(${captionBox.rotation}deg)`,
                border: "2px solid var(--accent)",
                borderRadius: "6px",
                background: "rgba(0,0,0,0.25)",
                boxShadow: "0 0 0 1px rgba(0,0,0,0.6)",
              }}
            >
              <div
                className="absolute -top-5 left-0 text-[10px] px-1.5 py-0.5 rounded"
                style={{
                  fontFamily: "'JetBrains Mono', monospace",
                  background: "var(--accent)",
                  color: "var(--bg)",
                }}
              >
                CAPTION
              </div>
            </div>
          </div>

          {/* Sliders */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {[
              { key: "x",        label: "X",        min: 0,    max: 100, step: 0.5, suffix: "%" },
              { key: "y",        label: "Y",        min: 0,    max: 100, step: 0.5, suffix: "%" },
              { key: "scale",    label: "Scale",    min: 5,    max: 100, step: 0.5, suffix: "%" },
              { key: "rotation", label: "Rotation", min: -180, max: 180, step: 1,   suffix: "°" },
            ].map((c) => {
              const val = (captionBox as any)[c.key] as number;
              return (
                <div
                  key={c.key}
                  className="rounded-lg px-3 py-2.5"
                  style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span
                      className="text-[11px] uppercase tracking-[0.15em]"
                      style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}
                    >
                      {c.label}
                    </span>
                    <span
                      className="text-[11px]"
                      style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--accent)" }}
                    >
                      {val.toFixed(c.step < 1 ? 1 : 0)}{c.suffix}
                    </span>
                  </div>
                  <input
                    type="range"
                    min={c.min}
                    max={c.max}
                    step={c.step}
                    value={val}
                    onChange={(e) => {
                      let next = parseFloat(e.target.value);
                      // Magnet snap to 50% for X and Y (center of each axis)
                      if ((c.key === "x" || c.key === "y") && Math.abs(next - 50) < 2) {
                        next = 50;
                      }
                      // Magnet snap to 0° for rotation
                      if (c.key === "rotation" && Math.abs(next) < 3) {
                        next = 0;
                      }
                      setCaptionBox((b) => ({ ...b, [c.key]: next }));
                    }}
                    className="w-full accent-current"
                    style={{ accentColor: "var(--accent)" }}
                  />
                </div>
              );
            })}
          </div>

          <button
            onClick={() => setCaptionBox({ x: 50, y: 85, scale: 60, rotation: 0 })}
            className="text-[11px] uppercase tracking-wider underline"
            style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}
          >
            Reset
          </button>
        </div>
      )}

      </div>{/* /hidden legacy block */}


      {scriptMode && (
        <div className="animate-in mx-auto w-full max-w-2xl rounded-xl p-4 text-sm text-center" style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          Languages will be detected from the {scriptFile ? "script's" : "text's"} bold headers. Voice profiles
          and timestamps still come from the video; the {scriptFile ? "script" : "pasted text"} supplies the translated
          dialogue and captions.
        </div>
      )}

      {error && (
        <p className="text-sm animate-in text-center" style={{ color: "var(--error)" }}>{error}</p>
      )}

      {/* Submit */}
      <div className="animate-in mx-auto w-full max-w-2xl" style={{ animationDelay: "240ms" }}>
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="btn-3d w-full py-3.5 rounded-lg font-semibold text-[14px] transition-all disabled:opacity-30 disabled:cursor-not-allowed"
          style={{
            fontFamily: "'JetBrains Mono', monospace",
            background: !canSubmit ? "var(--surface-2)" : "var(--accent)",
            color: !canSubmit ? "var(--text-muted)" : "var(--bg)",
            border: `1px solid ${!canSubmit ? "var(--border)" : "var(--accent)"}`,
          }}
        >
          {(() => {
            if (uploading) return "Uploading...";
            const base = scriptFile
              ? "Translate from script"
              : scriptText.trim()
                ? "Translate from pasted text"
                : `Translate → ${effectiveLangCount} language${effectiveLangCount === 1 ? "" : "s"}`;
            if (mode === "background-swap" && validBgEntriesCount > 0) {
              return `${base} + swap ${validBgEntriesCount} bg →`;
            }
            return scriptFile || scriptText.trim() ? `${base} →` : base;
          })()}
        </button>
      </div>

      {/* === Job queue === */}
      {queue.length > 0 && (
        <div className="animate-in mx-auto w-full max-w-7xl space-y-3">
          <h2 className="text-[11px] font-medium uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
            Queue ({queue.length})
          </h2>

          {queue.map((q) => {
            const s = q.status;
            const progress = typeof s.progress === "number" ? s.progress : 0;
            const liveElapsed = s.current_step_started_at ? Math.max(0, nowTs - s.current_step_started_at) : 0;
            const totalElapsed = (s.step_history || []).reduce((a, x) => a + (x.seconds || 0), 0) + liveElapsed;
            const isQueued = s.status === "queued";
            const isProcessing = s.status === "processing";
            const isDone = s.status === "completed";
            const isError = s.status === "error";
            const previewLang = q.selectedPreviewLang;

            return (
              <div
                key={q.jobId}
                className="rounded-2xl p-4 grid grid-cols-1 md:grid-cols-[280px_1fr] gap-4"
                style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
              >
                {/* Left: video preview */}
                <div className="relative rounded-xl overflow-hidden" style={{ background: "#000", border: "1px solid var(--border)", aspectRatio: "16 / 9" }}>
                  <video
                    key={isDone && previewLang ? `${q.jobId}-${previewLang}` : `${q.jobId}-orig`}
                    src={isDone && previewLang ? `${API}/api/download/${q.jobId}/${previewLang}` : q.videoUrl}
                    muted
                    autoPlay
                    loop
                    playsInline
                    controls={isDone}
                    className="absolute inset-0 w-full h-full object-contain"
                  />
                  <div
                    className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded text-[9px]"
                    style={{
                      background: isQueued ? "rgba(100,100,140,0.85)" : isProcessing ? "rgba(0,0,0,0.6)" : isDone ? "rgba(34,197,94,0.85)" : "rgba(239,68,68,0.85)",
                      color: "#fff",
                      fontFamily: "'JetBrains Mono', monospace",
                      backdropFilter: "blur(6px)",
                    }}
                  >
                    {isQueued ? "QUEUED" : isProcessing ? "PROCESSING" : isDone ? "DONE" : "ERROR"}
                  </div>
                </div>

                {/* Right: status + controls */}
                <div className="flex flex-col gap-3 min-w-0">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate" style={{ color: "var(--text-primary)" }}>{q.fileName}</p>
                      <p className="text-[11px]" style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}>
                        {(q.fileSize / 1024 / 1024).toFixed(1)} MB · {s.languages_total.length} langs
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() => retryJob(q.jobId)}
                        className="px-3 py-1.5 text-[11px] rounded-md"
                        style={{ background: "var(--surface-2)", color: "var(--accent)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
                      >
                        Retry
                      </button>
                      {(isProcessing || isQueued) ? (
                        <button
                          onClick={() => cancelJob(q.jobId)}
                          className="px-3 py-1.5 text-[11px] rounded-md"
                          style={{ background: "var(--surface-2)", color: "var(--error)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
                        >
                          Cancel
                        </button>
                      ) : (
                        <button
                          onClick={() => removeFromQueue(q.jobId)}
                          className="px-3 py-1.5 text-[11px] rounded-md"
                          style={{ background: "var(--surface-2)", color: "var(--text-muted)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
                        >
                          Remove
                        </button>
                      )}
                    </div>
                  </div>

                  {isQueued && (
                    <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>
                      Waiting in queue...
                    </p>
                  )}

                  {isProcessing && (
                    <>
                      <div>
                        <p className="text-[12px] mb-1.5" style={{ color: "var(--text-secondary)" }}>{s.current_step}</p>
                        <div className="w-full rounded-full h-1.5 overflow-hidden" style={{ background: "var(--surface)" }}>
                          <div className="progress-shimmer h-full rounded-full transition-all duration-700" style={{ width: `${Math.max(progress, 4)}%` }} />
                        </div>
                        <p className="text-[10px] mt-1" style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}>
                          {s.languages_done.length}/{s.languages_total.length} · {progress.toFixed(0)}% · {fmtSeconds(totalElapsed)} elapsed
                        </p>
                      </div>
                      {s.languages_done.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {s.languages_done.map((lang) => (
                            <a
                              key={lang}
                              href={`${API}/api/download/${q.jobId}/${lang}`}
                              download
                              className="px-2 py-1 text-[10px] rounded-md"
                              style={{ background: "var(--surface-2)", color: "var(--text-secondary)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
                            >
                              {lang.toUpperCase()} ↓
                            </a>
                          ))}
                        </div>
                      )}
                    </>
                  )}

                  {isError && (
                    <p className="text-[12px]" style={{ color: "var(--error)" }}>{s.error || "Error"}</p>
                  )}

                  {isDone && (
                    <>
                      <div className="flex flex-wrap gap-1.5">
                        {s.languages_total.map((lang) => (
                          <button
                            key={lang}
                            onClick={() => setQueueItemPreviewLang(q.jobId, lang)}
                            className="px-2.5 py-1 text-[11px] rounded-md font-medium"
                            style={{
                              background: previewLang === lang ? "var(--accent)" : "var(--surface-2)",
                              color: previewLang === lang ? "var(--bg)" : "var(--text-secondary)",
                              border: `1px solid ${previewLang === lang ? "var(--accent)" : "var(--border)"}`,
                              fontFamily: "'JetBrains Mono', monospace",
                            }}
                          >
                            {LANGUAGES[lang] || lang}
                          </button>
                        ))}
                      </div>
                      {previewLang && (
                        <div className="self-start flex items-center gap-2">
                          <a
                            href={`${API}/api/download/${q.jobId}/${previewLang}`}
                            download
                            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-[11px]"
                            style={{ background: "var(--accent)", color: "var(--bg)", fontFamily: "'JetBrains Mono', monospace" }}
                          >
                            Download {LANGUAGES[previewLang]}
                          </a>
                          <a
                            href={`/edit/${q.jobId}`}
                            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-[11px]"
                            style={{ background: "var(--bg-card)", color: "var(--text-secondary)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
                            title="Open the timeline editor for this job"
                          >
                            Edit
                          </a>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* === Voiceover audio modal === */}
      {/* Paste translation modal */}
      {scriptTextModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center px-4"
          style={{ background: "rgba(0,0,0,0.65)" }}
          onClick={() => setScriptTextModalOpen(false)}
        >
          <div
            className="w-full max-w-2xl rounded-2xl p-6 space-y-4 animate-in"
            style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold" style={{ color: "var(--text-primary)", fontFamily: "'Space Grotesk', sans-serif" }}>
              Paste translation
            </h3>
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              Use language headers like <b>EN</b>, <b>DE</b>, <b>GR</b> to separate sections. Same format as the .docx script.
            </p>
            <textarea
              value={scriptText}
              onChange={(e) => setScriptText(e.target.value)}
              placeholder={"EN\nHello, welcome to our channel.\n\nDE\nHallo, willkommen auf unserem Kanal.\n\nEL\n\u0393\u03b5\u03b9\u03b1 \u03c3\u03b1\u03c2, \u03ba\u03b1\u03bb\u03c9\u03c2 \u03ae\u03c1\u03b8\u03b1\u03c4\u03b5 \u03c3\u03c4\u03bf \u03ba\u03b1\u03bd\u03ac\u03bb\u03b9 \u03bc\u03b1\u03c2."}
              className="w-full rounded-lg p-3 text-sm resize-y"
              style={{
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
                fontFamily: "'JetBrains Mono', monospace",
                minHeight: "220px",
              }}
            />
            <div className="flex justify-end gap-2">
              {scriptText.trim() && (
                <button
                  onClick={() => { setScriptText(""); }}
                  className="px-4 py-1.5 text-xs rounded-md"
                  style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}
                >
                  Clear
                </button>
              )}
              <button
                onClick={() => {
                  if (scriptText.trim()) setScriptFile(null);
                  setScriptTextModalOpen(false);
                }}
                className="px-4 py-1.5 text-xs rounded-md"
                style={{ background: "var(--accent)", color: "var(--bg)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {voModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center px-4"
          style={{ background: "rgba(0,0,0,0.65)" }}
          onClick={() => setVoModalOpen(false)}
        >
          <div
            className="w-full max-w-2xl rounded-2xl p-6 space-y-4 animate-in"
            style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3
                className="text-sm font-medium uppercase tracking-[0.15em]"
                style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-secondary)" }}
              >
                Voiceover audio (per language)
              </h3>
              <button onClick={() => setVoModalOpen(false)} className="text-xl leading-none" style={{ color: "var(--text-muted)" }}>×</button>
            </div>
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              Upload pre-recorded narration files. Filename should hint at the
              language (<span style={{ fontFamily: "'JetBrains Mono', monospace" }}>en.mp3</span>,{" "}
              <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>de.wav</span>,{" "}
              <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>english.m4a</span>). When supplied,
              TTS is skipped for that language and only the listed
              languages are processed. If you also upload a script .docx, its
              matching section is used for captions; otherwise the audio is
              transcribed.
            </p>

            <input
              id="vo-input"
              type="file"
              accept="audio/*"
              multiple
              className="hidden"
              onChange={(e) => {
                const files = Array.from(e.target.files || []);
                const next = files.map((f) => ({ file: f, lang: langFromFilename(f.name) || "" }));
                setVoEntries((cur) => [...cur, ...next]);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              onClick={() => document.getElementById("vo-input")?.click()}
              className="w-full px-3 py-2 text-sm rounded-md"
              style={{ background: "var(--surface-2)", border: "1px dashed var(--border)", color: "var(--text-secondary)", fontFamily: "'JetBrains Mono', monospace" }}
            >
              + add audio files
            </button>

            <div className="flex flex-col gap-2">
              {voEntries.map((entry, i) => {
                const valid = !!entry.lang && !!LANGUAGES[entry.lang];
                return (
                  <div key={i} className="flex gap-2 items-center">
                    <span className="flex-1 text-xs truncate" style={{ color: "var(--text-secondary)", fontFamily: "'JetBrains Mono', monospace" }}>
                      {entry.file.name} · {(entry.file.size / 1024 / 1024).toFixed(1)} MB
                    </span>
                    <select
                      value={entry.lang}
                      onChange={(e) => {
                        const next = [...voEntries];
                        next[i] = { ...next[i], lang: e.target.value };
                        setVoEntries(next);
                      }}
                      className="px-2 py-2 text-sm rounded-md"
                      style={{
                        background: "var(--surface-2)",
                        border: `1px solid ${valid ? "var(--border)" : "var(--error)"}`,
                        color: "var(--text-primary)",
                      }}
                    >
                      <option value="">lang</option>
                      {Object.entries(LANGUAGES).map(([code, name]) => (
                        <option key={code} value={code}>{code.toUpperCase()} — {name}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      onClick={() => setVoEntries(voEntries.filter((_, j) => j !== i))}
                      className="px-2 py-2 text-sm rounded-md"
                      style={{ color: "var(--text-muted)", border: "1px solid var(--border)" }}
                      aria-label="Remove"
                    >
                      ×
                    </button>
                  </div>
                );
              })}
            </div>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setVoModalOpen(false)}
                className="px-4 py-1.5 text-xs rounded-md"
                style={{ background: "var(--accent)", color: "var(--bg)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {/* === Background blogs modal === */}
      {bgModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center px-4"
          style={{ background: "rgba(0,0,0,0.65)" }}
          onClick={() => setBgModalOpen(false)}
        >
          <div
            className="w-full max-w-2xl rounded-2xl p-6 space-y-4 animate-in"
            style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3
                className="text-sm font-medium uppercase tracking-[0.15em]"
                style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-secondary)" }}
              >
                Background blogs (per language)
              </h3>
              <button onClick={() => setBgModalOpen(false)} className="text-xl leading-none" style={{ color: "var(--text-muted)" }}>×</button>
            </div>
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              Each entry replaces the visual background for that language's dub
              with a screen-record of the URL, scrolled to match the dialogue.
            </p>

            <div className="flex flex-col gap-2">
              {bgEntries.map((entry, i) => (
                <div key={i} className="flex gap-2 items-center">
                  <input
                    type="url"
                    placeholder="https://example.com/blog-post"
                    value={entry.url}
                    onChange={(e) => {
                      const next = [...bgEntries];
                      next[i] = { ...next[i], url: e.target.value };
                      setBgEntries(next);
                    }}
                    className="flex-1 px-3 py-2 text-sm rounded-md"
                    style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  />
                  <select
                    value={entry.lang}
                    onChange={(e) => {
                      const next = [...bgEntries];
                      next[i] = { ...next[i], lang: e.target.value };
                      setBgEntries(next);
                    }}
                    className="px-2 py-2 text-sm rounded-md"
                    style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  >
                    <option value="">lang</option>
                    {Object.entries(LANGUAGES).map(([code, name]) => (
                      <option key={code} value={code}>{code.toUpperCase()} — {name}</option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => setBgEntries(bgEntries.filter((_, j) => j !== i))}
                    className="px-2 py-2 text-sm rounded-md"
                    style={{ color: "var(--text-muted)", border: "1px solid var(--border)" }}
                    aria-label="Remove"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setBgEntries([...bgEntries, { url: "", lang: "" }])}
                className="px-3 py-1.5 text-xs rounded-md flex items-center gap-1"
                style={{ background: "var(--surface-2)", border: "1px dashed var(--border)", color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                <span style={{ fontSize: 14, lineHeight: 1 }}>+</span> add background
              </button>
              <div className="flex-1" />
              <button
                type="button"
                onClick={() => setBgModalOpen(false)}
                className="px-4 py-1.5 text-xs rounded-md"
                style={{ background: "var(--accent)", color: "var(--bg)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
