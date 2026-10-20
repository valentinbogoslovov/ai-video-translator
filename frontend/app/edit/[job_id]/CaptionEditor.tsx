"use client";

import { useMemo } from "react";
import type { CaptionEvent } from "../../CaptionPreview";

type Props = {
  captions: CaptionEvent[];
  duration: number;
  playhead: number;
  onMutate: (idx: number, patch: Partial<CaptionEvent>) => void;
  onRemove: (idx: number) => void;
  onInsertAfter: (idx: number, atTime: number) => void;
  onShiftAll: (deltaSec: number) => void;
  /** Re-derive caption events from the original word-timed segments
   *  using the current style/box grouping options. Discards manual
   *  edits to start/end/text. */
  onRegroup: () => void;
  isRegrouping?: boolean;
};

function fmtTime(t: number): string {
  // Render with two-decimal seconds so the user can type ms-level
  // adjustments without juggling clock notation.
  if (!isFinite(t) || t < 0) return "0.00";
  return t.toFixed(2);
}

function parseTime(raw: string): number | null {
  const trimmed = (raw || "").trim();
  if (!trimmed) return null;
  // Accept "12.34", "1:12.34", or "0:01:12.34". Anything else → null.
  const parts = trimmed.split(":");
  if (parts.some((p) => p === "" || isNaN(Number(p)))) return null;
  const nums = parts.map(Number);
  let seconds = 0;
  for (const n of nums) seconds = seconds * 60 + n;
  if (!isFinite(seconds) || seconds < 0) return null;
  return seconds;
}

