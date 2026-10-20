"use client";

import { useState } from "react";

type CaptionBox = { x: number; y: number; scale: number; rotation: number };
type CaptionStyleDict = Record<string, unknown>;

type Props = {
  captionBox: CaptionBox;
  captionStyle: CaptionStyleDict;
  onBoxChange: (patch: Partial<CaptionBox>) => void;
  onStyleChange: (patch: CaptionStyleDict) => void;
};

function num(value: unknown, fallback: number): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function str(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function bool(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

const FIELD_BG = "var(--surface-2)";
const FIELD_BORDER = "1px solid var(--border)";
const MONO = "'JetBrains Mono', monospace";

function Slider({
  label, value, min, max, step, fmt, onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  fmt?: (v: number) => string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="rounded-lg px-3 py-2" style={{ background: FIELD_BG, border: FIELD_BORDER }}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] uppercase tracking-[0.15em]" style={{ fontFamily: MONO, color: "var(--text-muted)" }}>{label}</span>
        <span className="text-[10px]" style={{ fontFamily: MONO, color: "var(--accent)" }}>
          {fmt ? fmt(value) : value.toFixed(step < 1 ? 2 : 0)}
        </span>
      </div>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full"
        style={{ accentColor: "var(--accent)" }}
      />
    </div>
  );
}

function ColorRow({
  label, value, onChange,
}: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="rounded-lg px-3 py-2 flex items-center justify-between gap-3" style={{ background: FIELD_BG, border: FIELD_BORDER }}>
      <span className="text-[10px] uppercase tracking-[0.15em]" style={{ fontFamily: MONO, color: "var(--text-muted)" }}>{label}</span>
      <input
        type="color" value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-10 h-7 rounded cursor-pointer"
        style={{ background: "transparent", border: FIELD_BORDER }}
      />
    </div>
  );
}

