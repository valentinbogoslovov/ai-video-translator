"use client";

import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import EditPreview, { Clip, PreviewControl } from "./EditPreview";
import EditTimeline from "./EditTimeline";
import CaptionEditor from "./CaptionEditor";
import CaptionStylePanel from "./CaptionStylePanel";
import type { CaptionEvent } from "../../CaptionPreview";

const API = "http://localhost:8000";

type EDL = {
  job_id: string;
  language: string;
  duration: number;
  video_width: number;
  video_height: number;
  clips: Clip[];
  captions: CaptionEvent[];
  caption_box: { x: number; y: number; scale: number; rotation: number };
  caption_style: Record<string, unknown>;
};

type EditLanguageList = {
  job_id: string;
  languages_done: string[];
};

function fmt(t: number): string {
  const s = Math.max(0, t);
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  const ms = Math.floor((s - Math.floor(s)) * 100);
  return `${m}:${sec.toString().padStart(2, "0")}.${ms.toString().padStart(2, "0")}`;
}

export default function EditPage({ params }: { params: Promise<{ job_id: string }> }) {
  const { job_id: jobId } = use(params);

  const [langList, setLangList] = useState<EditLanguageList | null>(null);
  const [language, setLanguage] = useState<string | null>(null);
  const [edl, setEdl] = useState<EDL | null>(null);
  // The fetched EDL is the immutable "original"; `clips`/`captions` are
  // the user's working copy. Reset restores from `edl`.
  const [clips, setClips] = useState<Clip[]>([]);
  const [captions, setCaptions] = useState<CaptionEvent[]>([]);
  const [captionBox, setCaptionBox] = useState<{ x: number; y: number; scale: number; rotation: number }>({ x: 50, y: 85, scale: 60, rotation: 0 });
  const [captionStyle, setCaptionStyle] = useState<Record<string, unknown>>({});
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [isDirty, setIsDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  // Render flow.
  const [isRendering, setIsRendering] = useState(false);
  const [renderUrl, setRenderUrl] = useState<string | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [isRegrouping, setIsRegrouping] = useState(false);

  const previewRef = useRef<PreviewControl | null>(null);
  // True after the working copy has been initialised at least once for
  // the current EDL; lets the "pause-on-edit" effect skip the initial
  // setClips fired by the EDL load.
  const workingCopyReadyRef = useRef(false);

  // Load language list from disk (survives backend restarts, unlike
  // /api/status which only knows about jobs still in the in-memory dict).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API}/api/edit/${jobId}`);
        if (!res.ok) throw new Error(`status ${res.status}`);
        const j: EditLanguageList = await res.json();
        if (cancelled) return;
        setLangList(j);
        if (!language && j.languages_done && j.languages_done.length > 0) {
          setLanguage(j.languages_done[0]);
        }
      } catch (e) {
        if (!cancelled) setError(`Failed to load job: ${(e as Error).message}`);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // Load /api/edit when the language changes.
  useEffect(() => {
    if (!language) return;
    let cancelled = false;
    setError(null);
    setEdl(null);
    setClips([]);
    setCaptions([]);
    setSelectedClipId(null);
    setIsDirty(false);
    setRenderUrl(null);
    setRenderError(null);
    setPlayhead(0);
    setIsPlaying(false);
    workingCopyReadyRef.current = false;
    (async () => {
      try {
        const res = await fetch(`${API}/api/edit/${jobId}/${language}`);
        if (!res.ok) {
          const body = await res.text();
          throw new Error(body || `${res.status}`);
        }
        const j: EDL = await res.json();
        if (cancelled) return;
        setEdl(j);
        setClips(j.clips);
        setCaptions(j.captions);
        setCaptionBox(j.caption_box);
        setCaptionStyle(j.caption_style || {});
        // Mark working copy ready on the next tick so the
        // "pause-on-edit" effect doesn't fire on this initial seed.
        setTimeout(() => { workingCopyReadyRef.current = true; }, 0);
      } catch (e) {
        if (!cancelled) setError(`Failed to load EDL: ${(e as Error).message}`);
      }
    })();
    return () => { cancelled = true; };
  }, [jobId, language]);

  // Whenever the user mutates the clip list while playing, pause —
  // the audio scheduler can't gracefully reflow already-scheduled
  // BufferSourceNodes mid-playback, so we let the user re-press Play.
  // Caption / style / box edits only repaint the libass overlay and
  // don't need a pause.
  useEffect(() => {
    if (!workingCopyReadyRef.current) return;
    if (isPlaying) previewRef.current?.pause();
    setRenderUrl(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clips]);
  useEffect(() => {
    if (!workingCopyReadyRef.current) return;
    setRenderUrl(null);
  }, [captions, captionBox, captionStyle]);

  const mutateClip = useCallback((id: string, patch: Partial<Clip>) => {
    setClips((prev) => prev.map((c) => (c.id === id ? { ...c, ...patch } : c)));
    setIsDirty(true);
  }, []);
  const removeClip = useCallback((id: string) => {
    setClips((prev) => prev.filter((c) => c.id !== id));
    setSelectedClipId((cur) => (cur === id ? null : cur));
    setIsDirty(true);
  }, []);
  const addClip = useCallback((c: Clip) => {
    setClips((prev) => [...prev, c]);
    setIsDirty(true);
  }, []);
  const resetEdits = useCallback(() => {
    if (!edl) return;
    setClips(edl.clips);
    setCaptions(edl.captions);
    setCaptionBox(edl.caption_box);
    setCaptionStyle(edl.caption_style || {});
    setSelectedClipId(null);
    setIsDirty(false);
    setRenderUrl(null);
    setRenderError(null);
  }, [edl]);
  // Caption mutators — same is-dirty / clear-render-url discipline as
  // the clip mutators so the user sees immediate feedback.
  const mutateCaption = useCallback((idx: number, patch: Partial<CaptionEvent>) => {
    setCaptions((prev) => prev.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
    setIsDirty(true);
  }, []);
  const removeCaption = useCallback((idx: number) => {
    setCaptions((prev) => prev.filter((_, i) => i !== idx));
    setIsDirty(true);
  }, []);
  const insertCaptionAfter = useCallback((idx: number, atTime: number) => {
    setCaptions((prev) => {
      const insertion: CaptionEvent = {
        start: atTime,
        end: atTime + 1.0,
        text: "",
      };
      const out = [...prev];
      // -1 means "insert at the start". idx >=0 means after that idx.
      const pos = idx < 0 ? 0 : idx + 1;
      out.splice(pos, 0, insertion);
      return out;
    });
    setIsDirty(true);
  }, []);
  const shiftAllCaptions = useCallback((deltaSec: number) => {
    setCaptions((prev) => prev.map((c) => ({
      ...c,
      start: Math.max(0, c.start + deltaSec),
      end: Math.max(0, c.end + deltaSec),
    })));
    setIsDirty(true);
  }, []);
  const updateCaptionBox = useCallback((patch: Partial<{ x: number; y: number; scale: number; rotation: number }>) => {
    setCaptionBox((prev) => ({ ...prev, ...patch }));
    setIsDirty(true);
  }, []);
  const updateCaptionStyle = useCallback((patch: Record<string, unknown>) => {
    setCaptionStyle((prev) => ({ ...prev, ...patch }));
    setIsDirty(true);
  }, []);
  /** Server-side re-derivation of caption events from the original
   *  word-timed segments using the current grouping options. Discards
   *  any manual start/end/text edits the user made to individual
   *  events — the user invoked the action explicitly, so that's fine. */
  const regroupCaptions = useCallback(async () => {
    if (!edl || !language) return;
    setIsRegrouping(true);
    setRenderError(null);
    try {
      const res = await fetch(`${API}/api/edit/${jobId}/${language}/captions/regroup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ caption_box: captionBox, caption_style: captionStyle }),
      });
      if (!res.ok) throw new Error(await res.text());
      const j: { captions: CaptionEvent[] } = await res.json();
      setCaptions(j.captions);
      setIsDirty(true);
    } catch (e) {
      setRenderError(`Re-group failed: ${(e as Error).message}`);
    } finally {
      setIsRegrouping(false);
    }
  }, [edl, language, jobId, captionBox, captionStyle]);
  /** Upload a media file for a given track and add it as a new clip
   *  at the current playhead. Probes the file's duration client-side
   *  before committing so the new clip has the right `sourceEnd`. */
  const uploadAndAdd = useCallback(async (file: File, trackKind: "voice" | "music") => {
    if (!language) return;
    const fd = new FormData();
    fd.append("file", file);
    let src: string;
    try {
      const res = await fetch(`${API}/api/edit/${jobId}/${language}/upload`, {
        method: "POST", body: fd,
      });
      if (!res.ok) throw new Error(await res.text());
      const j: { src: string } = await res.json();
      src = j.src;
    } catch (e) {
      setRenderError(`Upload failed: ${(e as Error).message}`);
      return;
    }
    // Probe duration via a temporary audio element so the new clip's
    // sourceEnd matches the file's real length.
    const blobUrl = URL.createObjectURL(file);
    const dur = await new Promise<number>((resolve) => {
      const a = document.createElement("audio");
      a.preload = "metadata";
      a.src = blobUrl;
      a.onloadedmetadata = () => resolve(isFinite(a.duration) ? a.duration : 0);
      a.onerror = () => resolve(0);
    });
    URL.revokeObjectURL(blobUrl);
    if (dur <= 0) {
      setRenderError("Couldn't read uploaded file's duration.");
      return;
    }
    const newId = `${trackKind}-up-${Date.now().toString(36)}`;
    addClip({
      id: newId,
      track: trackKind,
      src,
      sourceStart: 0,
      sourceEnd: dur,
      timelineStart: playhead,
      speed: 1,
      text: file.name,
    });
    setSelectedClipId(newId);
  }, [language, jobId, playhead, addClip]);

  const voiceUploadInputRef = useRef<HTMLInputElement | null>(null);
  const musicUploadInputRef = useRef<HTMLInputElement | null>(null);

  const exportEdited = useCallback(async () => {
    if (!edl || !language) return;
    setIsRendering(true);
    setRenderError(null);
    setRenderUrl(null);
    try {
      const body = {
        clips,
        captions,
        caption_box: captionBox,
        caption_style: captionStyle,
      };
      const res = await fetch(`${API}/api/render/${jobId}/${language}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || `render ${res.status}`);
      }
      const j: { output_url: string } = await res.json();
      setRenderUrl(j.output_url);
    } catch (e) {
      setRenderError((e as Error).message);
    } finally {
      setIsRendering(false);
    }
  }, [edl, language, jobId, clips, captions]);

  const togglePlay = useCallback(() => {
    if (!previewRef.current) return;
    if (isPlaying) previewRef.current.pause();
    else void previewRef.current.play();
  }, [isPlaying]);

  const onSeek = useCallback((t: number) => {
    if (!previewRef.current) return;
    previewRef.current.seek(t);
  }, []);

  // Keyboard shortcuts. Space = play/pause, Delete/Backspace = remove
  // selected clip. Both ignore form fields and content-editable elements.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (document.activeElement?.tagName || "").toLowerCase();
      const editable = tag === "input" || tag === "textarea" || tag === "select"
        || (document.activeElement as HTMLElement | null)?.isContentEditable;
      if (editable) return;
      if (e.key === " " || e.code === "Space") {
        e.preventDefault();
        togglePlay();
      } else if ((e.key === "Delete" || e.key === "Backspace") && selectedClipId) {
        e.preventDefault();
        removeClip(selectedClipId);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePlay, selectedClipId, removeClip]);

  const langOptions = useMemo(() => langList?.languages_done ?? [], [langList]);

  return (
    <main className="min-h-screen p-6" style={{ background: "var(--bg)", color: "var(--text)" }}>
      <div className="max-w-6xl mx-auto space-y-4">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold" style={{ fontFamily: "'JetBrains Mono', monospace" }}>EDIT</h1>
            <div className="text-[11px]" style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}>
              job <span style={{ color: "var(--accent)" }}>{jobId.slice(0, 8)}</span>
              {language && <> · lang <span style={{ color: "var(--accent)" }}>{language.toUpperCase()}</span></>}
            </div>
          </div>
          <a
            href="/"
            className="text-[11px] uppercase tracking-[0.15em] underline"
            style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}
          >
            ← back
          </a>
        </header>

        {/* Language picker */}
        {langOptions.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>language</span>
            <div className="flex gap-1 flex-wrap">
              {langOptions.map((l) => (
                <button
                  key={l}
                  onClick={() => setLanguage(l)}
                  className="px-3 py-1.5 rounded-md text-[11px] font-medium uppercase tracking-[0.1em]"
                  style={{
                    background: l === language ? "var(--accent)" : "var(--bg-card)",
                    color: l === language ? "var(--bg)" : "var(--text-secondary)",
                    border: "1px solid var(--border)",
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                >
                  {l}
                </button>
              ))}
            </div>
          </div>
        )}

        {error && (
          <div className="text-[12px] p-3 rounded-md" style={{ background: "rgba(255,80,80,0.08)", color: "#ff8080", border: "1px solid rgba(255,80,80,0.2)" }}>
            {error}
          </div>
        )}

        {edl && (
          <>
            {/* Preview */}
            <EditPreview
              ref={previewRef}
              jobId={jobId}
              language={edl.language}
              clips={clips}
              captions={captions}
              duration={edl.duration}
              videoWidth={edl.video_width}
              videoHeight={edl.video_height}
              captionBox={captionBox}
              captionStyle={captionStyle}
              apiBase={API}
              onTimeUpdate={setPlayhead}
              onPlayingChange={setIsPlaying}
            />

            {/* Transport */}
            <div className="flex items-center gap-3 px-1">
              <button
                onClick={togglePlay}
                className="w-9 h-9 rounded-md flex items-center justify-center"
                style={{ background: "var(--accent)", color: "var(--bg)" }}
                aria-label={isPlaying ? "Pause" : "Play"}
              >
                {isPlaying ? (
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z" /></svg>
                ) : (
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
                )}
              </button>
              <div className="text-[12px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
                <span style={{ color: "var(--accent)" }}>{fmt(playhead)}</span>
                <span> / {fmt(edl.duration)}</span>
              </div>
              <div className="ml-auto flex items-center gap-2">
                <button
                  type="button"
                  onClick={resetEdits}
                  disabled={!isDirty}
                  className="px-3 py-1.5 rounded-md text-[11px] uppercase tracking-[0.1em]"
                  style={{
                    background: "var(--bg-card)",
                    color: isDirty ? "var(--text-secondary)" : "var(--text-muted)",
                    border: "1px solid var(--border)",
                    fontFamily: "'JetBrains Mono', monospace",
                    opacity: isDirty ? 1 : 0.55,
                    cursor: isDirty ? "pointer" : "not-allowed",
                  }}
                >
                  Reset
                </button>
                <button
                  type="button"
                  onClick={exportEdited}
                  disabled={isRendering}
                  className="px-3 py-1.5 rounded-md text-[11px] uppercase tracking-[0.1em] font-medium"
                  style={{
                    background: "var(--accent)",
                    color: "var(--bg)",
                    fontFamily: "'JetBrains Mono', monospace",
                    opacity: isRendering ? 0.6 : 1,
                    cursor: isRendering ? "wait" : "pointer",
                  }}
                >
                  {isRendering ? "Rendering…" : "Export"}
                </button>
              </div>
            </div>

            <div className="text-[10px] uppercase tracking-[0.15em] px-1" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
              space = play/pause · click ruler to seek · drag clip = move · drag clip edge = trim · delete = remove selected
            </div>

            {/* Per-track upload row */}
            <div className="flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>add clip</span>
              <input
                ref={voiceUploadInputRef}
                type="file" accept="audio/*" className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void uploadAndAdd(f, "voice");
                  e.target.value = "";
                }}
              />
              <button
                type="button"
                onClick={() => voiceUploadInputRef.current?.click()}
                className="px-3 py-1.5 rounded-md text-[11px] uppercase tracking-[0.1em]"
                style={{ background: "var(--bg-card)", color: "#3ed598", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                + voice
              </button>
              <input
                ref={musicUploadInputRef}
                type="file" accept="audio/*" className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void uploadAndAdd(f, "music");
                  e.target.value = "";
                }}
              />
              <button
                type="button"
                onClick={() => musicUploadInputRef.current?.click()}
                className="px-3 py-1.5 rounded-md text-[11px] uppercase tracking-[0.1em]"
                style={{ background: "var(--bg-card)", color: "#f5a623", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                + music
              </button>
              <span className="text-[10px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
                (added at playhead)
              </span>
            </div>

            {/* Timeline */}
            <EditTimeline
              clips={clips}
              duration={edl.duration}
              playhead={playhead}
              selectedClipId={selectedClipId}
              onSeek={onSeek}
              onSelect={setSelectedClipId}
              onMutate={mutateClip}
            />

            {/* Caption events editor */}
            <CaptionEditor
              captions={captions}
              duration={edl.duration}
              playhead={playhead}
              onMutate={mutateCaption}
              onRemove={removeCaption}
              onInsertAfter={insertCaptionAfter}
              onShiftAll={shiftAllCaptions}
              onRegroup={regroupCaptions}
              isRegrouping={isRegrouping}
            />

            {/* Caption style + box panel */}
            <CaptionStylePanel
              captionBox={captionBox}
              captionStyle={captionStyle}
              onBoxChange={updateCaptionBox}
              onStyleChange={updateCaptionStyle}
            />

            {/* Render result + summary */}
            {renderError && (
              <div className="text-[11px] p-2 rounded-md" style={{ background: "rgba(255,80,80,0.08)", color: "#ff8080", border: "1px solid rgba(255,80,80,0.2)", fontFamily: "'JetBrains Mono', monospace" }}>
                Render failed: {renderError.slice(0, 240)}
              </div>
            )}
            {renderUrl && (
              <a
                href={`${API}${renderUrl}`}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-[11px]"
                style={{ background: "var(--accent)", color: "var(--bg)", fontFamily: "'JetBrains Mono', monospace" }}
              >
                Download edited mp4
              </a>
            )}
            <div className="text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
              {clips.length} clips · {captions.length} caption events · {edl.video_width}×{edl.video_height}
              {isDirty && <span style={{ color: "var(--accent)" }}> · unsaved edits</span>}
            </div>
          </>
        )}

        {!edl && !error && language && (
          <div className="text-[12px]" style={{ color: "var(--text-muted)" }}>Loading EDL…</div>
        )}
        {langList && langOptions.length === 0 && (
          <div className="text-[12px]" style={{ color: "var(--text-muted)" }}>
            No completed languages on this job yet.
          </div>
        )}
      </div>
    </main>
  );
}