export default function CaptionEditor({
  captions, duration, playhead, onMutate, onRemove, onInsertAfter, onShiftAll,
  onRegroup, isRegrouping,
}: Props) {
  const activeIdx = useMemo(() => {
    // Highlight the event whose [start, end] window covers the playhead.
    return captions.findIndex((c) => playhead >= c.start && playhead < c.end);
  }, [captions, playhead]);

  return (
    <div
      className="rounded-xl"
      style={{ background: "var(--surface-2)", border: "1px solid var(--border)", overflow: "hidden" }}
    >
      <div
        className="flex items-center gap-2 px-3 py-2"
        style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-card)" }}
      >
        <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
          captions
        </span>
        <span className="text-[10px]" style={{ fontFamily: "'JetBrains Mono', monospace", color: "var(--text-muted)" }}>
          {captions.length} events
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={onRegroup}
            disabled={isRegrouping}
            className="px-2 py-1 rounded text-[10px] uppercase tracking-[0.1em]"
            style={{
              background: "var(--bg-card)",
              color: isRegrouping ? "var(--text-muted)" : "var(--text-secondary)",
              border: "1px solid var(--border)",
              fontFamily: "'JetBrains Mono', monospace",
              opacity: isRegrouping ? 0.6 : 1,
              cursor: isRegrouping ? "wait" : "pointer",
            }}
            title="Discard manual caption edits and rebuild lines from the words using the current Words/caption + pause + punctuation options"
          >
            {isRegrouping ? "Re-grouping…" : "Re-group"}
          </button>
          <button
            type="button"
            onClick={() => onShiftAll(-0.1)}
            className="px-2 py-1 rounded text-[10px] uppercase tracking-[0.1em]"
            style={{ background: "var(--bg-card)", color: "var(--text-secondary)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
            title="Shift every caption 100ms earlier"
          >
            −100ms
          </button>
          <button
            type="button"
            onClick={() => onShiftAll(0.1)}
            className="px-2 py-1 rounded text-[10px] uppercase tracking-[0.1em]"
            style={{ background: "var(--bg-card)", color: "var(--text-secondary)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
            title="Shift every caption 100ms later"
          >
            +100ms
          </button>
          <button
            type="button"
            onClick={() => onInsertAfter(captions.length - 1, Math.min(playhead, Math.max(0, duration - 1)))}
            className="px-2 py-1 rounded text-[10px] uppercase tracking-[0.1em]"
            style={{ background: "var(--accent)", color: "var(--bg)", fontFamily: "'JetBrains Mono', monospace" }}
            title="Add a new caption event at the playhead"
          >
            + add at playhead
          </button>
        </div>
      </div>

      {captions.length === 0 ? (
        <div className="px-3 py-6 text-center text-[12px]" style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}>
          No caption events. Click <span style={{ color: "var(--accent)" }}>+ add at playhead</span> to start.
        </div>
      ) : (
        <div className="max-h-[40vh] overflow-y-auto">
          {captions.map((c, idx) => {
            const isActive = idx === activeIdx;
            return (
              <div
                key={idx}
                className="grid items-center gap-2 px-3 py-1.5"
                style={{
                  gridTemplateColumns: "70px 70px 1fr auto auto auto auto",
                  borderBottom: idx < captions.length - 1 ? "1px solid var(--border)" : "none",
                  background: isActive ? "rgba(94, 230, 175, 0.08)" : "transparent",
                }}
              >
                <CaptionTimeInput
                  value={c.start}
                  max={c.end - 0.05}
                  onCommit={(t) => onMutate(idx, { start: t })}
                />
                <CaptionTimeInput
                  value={c.end}
                  min={c.start + 0.05}
                  max={duration}
                  onCommit={(t) => onMutate(idx, { end: t })}
                />
                <input
                  type="text"
                  value={c.text}
                  onChange={(e) => onMutate(idx, { text: e.target.value })}
                  className="px-2 py-1 rounded text-[12px]"
                  style={{
                    background: "var(--bg-card)",
                    color: "var(--text)",
                    border: "1px solid var(--border)",
                    fontFamily: "'JetBrains Mono', monospace",
                    minWidth: 0,
                  }}
                  placeholder="(empty caption — won't render)"
                />
                <button
                  type="button"
                  onClick={() => onMutate(idx, { start: playhead })}
                  className="px-2 py-1 rounded text-[10px]"
                  style={{ background: "var(--bg-card)", color: "var(--text-secondary)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
                  title="Set start to current playhead"
                >
                  ⇤ph
                </button>
                <button
                  type="button"
                  onClick={() => onMutate(idx, { end: playhead })}
                  className="px-2 py-1 rounded text-[10px]"
                  style={{ background: "var(--bg-card)", color: "var(--text-secondary)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
                  title="Set end to current playhead"
                >
                  ph⇥
                </button>
                <button
                  type="button"
                  onClick={() => onInsertAfter(idx, c.end)}
                  className="px-2 py-1 rounded text-[10px]"
                  style={{ background: "var(--bg-card)", color: "var(--text-secondary)", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
                  title="Insert a new event after this one"
                >
                  +
                </button>
                <button
                  type="button"
                  onClick={() => onRemove(idx)}
                  className="px-2 py-1 rounded text-[10px]"
                  style={{ background: "var(--bg-card)", color: "#ff8080", border: "1px solid var(--border)", fontFamily: "'JetBrains Mono', monospace" }}
                  title="Delete this event"
                >
                  ×
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/**
 * Lightweight time input that:
 *  - Renders the current value as `s.ms` (seconds with two decimals).
 *  - Commits on blur/Enter so per-keystroke changes don't spam the
 *    JASSUB ASS rebuild.
 *  - Reverts on invalid input.
 */
function CaptionTimeInput({
  value, min, max, onCommit,
}: {
  value: number;
  min?: number;
  max?: number;
  onCommit: (t: number) => void;
}) {
  return (
    <input
      type="text"
      defaultValue={fmtTime(value)}
      key={value}
      onBlur={(e) => {
        const parsed = parseTime(e.target.value);
        if (parsed == null) {
          e.target.value = fmtTime(value);
          return;
        }
        let v = parsed;
        if (typeof min === "number") v = Math.max(min, v);
        if (typeof max === "number") v = Math.min(max, v);
        onCommit(v);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") {
          (e.target as HTMLInputElement).value = fmtTime(value);
          (e.target as HTMLInputElement).blur();
        }
      }}
      className="px-2 py-1 rounded text-[11px]"
      style={{
        background: "var(--bg-card)",
        color: "var(--text)",
        border: "1px solid var(--border)",
        fontFamily: "'JetBrains Mono', monospace",
        textAlign: "right",
      }}
    />
  );
}
