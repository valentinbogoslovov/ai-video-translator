"use client";

import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState,
} from "react";
import CaptionPreview, { CaptionEvent } from "../../CaptionPreview";

export type Clip = {
  id: string;
  track: "video" | "voice" | "music";
  src: string;
  sourceStart: number;
  sourceEnd: number;
  timelineStart: number;
  speed: number;
  text?: string;
  speaker?: string;
};

export type PreviewControl = {
  play: () => Promise<void>;
  pause: () => void;
  seek: (t: number) => void;
};

type Props = {
  jobId: string;
  language: string;
  clips: Clip[];
  captions: CaptionEvent[];
  duration: number;
  videoWidth: number;
  videoHeight: number;
  captionBox: { x: number; y: number; scale: number; rotation: number };
  captionStyle: Record<string, unknown>;
  apiBase: string;
  onTimeUpdate: (t: number) => void;
  onPlayingChange: (p: boolean) => void;
};

/**
 * Resolve a clip's `src` (relative to {jobId}/{language}/) to an absolute
 * /api/asset URL. Mirrors the backend's normpath resolution: `..`
 * components walk up out of the language dir.
 */
function clipAssetUrl(jobId: string, language: string, src: string, apiBase: string): string {
  const parts = `${language}/${src}`.split("/");
  const stack: string[] = [];
  for (const p of parts) {
    if (p === "..") stack.pop();
    else if (p === "." || p === "") continue;
    else stack.push(p);
  }
  return `${apiBase}/api/asset/${jobId}/${stack.join("/")}`;
}

