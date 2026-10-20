"use client";

import { useEffect, useRef } from "react";

type CaptionBox = { x: number; y: number; scale: number; rotation: number };
export type CaptionEvent = { start: number; end: number; text: string };

type Props = {
  videoEl: HTMLVideoElement | null;
  captionBox: CaptionBox;
  captionStyle: Record<string, unknown>;
  apiBase: string;
  // When supplied, JASSUB renders all events at their actual times
  // (used by the edit page). When omitted, the home-page style preview
  // shows a single always-on "Sample caption" so users can tune look.
  captions?: CaptionEvent[];
};

async function fetchAss(
  apiBase: string,
  videoEl: HTMLVideoElement,
  captionBox: CaptionBox,
  captionStyle: Record<string, unknown>,
  captions: CaptionEvent[] | undefined,
  signal: AbortSignal,
): Promise<string | null> {
  const w = videoEl.videoWidth || 1920;
  const h = videoEl.videoHeight || 1080;
  try {
    const body: Record<string, unknown> = {
      caption_box: captionBox,
      caption_style: captionStyle,
      video_width: w,
      video_height: h,
    };
    if (captions && captions.length > 0) body.captions = captions;
    else body.text = "Sample caption";
    const res = await fetch(`${apiBase}/api/preview-ass`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal,
      body: JSON.stringify(body),
    });
    if (!res.ok) return null;
    const data = await res.json();
    return typeof data?.ass === "string" ? data.ass : null;
  } catch {
    return null;
  }
}

export default function CaptionPreview({ videoEl, captionBox, captionStyle, apiBase, captions }: Props) {
  // Holds the live JASSUB instance. Untyped because the type lives behind
  // a dynamic import and we only call .destroy / .renderer on it.
  const jassubRef = useRef<{ destroy: () => Promise<void>; renderer: { setTrack: (s: string) => unknown } } | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Once the video has reported its intrinsic size at least once, we can
  // mount JASSUB. Triggering on metadata avoids a 0×0 render that some
  // libass builds refuse to draw into.
  const readyRef = useRef(false);

  // Stable signature so the update effect only fires on real content change,
  // not on every parent re-render that builds new object identities.
  const sig = JSON.stringify({ captionBox, captionStyle, captions });

  // Mount / unmount JASSUB whenever the underlying video element identity
  // changes (file replaced, component re-mounted, etc.).
  useEffect(() => {
    if (!videoEl) return;
    let cancelled = false;
    let inst: { destroy: () => Promise<void>; renderer: { setTrack: (s: string) => unknown } } | null = null;

    const start = async () => {
      // Wait until the video reports an intrinsic size — JASSUB sizes its
      // canvas off videoWidth/videoHeight at construction time.
      if (!videoEl.videoWidth || !videoEl.videoHeight) {
        await new Promise<void>((resolve) => {
          const onMeta = () => { videoEl.removeEventListener("loadedmetadata", onMeta); resolve(); };
          videoEl.addEventListener("loadedmetadata", onMeta);
        });
      }
      if (cancelled) return;
      readyRef.current = true;

      const controller = new AbortController();
      abortRef.current = controller;
      const ass = await fetchAss(apiBase, videoEl, captionBox, captionStyle, captions, controller.signal);
      if (cancelled || !ass) return;

      const mod = await import("jassub");
      const JASSUB = mod.default;
      if (cancelled) return;

      // No workerUrl / wasmUrl override: those defaults use
      // `new URL('./worker/worker.js', import.meta.url)` which webpack
      // (Next.js) resolves into a real worker chunk. Pointing them at
      // hand-copied paths in /public was wrong because JASSUB's
      // `wasm/jassub-worker.js` is the emscripten WASM glue, not the
      // worker entry — the worker then never started.
      // Eager-load Rubik via `fonts:` so it's guaranteed to be in libass'
      // memory before the first render — `availableFonts` is lazy and any
      // fetch failure gets silently swallowed by Promise.allSettled.
      // Don't set `defaultFont` either: leaving it unset keeps JASSUB's
      // built-in liberation sans as a fallback so missing fonts still
      // render glyphs (they'd just look wrong) instead of a blank canvas.
      inst = new JASSUB({
        video: videoEl,
        subContent: ass,
        fonts: ["/fonts/Rubik-Bold.ttf"],
        availableFonts: {
          rubik: "/fonts/Rubik-Bold.ttf",
          "rubik bold": "/fonts/Rubik-Bold.ttf",
          anton: "/fonts/Anton-Regular.ttf",
          inter: "/fonts/Inter-VF.ttf",
          montserrat: "/fonts/Montserrat-VF.ttf",
          oswald: "/fonts/Oswald-VF.ttf",
        },
        debug: true,
      }) as unknown as { destroy: () => Promise<void>; renderer: { setTrack: (s: string) => unknown } };
      jassubRef.current = inst;
    };

    start();

    return () => {
      cancelled = true;
      readyRef.current = false;
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      const live = jassubRef.current;
      jassubRef.current = null;
      if (live) {
        // destroy() is async but we don't need to wait — the canvas is
        // wiped immediately and the worker shuts itself down.
        live.destroy().catch(() => {});
      }
    };
  }, [videoEl, apiBase]);

  // Refetch the ASS string whenever the caption settings change, debounced.
  useEffect(() => {
    if (!videoEl) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      const inst = jassubRef.current;
      if (!inst || !videoEl) return;
      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const ass = await fetchAss(apiBase, videoEl, captionBox, captionStyle, captions, controller.signal);
      if (!ass) return;
      try { inst.renderer.setTrack(ass); } catch { /* worker may have torn down */ }
    }, 80);
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
    // sig, not the raw objects — avoids firing when parent re-renders without
    // actually changing caption content.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, videoEl, apiBase]);

  return null;
}
