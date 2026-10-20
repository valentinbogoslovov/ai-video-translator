"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Clip } from "./EditPreview";

type Props = {
  clips: Clip[];
  duration: number;
  playhead: number;
  selectedClipId: string | null;
  onSeek: (t: number) => void;
  onSelect: (id: string | null) => void;
  onMutate: (id: string, patch: Partial<Clip>) => void;
};

const ROW_PX = 40;
const RULER_PX = 18;
const EDGE_HOT_PX = 6;
const SNAP_PX = 8;
const MIN_SOURCE_LEN = 0.05;

function fmt(t: number): string {
  const s = Math.floor(t);
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
}

function tickStep(durationS: number): number {
  if (durationS <= 10) return 1;
  if (durationS <= 30) return 2;
  if (durationS <= 90) return 5;
  if (durationS <= 300) return 10;
  return 30;
}

const TRACK_LABEL: Record<Clip["track"], string> = {
  video: "VIDEO",
  voice: "VOICE",
  music: "MUSIC",
};

const TRACK_COLOR: Record<Clip["track"], string> = {
  video: "#5b8def",
  voice: "#3ed598",
  music: "#f5a623",
};

type DragMode = "move" | "trim-l" | "trim-r";

interface DragState {
  mode: DragMode;
  clipId: string;
  track: Clip["track"];
  startMouseX: number;
  startTimelineStart: number;
  startSourceStart: number;
  startSourceEnd: number;
  speed: number;
  pxPerSec: number;
  rowLeft: number; // viewport-x of the row's left edge (for snap math)
}

function clipPlayedDur(c: { sourceStart: number; sourceEnd: number; speed: number }): number {
  const len = c.sourceEnd - c.sourceStart;
  return c.speed > 0 ? len / c.speed : len;
}