const EditPreview = forwardRef<PreviewControl, Props>(function EditPreview(props, ref) {
  const {
    jobId, language, clips, captions, duration,
    videoWidth, videoHeight, captionBox, captionStyle, apiBase,
    onTimeUpdate, onPlayingChange,
  } = props;

  const videoEl = clips.find((c) => c.track === "video");
  const audioClips = clips.filter((c) => c.track === "voice" || c.track === "music");

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [videoNode, setVideoNode] = useState<HTMLVideoElement | null>(null);
  const setVideoRef = useCallback((el: HTMLVideoElement | null) => {
    videoRef.current = el;
    setVideoNode(el);
  }, []);

  // WebAudio plumbing. AudioContext is created lazily on the first
  // play() call (browser autoplay policy requires a user gesture). All
  // decoded buffers are cached by URL so seeking and re-play don't
  // re-fetch.
  const audioCtxRef = useRef<AudioContext | null>(null);
  const bufferCacheRef = useRef<Map<string, AudioBuffer>>(new Map());
  const sourcesRef = useRef<AudioBufferSourceNode[]>([]);
  // Master clock anchors. When playing, playhead = playStartPlayhead +
  // (audioCtx.currentTime - playStartCtxTime).
  const playStartCtxTimeRef = useRef(0);
  const playStartPlayheadRef = useRef(0);
  const isPlayingRef = useRef(false);
  const rafRef = useRef<number | null>(null);
  // Where we are when paused (between stop and next play).
  const pausedPlayheadRef = useRef(0);

  /** Decode a remote audio file into an AudioBuffer (cached). */
  const loadBuffer = useCallback(async (url: string): Promise<AudioBuffer> => {
    const cache = bufferCacheRef.current;
    const hit = cache.get(url);
    if (hit) return hit;
    if (!audioCtxRef.current) throw new Error("AudioContext not ready");
    const res = await fetch(url);
    if (!res.ok) throw new Error(`fetch ${url} → ${res.status}`);
    const ab = await res.arrayBuffer();
    const decoded = await audioCtxRef.current.decodeAudioData(ab);
    cache.set(url, decoded);
    return decoded;
  }, []);

  const stopAllSources = useCallback(() => {
    for (const s of sourcesRef.current) {
      try { s.stop(); } catch { /* not started yet */ }
      try { s.disconnect(); } catch { /* already detached */ }
    }
    sourcesRef.current = [];
  }, []);

  const stopRaf = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  /**
   * Schedule every audio clip on the AudioContext timeline relative to
   * the current playhead. Clips entirely behind the playhead are
   * skipped; clips overlapping it start at offset = (playhead - clip.timelineStart).
   */
  const scheduleAll = useCallback(async (fromPlayhead: number) => {
    const ctx = audioCtxRef.current;
    if (!ctx) return;

    const startCtxTime = ctx.currentTime + 0.05; // small lead-in to avoid clicks
    playStartCtxTimeRef.current = startCtxTime;
    playStartPlayheadRef.current = fromPlayhead;

    for (const c of audioClips) {
      const sourceLen = c.sourceEnd - c.sourceStart;
      if (sourceLen <= 0) continue;
      const playedDur = sourceLen / Math.max(0.01, c.speed);
      const clipEndOnTimeline = c.timelineStart + playedDur;
      if (clipEndOnTimeline <= fromPlayhead) continue;

      const url = clipAssetUrl(jobId, language, c.src, apiBase);
      let buffer: AudioBuffer;
      try {
        buffer = await loadBuffer(url);
      } catch (e) {
        // One bad clip shouldn't kill playback — log and move on.
        console.warn(`[edit] failed to load ${c.id} (${url}):`, e);
        continue;
      }

      const node = ctx.createBufferSource();
      node.buffer = buffer;
      if (c.speed !== 1) node.playbackRate.value = c.speed;
      node.connect(ctx.destination);

      let when: number;
      let offset: number;
      let dur: number;
      if (c.timelineStart >= fromPlayhead) {
        when = startCtxTime + (c.timelineStart - fromPlayhead);
        offset = c.sourceStart;
        dur = sourceLen;
      } else {
        const into = fromPlayhead - c.timelineStart; // seconds elapsed inside the clip
        when = startCtxTime;
        offset = c.sourceStart + into * c.speed;
        dur = sourceLen - into * c.speed;
      }
      if (dur <= 0) continue;
      try {
        node.start(when, offset, dur);
        sourcesRef.current.push(node);
      } catch (e) {
        console.warn(`[edit] start() failed for ${c.id}:`, e);
      }
    }
  }, [audioClips, jobId, language, apiBase, loadBuffer]);

  const play = useCallback(async () => {
    if (isPlayingRef.current) return;
    if (!videoRef.current) return;
    if (!audioCtxRef.current) {
      const Ctor = (window as unknown as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext }).AudioContext
        ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) {
        console.warn("[edit] no AudioContext available");
        return;
      }
      audioCtxRef.current = new Ctor();
    }
    if (audioCtxRef.current.state === "suspended") {
      await audioCtxRef.current.resume();
    }

    let playhead = pausedPlayheadRef.current;
    if (playhead >= duration - 0.05) playhead = 0; // wrap from end
    pausedPlayheadRef.current = playhead;

    // Sync video first; then fire off audio scheduling. Video plays
    // muted — its native audio track is the original-language one we
    // don't want to hear during the edit preview.
    const v = videoRef.current;
    v.muted = true;
    try { v.currentTime = playhead; } catch { /* may be NaN if no metadata */ }
    await v.play().catch(() => undefined);

    isPlayingRef.current = true;
    onPlayingChange(true);

    await scheduleAll(playhead);

    // RAF loop reports playhead upward at ~30 Hz so the timeline +
    // transport readout track playback. Also pulls video.currentTime
    // back into line if it drifts past 100ms.
    let lastReport = 0;
    const tick = () => {
      if (!isPlayingRef.current || !audioCtxRef.current) {
        rafRef.current = null;
        return;
      }
      const elapsed = audioCtxRef.current.currentTime - playStartCtxTimeRef.current;
      const ph = playStartPlayheadRef.current + Math.max(0, elapsed);
      pausedPlayheadRef.current = ph;
      if (ph >= duration) {
        // End of timeline: stop and reset for next play.
        pause();
        pausedPlayheadRef.current = duration;
        onTimeUpdate(duration);
        return;
      }
      const now = performance.now();
      if (now - lastReport > 33) {
        onTimeUpdate(ph);
        lastReport = now;
        // Drift correction on the video element.
        if (videoRef.current && Math.abs(videoRef.current.currentTime - ph) > 0.15) {
          try { videoRef.current.currentTime = ph; } catch { /* */ }
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [duration, scheduleAll, onPlayingChange, onTimeUpdate]);

  const pause = useCallback(() => {
    if (!isPlayingRef.current) return;
    isPlayingRef.current = false;
    stopRaf();
    stopAllSources();
    if (videoRef.current) {
      try { videoRef.current.pause(); } catch { /* */ }
    }
    onPlayingChange(false);
  }, [stopAllSources, stopRaf, onPlayingChange]);

  const seek = useCallback((t: number) => {
    const clamped = Math.max(0, Math.min(duration, t));
    pausedPlayheadRef.current = clamped;
    onTimeUpdate(clamped);
    if (videoRef.current) {
      try { videoRef.current.currentTime = clamped; } catch { /* */ }
    }
    // If we were playing, jump-restart audio at the new position.
    if (isPlayingRef.current) {
      stopAllSources();
      // schedule from the new point against the same audioCtx
      void scheduleAll(clamped);
    }
  }, [duration, onTimeUpdate, scheduleAll, stopAllSources]);

  useImperativeHandle(ref, () => ({ play, pause, seek }), [play, pause, seek]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      stopRaf();
      stopAllSources();
      const ctx = audioCtxRef.current;
      audioCtxRef.current = null;
      if (ctx) ctx.close().catch(() => undefined);
    };
  }, [stopAllSources, stopRaf]);

  if (!videoEl) {
    return <div className="text-sm" style={{ color: "var(--text-muted)" }}>No video clip in EDL.</div>;
  }

  const videoUrl = clipAssetUrl(jobId, language, videoEl.src, apiBase);
  const aspect = videoWidth && videoHeight ? `${videoWidth} / ${videoHeight}` : "16 / 9";

  return (
    <div
      className="relative rounded-2xl overflow-hidden select-none"
      style={{ background: "#000", border: "1px solid var(--border)", aspectRatio: aspect, maxHeight: "60vh" }}
    >
      <video
        ref={setVideoRef}
        src={videoUrl}
        muted
        playsInline
        className="absolute inset-0 w-full h-full object-contain"
      />
      <CaptionPreview
        videoEl={videoNode}
        captionBox={captionBox}
        captionStyle={captionStyle}
        apiBase={apiBase}
        captions={captions}
      />
    </div>
  );
});

export default EditPreview;