export default function CaptionStylePanel({
  captionBox, captionStyle, onBoxChange, onStyleChange,
}: Props) {
  const [open, setOpen] = useState(false);

  const primaryColor = str(captionStyle.primary_color, "#FFFFFF");
  const outlineColor = str(captionStyle.outline_color, "#000000");
  const shadowColor = str(captionStyle.shadow_color, "#000000");
  const outlineWidth = num(captionStyle.outline_width, 0);
  const shadow = num(captionStyle.shadow, 2);
  const shadowAlpha = num(captionStyle.shadow_alpha, 0.88);
  const shadowBlur = num(captionStyle.shadow_blur, 0);
  const letterSpacing = num(captionStyle.letter_spacing, 0);
  const lineSpacing = num(captionStyle.line_spacing, 1.2);
  const isBold = bool(captionStyle.bold, true);
  // Caption grouping tuneables. Changes here only take effect after the
  // user clicks "Re-group" in the events panel (or runs a fresh
  // translation) — the editor's working caption list is otherwise
  // append-only and respects manual edits.
  const maxWordsPerLine = Math.round(num(captionStyle.max_words_per_line, 3));
  const maxLinesPerCaption = Math.round(num(captionStyle.max_lines_per_caption, 2));
  const breakOnPunct = bool(captionStyle.break_on_punct, true);
  const breakOnPauseMs = Math.round(num(captionStyle.break_on_pause_ms, 100));

  return (
    <div className="rounded-xl" style={{ background: "var(--surface-2)", border: FIELD_BORDER, overflow: "hidden" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 cursor-pointer"
        style={{ background: "var(--bg-card)", borderBottom: open ? FIELD_BORDER : "none" }}
      >
        <span className="text-[11px] uppercase tracking-[0.15em]" style={{ fontFamily: MONO, color: "var(--text-muted)" }}>caption style</span>
        <span className="ml-auto text-[10px]" style={{ fontFamily: MONO, color: "var(--text-muted)" }}>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="p-3 grid grid-cols-2 sm:grid-cols-3 gap-2">
          {/* Position / size / rotation */}
          <Slider label="X (%)" value={captionBox.x} min={0} max={100} step={0.5} fmt={(v) => `${v.toFixed(1)}%`} onChange={(v) => onBoxChange({ x: v })} />
          <Slider label="Y (%)" value={captionBox.y} min={0} max={100} step={0.5} fmt={(v) => `${v.toFixed(1)}%`} onChange={(v) => onBoxChange({ y: v })} />
          <Slider label="Scale (%)" value={captionBox.scale} min={5} max={100} step={0.5} fmt={(v) => `${v.toFixed(1)}%`} onChange={(v) => onBoxChange({ scale: v })} />
          <Slider label="Rotation" value={captionBox.rotation} min={-30} max={30} step={1} fmt={(v) => `${v.toFixed(0)}°`} onChange={(v) => onBoxChange({ rotation: v })} />
          {/* Bold toggle uses the same box-row layout as colors. */}
          <div className="rounded-lg px-3 py-2 flex items-center justify-between" style={{ background: FIELD_BG, border: FIELD_BORDER }}>
            <span className="text-[10px] uppercase tracking-[0.15em]" style={{ fontFamily: MONO, color: "var(--text-muted)" }}>Bold</span>
            <button
              type="button"
              onClick={() => onStyleChange({ bold: !isBold })}
              className="px-2 py-0.5 rounded text-[10px] font-medium"
              style={{
                background: isBold ? "var(--accent)" : "var(--bg-card)",
                color: isBold ? "var(--bg)" : "var(--text-secondary)",
                border: FIELD_BORDER,
                fontFamily: MONO,
              }}
            >
              {isBold ? "ON" : "OFF"}
            </button>
          </div>
          {/* Spacing */}
          <Slider label="Tracking" value={letterSpacing} min={-0.05} max={0.30} step={0.005}
            fmt={(v) => `${(v * 1000).toFixed(0)}`}
            onChange={(v) => onStyleChange({ letter_spacing: v })} />
          <Slider label="Leading" value={lineSpacing} min={0.8} max={2.0} step={0.05}
            fmt={(v) => `${v.toFixed(2)}×`}
            onChange={(v) => onStyleChange({ line_spacing: v })} />
          {/* Caption line-break rules (apply on Re-group) */}
          <Slider label="Words / line" value={maxWordsPerLine} min={1} max={10} step={1}
            fmt={(v) => `${v.toFixed(0)} max`}
            onChange={(v) => onStyleChange({ max_words_per_line: Math.round(v) })} />
          <Slider label="Lines / caption" value={maxLinesPerCaption} min={1} max={3} step={1}
            fmt={(v) => `${v.toFixed(0)} max`}
            onChange={(v) => onStyleChange({ max_lines_per_caption: Math.round(v) })} />
          <Slider label="Pause break (ms)" value={breakOnPauseMs} min={0} max={500} step={10}
            fmt={(v) => v <= 0 ? "off" : `${v.toFixed(0)} ms`}
            onChange={(v) => onStyleChange({ break_on_pause_ms: Math.round(v) })} />
          <div className="rounded-lg px-3 py-2 flex items-center justify-between" style={{ background: FIELD_BG, border: FIELD_BORDER }}>
            <span className="text-[10px] uppercase tracking-[0.15em]" style={{ fontFamily: MONO, color: "var(--text-muted)" }}>Break on .,;:!?</span>
            <button
              type="button"
              onClick={() => onStyleChange({ break_on_punct: !breakOnPunct })}
              className="px-2 py-0.5 rounded text-[10px] font-medium"
              style={{
                background: breakOnPunct ? "var(--accent)" : "var(--bg-card)",
                color: breakOnPunct ? "var(--bg)" : "var(--text-secondary)",
                border: FIELD_BORDER,
                fontFamily: MONO,
              }}
            >
              {breakOnPunct ? "ON" : "OFF"}
            </button>
          </div>
          {/* Colors */}
          <ColorRow label="Text" value={primaryColor} onChange={(v) => onStyleChange({ primary_color: v })} />
          <ColorRow label="Outline" value={outlineColor} onChange={(v) => onStyleChange({ outline_color: v })} />
          <ColorRow label="Shadow" value={shadowColor} onChange={(v) => onStyleChange({ shadow_color: v })} />
          {/* Outline + shadow knobs */}
          <Slider label="Outline width" value={outlineWidth} min={0} max={8} step={0.5}
            onChange={(v) => onStyleChange({ outline_width: v })} />
          <Slider label="Shadow offset" value={shadow} min={0} max={8} step={0.5}
            onChange={(v) => onStyleChange({ shadow: v })} />
          <Slider label="Shadow opacity" value={shadowAlpha} min={0} max={1} step={0.05}
            fmt={(v) => `${Math.round(v * 100)}%`}
            onChange={(v) => onStyleChange({ shadow_alpha: v })} />
          <Slider label="Shadow softness" value={shadowBlur} min={0} max={10} step={0.5}
            onChange={(v) => onStyleChange({ shadow_blur: v })} />
        </div>
      )}
    </div>
  );
}