export default function EditTimeline({
  clips, duration, playhead, selectedClipId, onSeek, onSelect, onMutate,
}: Props) {
  const tracks: Clip["track"][] = ["video", "voice", "music"];
  const byTrack = tracks.map((t) => clips.filter((c) => c.track === t));

  const tracksWrapRef = useRef<HTMLDivElement | null>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  // Snapshot the moving clip ID separately so we can suppress a click-
  // to-seek that would otherwise fire when the user mouses-up after
  // a drag (mousedown on a clip → mouseup also bubbles).
  const dragStartedRef = useRef(false);

  /** Convert a clip's track + the ongoing drag into other-clip edge
   *  positions (in seconds) on the same track for snapping. */
  const snapTargetsForTrack = useCallback((trackKind: Clip["track"], excludeId: string): number[] => {
    const out: number[] = [0, duration];
    for (const c of clips) {
      if (c.track !== trackKind || c.id === excludeId) continue;
      out.push(c.timelineStart);
      out.push(c.timelineStart + clipPlayedDur(c));
    }
    return out;
  }, [clips, duration]);

  const startDrag = useCallback((
    e: React.MouseEvent<HTMLDivElement>,
    c: Clip,
    mode: DragMode,
    rowEl: HTMLDivElement,
  ) => {
    e.stopPropagation();
    e.preventDefault();
    const rowRect = rowEl.getBoundingClientRect();
    setDrag({
      mode,
      clipId: c.id,
      track: c.track,
      startMouseX: e.clientX,
      startTimelineStart: c.timelineStart,
      startSourceStart: c.sourceStart,
      startSourceEnd: c.sourceEnd,
      speed: c.speed,
      pxPerSec: rowRect.width / Math.max(0.001, duration),
      rowLeft: rowRect.left,
    });
    dragStartedRef.current = true;
    onSelect(c.id);
  }, [duration, onSelect]);

  // Global mouse listeners for the active drag.
  useEffect(() => {
    if (!drag) return;
    const onMove = (e: MouseEvent) => {
      const deltaPx = e.clientX - drag.startMouseX;
      const deltaSec = deltaPx / drag.pxPerSec;
      const targets = snapTargetsForTrack(drag.track, drag.clipId);

      if (drag.mode === "move") {
        const playedDur = (drag.startSourceEnd - drag.startSourceStart) / Math.max(0.01, drag.speed);
        let newStart = drag.startTimelineStart + deltaSec;
        // snap left edge
        const candL = newStart;
        const snapL = targets.find((t) => Math.abs(t - candL) * drag.pxPerSec < SNAP_PX);
        if (snapL !== undefined) newStart = snapL;
        else {
          // also try snapping the right edge
          const candR = newStart + playedDur;
          const snapR = targets.find((t) => Math.abs(t - candR) * drag.pxPerSec < SNAP_PX);
          if (snapR !== undefined) newStart = snapR - playedDur;
        }
        newStart = Math.max(0, Math.min(duration - playedDur, newStart));
        onMutate(drag.clipId, { timelineStart: newStart });
      } else if (drag.mode === "trim-r") {
        // Right edge trim: changes sourceEnd. The on-timeline right edge
        // is timelineStart + (newSourceEnd - sourceStart)/speed.
        let newEnd = drag.startSourceEnd + deltaSec * drag.speed;
        newEnd = Math.max(drag.startSourceStart + MIN_SOURCE_LEN, newEnd);
        // Snap timeline right edge to neighbour edges.
        const tlRight = drag.startTimelineStart + (newEnd - drag.startSourceStart) / Math.max(0.01, drag.speed);
        const snap = targets.find((t) => Math.abs(t - tlRight) * drag.pxPerSec < SNAP_PX);
        if (snap !== undefined) {
          newEnd = drag.startSourceStart + (snap - drag.startTimelineStart) * drag.speed;
          newEnd = Math.max(drag.startSourceStart + MIN_SOURCE_LEN, newEnd);
        }
        // Cap to whole-timeline duration so the clip can't extend past the end.
        const maxEndOnSrc = drag.startSourceStart + (duration - drag.startTimelineStart) * drag.speed;
        if (newEnd > maxEndOnSrc) newEnd = maxEndOnSrc;
        onMutate(drag.clipId, { sourceEnd: newEnd });
      } else if (drag.mode === "trim-l") {
        // Left edge trim: changes sourceStart, AND timelineStart shifts
        // by the same amount in timeline units so the right edge stays put.
        let newSourceStart = drag.startSourceStart + deltaSec * drag.speed;
        newSourceStart = Math.max(0, Math.min(drag.startSourceEnd - MIN_SOURCE_LEN, newSourceStart));
        let newTimelineStart = drag.startTimelineStart + (newSourceStart - drag.startSourceStart) / Math.max(0.01, drag.speed);
        // Snap left timeline edge to neighbour edges.
        const snap = targets.find((t) => Math.abs(t - newTimelineStart) * drag.pxPerSec < SNAP_PX);
        if (snap !== undefined) {
          const snapShift = snap - newTimelineStart;
          newTimelineStart = snap;
          newSourceStart = Math.max(0, Math.min(drag.startSourceEnd - MIN_SOURCE_LEN, newSourceStart + snapShift * drag.speed));
        }
        // Don't allow the left edge to cross t=0.
        if (newTimelineStart < 0) {
          const shift = -newTimelineStart;
          newTimelineStart = 0;
          newSourceStart = Math.max(0, Math.min(drag.startSourceEnd - MIN_SOURCE_LEN, newSourceStart + shift * drag.speed));
        }
        onMutate(drag.clipId, { sourceStart: newSourceStart, timelineStart: newTimelineStart });
      }
    };
    const onUp = () => setDrag(null);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [drag, duration, onMutate, snapTargetsForTrack]);

  const handleClickOnRail = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    // If the user just finished a drag, that mouseup fires a click on
    // the row — swallow it so we don't seek to wherever the drag ended.
    if (dragStartedRef.current) {
      dragStartedRef.current = false;
      return;
    }
    const rail = e.currentTarget;
    const rect = rail.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = (x / rect.width) * duration;
    onSeek(Math.max(0, Math.min(duration, t)));
    onSelect(null);
  }, [duration, onSeek, onSelect]);

  const tStep = tickStep(duration);
  const ticks: number[] = [];
  for (let t = 0; t <= duration + 1e-6; t += tStep) ticks.push(t);

  return (
    <div className="rounded-xl" style={{ background: "var(--surface-2)", border: "1px solid var(--border)", overflow: "hidden" }}>
      {/* Ruler */}
      <div
        onClick={handleClickOnRail}
        className="relative cursor-pointer"
        style={{ height: RULER_PX, background: "var(--bg-card)", borderBottom: "1px solid var(--border)" }}
      >
        {ticks.map((t) => (
          <div
            key={t}
            className="absolute top-0 bottom-0 flex items-center"
            style={{ left: `${(t / duration) * 100}%`, transform: "translateX(-1px)" }}
          >
            <div style={{ width: 1, height: 8, background: "var(--border)" }} />
            <span className="ml-1 text-[10px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
              {fmt(t)}
            </span>
          </div>
        ))}
        <div
          className="absolute top-0 bottom-0 pointer-events-none"
          style={{ left: `${(playhead / duration) * 100}%`, width: 2, background: "var(--accent)", transform: "translateX(-1px)" }}
        />
      </div>

      {/* Tracks */}
      <div ref={tracksWrapRef} className="relative">
        {tracks.map((tk, idx) => (
          <TrackRow
            key={tk}
            tk={tk}
            isLast={idx === tracks.length - 1}
            stripe={idx % 2 === 1}
            clips={byTrack[idx]}
            duration={duration}
            selectedClipId={selectedClipId}
            onClickRow={handleClickOnRail}
            onClipMouseDown={startDrag}
          />
        ))}
        <div
          className="absolute top-0 bottom-0 pointer-events-none"
          style={{ left: `${(playhead / duration) * 100}%`, width: 2, background: "var(--accent)", transform: "translateX(-1px)" }}
        />
      </div>
    </div>
  );
}

function TrackRow({
  tk, isLast, stripe, clips, duration, selectedClipId,
  onClickRow, onClipMouseDown,
}: {
  tk: Clip["track"];
  isLast: boolean;
  stripe: boolean;
  clips: Clip[];
  duration: number;
  selectedClipId: string | null;
  onClickRow: (e: React.MouseEvent<HTMLDivElement>) => void;
  onClipMouseDown: (e: React.MouseEvent<HTMLDivElement>, c: Clip, mode: DragMode, rowEl: HTMLDivElement) => void;
}) {
  const rowRef = useRef<HTMLDivElement | null>(null);
  return (
    <div
      ref={rowRef}
      className="relative cursor-pointer"
      onClick={onClickRow}
      style={{
        height: ROW_PX,
        borderBottom: !isLast ? "1px solid var(--border)" : "none",
        background: stripe ? "rgba(255,255,255,0.02)" : "transparent",
      }}
    >
      <div
        className="absolute top-0 bottom-0 left-0 flex items-center px-2 pointer-events-none z-10"
        style={{ width: 56, fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: "var(--text-muted)", letterSpacing: "0.15em", background: "linear-gradient(to right, var(--bg-card) 80%, transparent)" }}
      >
        {TRACK_LABEL[tk]}
      </div>
      {clips.map((c) => {
        const playedDur = clipPlayedDur(c);
        const left = (c.timelineStart / duration) * 100;
        const width = (playedDur / duration) * 100;
        const isSelected = c.id === selectedClipId;
        return (
          <div
            key={c.id}
            title={c.text || c.id}
            onMouseDown={(e) => {
              const row = rowRef.current;
              if (!row) return;
              const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
              const localX = e.clientX - rect.left;
              const widthPx = rect.width;
              let mode: DragMode = "move";
              if (localX <= EDGE_HOT_PX) mode = "trim-l";
              else if (widthPx - localX <= EDGE_HOT_PX) mode = "trim-r";
              onClipMouseDown(e, c, mode, row);
            }}
            onClick={(e) => e.stopPropagation()}
            className="absolute rounded-md overflow-hidden flex items-center px-2 truncate text-[10px]"
            style={{
              top: 4,
              bottom: 4,
              left: `${left}%`,
              width: `${Math.max(0.5, width)}%`,
              background: TRACK_COLOR[tk],
              color: "#0a0a0a",
              fontFamily: "'JetBrains Mono', monospace",
              fontWeight: 600,
              boxShadow: isSelected
                ? "inset 0 0 0 2px var(--accent), 0 0 0 1px rgba(0,0,0,0.4)"
                : "inset 0 0 0 1px rgba(0,0,0,0.25)",
              cursor: "grab",
              userSelect: "none",
            }}
          >
            <span className="truncate flex-1 pointer-events-none">{c.text || c.id}</span>
            {/* Edge hot zones (visual only — actual mode resolved on mousedown) */}
            <div className="absolute left-0 top-0 bottom-0" style={{ width: EDGE_HOT_PX, cursor: "ew-resize" }} />
            <div className="absolute right-0 top-0 bottom-0" style={{ width: EDGE_HOT_PX, cursor: "ew-resize" }} />
          </div>
        );
      })}
    </div>
  );
}
