"""Background replacement preprocessor.

Pipeline:
  1. BiRefNet-portrait — per-frame portrait alpha matte. We then keep only the
     largest connected blob in the alpha so secondary people / framed photos in
     the original recording don't get matted alongside the speaker.
  2. Capture the new blog page with Playwright (chromium headless) and also
     extract the Y-offsets of every section heading on the rendered page.
  3. Ask the LLM to map each transcript segment to the most relevant section,
     then build a smooth (cubic-eased) scroll curve from those keyframes —
     so the visible portion of the blog tracks what the speaker is saying.
  4. Composite the speaker (alpha) over the scrolled blog frame and write a new
     mp4 via an ffmpeg pipe; the caller mixes the dubbed audio in afterwards.
"""
from __future__ import annotations

import os
import subprocess
from typing import Callable, List, Optional

import cv2
import numpy as np
import torch

from models import Segment
from services.ffmpeg import get_video_info


# ---------------------------------------------------------------------------
# BiRefNet model loader (cached)
# ---------------------------------------------------------------------------
#
# BiRefNet is a high-resolution dichotomous image segmentation model
# (ZhengPeng7/BiRefNet). It produces much cleaner edges than RVM —
# particularly around hair and semi-transparent regions — at the cost of
# being per-frame (no temporal recurrence) and noticeably slower.

_matte_model = None
# 512 instead of 1024 — ~3-4× faster, edge quality drop is minor for portraits.
_matte_input_size = (512, 512)
# HF repo for the portrait-specific BiRefNet checkpoint.
_MATTE_REPO = "ZhengPeng7/BiRefNet-portrait"


def _matte_device():
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _load_matte():
    global _matte_model
    if _matte_model is None:
        from transformers import AutoModelForImageSegmentation
        m = AutoModelForImageSegmentation.from_pretrained(
            _MATTE_REPO, trust_remote_code=True
        )
        m.eval()
        m = m.to(_matte_device())
        # Use float32 — fp16 on MPS is flaky for this model.
        _matte_model = m
    return _matte_model


# ImageNet normalization (BiRefNet expects this).
_MATTE_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
_MATTE_STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)


def _matte_frame(frame_bgr: np.ndarray, safe_zone: Optional[tuple] = None) -> np.ndarray:
    """Run BiRefNet on a single BGR frame and return an (H,W) float alpha."""
    H, W = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    inp = cv2.resize(rgb, _matte_input_size, interpolation=cv2.INTER_AREA)
    t = torch.from_numpy(inp).float().div(255.0).permute(2, 0, 1).unsqueeze(0)
    device = _matte_device()
    t = (t - _MATTE_MEAN).div(_MATTE_STD).to(device)
    with torch.no_grad():
        out = _matte_model(t)
        # BiRefNet returns a list of side outputs; the last is the final mask.
        if isinstance(out, (list, tuple)):
            pred = out[-1]
            if isinstance(pred, (list, tuple)):
                pred = pred[-1]
        else:
            pred = out
        pred = pred.sigmoid()[0, 0].float().cpu().numpy()
    alpha = cv2.resize(pred, (W, H), interpolation=cv2.INTER_LINEAR)
    alpha = np.clip(alpha, 0.0, 1.0)
    return _isolate_speaker(alpha, frame_bgr, safe_zone=safe_zone)


# Lazy-loaded MediaPipe face detector. Way more robust than the OpenCV Haar
# cascade — locks on instantly, handles partial profiles and motion blur.
_mp_face_detector = None


def _get_face_detector():
    global _mp_face_detector
    if _mp_face_detector is None:
        import mediapipe as mp
        # model_selection=1 → full-range model, good for typical selfie /
        # talking-head framing where the face is medium-distance.
        _mp_face_detector = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5
        )
    return _mp_face_detector


def _detect_faces(frame_bgr: np.ndarray) -> list:
    """Return a list of (x, y, w, h) face boxes in pixel coordinates."""
    H, W = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    results = _get_face_detector().process(rgb)
    out = []
    if not results or not results.detections:
        return out
    for det in results.detections:
        rb = det.location_data.relative_bounding_box
        x = int(max(0, rb.xmin * W))
        y = int(max(0, rb.ymin * H))
        w = int(min(W - x, rb.width * W))
        h = int(min(H - y, rb.height * H))
        if w > 0 and h > 0:
            out.append((x, y, w, h))
    return out


def _face_to_safe_zone(face_bbox: tuple, W: int, H: int) -> tuple:
    """Expand a face bbox into a torso-covering "safe zone".

    The zone is centered horizontally on the face and extends generously
    downward to include shoulders / chest / hands, with extra padding so
    raised hands don't get clipped. Only this region is allowed to contain
    matte alpha — everything else gets zeroed.
    """
    fx, fy, fw, fh = face_bbox
    fcx = fx + fw / 2
    # Width: 4× face width, capped to frame.
    zw = min(W, int(round(fw * 4.0)))
    zx = max(0, int(round(fcx - zw / 2)))
    if zx + zw > W:
        zx = max(0, W - zw)
    # Vertical: from a bit above the face top to ~7× face height below it.
    top_pad = int(round(fh * 0.6))
    zy = max(0, fy - top_pad)
    bottom = min(H, fy + int(round(fh * 7.5)))
    zh = max(1, bottom - zy)
    return (zx, zy, zw, zh)


def _smooth_bbox(prev: Optional[tuple], cur: Optional[tuple], alpha: float = 0.35) -> Optional[tuple]:
    """Exponential moving average between two bboxes (x,y,w,h)."""
    if prev is None:
        return cur
    if cur is None:
        return prev
    return tuple(int(round((1 - alpha) * p + alpha * c)) for p, c in zip(prev, cur))


# Scene-cut detection threshold. MAD of a 64x36 downscaled grayscale thumb
# between consecutive frames on a 0-255 scale. Hard cuts on real footage
# come in well above 30; soft cross-dissolves stay below ~15. 25 is the
# safe middle that catches cuts we care about without tripping on motion.
_SCENE_CUT_MAD = 25.0
_SCENE_THUMB_SIZE = (64, 36)


def _frame_thumb(frame_bgr) -> "np.ndarray":
    """Downscaled grayscale thumbnail for cheap scene-cut scoring."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, _SCENE_THUMB_SIZE, interpolation=cv2.INTER_AREA)


def _is_scene_cut(prev_thumb, cur_thumb) -> bool:
    """True when the two thumbnails look different enough to be a hard cut."""
    if prev_thumb is None or cur_thumb is None:
        return False
    mad = float(np.mean(np.abs(cur_thumb.astype(np.int16) - prev_thumb.astype(np.int16))))
    return mad > _SCENE_CUT_MAD


def precompute_safe_zones(
    video_path: str,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    cancel_check: Optional[Callable[[], None]] = None,
) -> list:
    """Sweep the video once and return a per-frame list of safe-zone bboxes.

    Each entry is `(x, y, w, h)` in pixel coordinates, EMA-smoothed across
    frames. Frames where mediapipe missed the face inherit the previous zone.
    The first valid zone is back-filled to all preceding frames.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video {video_path}")
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

    raw_zones: list = []
    last_zone: Optional[tuple] = None
    prev_thumb = None
    idx = 0
    try:
        while True:
            ok, frame_bgr = cap.read()
            if not ok:
                break
            if cancel_check is not None and idx % 30 == 0:
                cancel_check()
            # On a hard scene cut the speaker may have moved to a totally
            # different part of the frame (or the shot may not contain them
            # at all). Break the EMA here so the first frame of the new
            # shot uses its own fresh bbox instead of a half-blended ghost
            # of the previous shot's zone — otherwise the Gaussian-feathered
            # safe-zone mask leaks the previous speaker's silhouette onto
            # the new shot for ~3-4 frames until the EMA converges.
            cur_thumb = _frame_thumb(frame_bgr)
            if _is_scene_cut(prev_thumb, cur_thumb):
                last_zone = None
            prev_thumb = cur_thumb

            faces = _detect_faces(frame_bgr)
            cur_zone: Optional[tuple] = None
            if faces:
                fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                cur_zone = _face_to_safe_zone((fx, fy, fw, fh), W, H)
            smoothed = _smooth_bbox(last_zone, cur_zone)
            raw_zones.append(smoothed)
            last_zone = smoothed
            idx += 1
            if progress_cb and n_frames > 0 and idx % 10 == 0:
                progress_cb(idx / n_frames, f"Locking on speaker ({idx}/{n_frames})")
    finally:
        cap.release()

    # Back-fill leading None entries with the first valid zone we found.
    first_valid = next((z for z in raw_zones if z is not None), None)
    if first_valid is not None:
        for i, z in enumerate(raw_zones):
            if z is None:
                raw_zones[i] = first_valid
            else:
                break
    return raw_zones


def _isolate_speaker(
    alpha: np.ndarray,
    frame_bgr: np.ndarray,
    safe_zone: Optional[tuple] = None,
) -> np.ndarray:
    """Keep only the speaker's matte.

    If `safe_zone` (x,y,w,h) is provided, alpha is zeroed everywhere outside
    that rectangle BEFORE component analysis — guaranteeing no off-zone
    content (e.g. people in posters / photos) can survive.

    Inside the zone we still keep only the connected component containing
    the largest detected face (or the largest blob in the zone as fallback).
    """
    H, W = alpha.shape[:2]

    # Apply the hard safe-zone mask first.
    if safe_zone is not None:
        zx, zy, zw, zh = safe_zone
        zx = max(0, min(W, zx))
        zy = max(0, min(H, zy))
        zw = max(0, min(W - zx, zw))
        zh = max(0, min(H - zy, zh))
        if zw > 0 and zh > 0:
            zone_mask = np.zeros((H, W), dtype=np.float32)
            zone_mask[zy:zy + zh, zx:zx + zw] = 1.0
            # Soften the zone edge so the speaker's silhouette doesn't get
            # clipped if the matte spills slightly over the boundary.
            zone_mask = cv2.GaussianBlur(zone_mask, (31, 31), 0)
            alpha = alpha * zone_mask

    binary = (alpha > 0.5).astype(np.uint8)
    if binary.sum() == 0:
        return alpha
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n_labels <= 2:
        return alpha

    faces = _detect_faces(frame_bgr)
    target_label: Optional[int] = None
    if len(faces) > 0:
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        cx, cy = fx + fw // 2, fy + fh // 2
        cy = min(max(cy, 0), labels.shape[0] - 1)
        cx = min(max(cx, 0), labels.shape[1] - 1)
        lab = int(labels[cy, cx])
        if lab > 0:
            target_label = lab
        else:
            sub = labels[fy:fy + fh, fx:fx + fw]
            if sub.size > 0:
                vals, counts = np.unique(sub[sub > 0], return_counts=True)
                if vals.size > 0:
                    target_label = int(vals[np.argmax(counts)])

    if target_label is None:
        areas = stats[1:, cv2.CC_STAT_AREA]
        target_label = 1 + int(np.argmax(areas))

    keep = (labels == target_label).astype(np.float32)
    keep = cv2.dilate(keep, np.ones((5, 5), np.uint8), iterations=1)
    return alpha * keep


# ---------------------------------------------------------------------------
# Playwright capture
# ---------------------------------------------------------------------------

async def _capture_blog_async(url: str, width: int, height: int, out_path: str) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
            )
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            # Trigger lazy-loaded content: incrementally scroll to the bottom,
            # wait for network to settle, then return to top.
            try:
                await page.evaluate(
                    """
                    async () => {
                        const sleep = (ms) => new Promise(r => setTimeout(r, ms));
                        const step = Math.max(200, Math.floor(window.innerHeight * 0.8));
                        let y = 0;
                        const max = document.body.scrollHeight;
                        while (y < max) {
                            window.scrollTo(0, y);
                            await sleep(150);
                            y += step;
                        }
                        window.scrollTo(0, document.body.scrollHeight);
                        await sleep(400);
                        window.scrollTo(0, 0);
                    }
                    """
                )
            except Exception as e:
                print(f"[bg] lazy-load scroll failed: {e}")
            try:
                await page.wait_for_load_state("networkidle", timeout=30_000)
            except Exception:
                pass
            try:
                await page.wait_for_timeout(2500)
            except Exception:
                pass
            await page.screenshot(path=out_path, full_page=True)
            # Pull every heading + paragraph anchor with its Y offset, so we can
            # later align transcript segments to specific places on the page.
            sections = await page.evaluate(
                """
                () => {
                    const out = [];
                    const sel = 'h1, h2, h3, h4, p';
                    document.querySelectorAll(sel).forEach((el) => {
                        const text = (el.innerText || '').trim();
                        if (!text || text.length < 8) return;
                        const r = el.getBoundingClientRect();
                        const y = r.top + window.scrollY;
                        out.push({ tag: el.tagName.toLowerCase(), y, text });
                    });
                    return out;
                }
                """
            )
        finally:
            await browser.close()
    return {"path": out_path, "sections": sections or []}


def capture_blog(url: str, width: int, height: int, out_path: str) -> dict:
    """Render `url` headless at `width`x`height`, save a full-page screenshot,
    and also return a list of `{tag, y, text}` anchors for every heading /
    paragraph on the page (Y in CSS pixels relative to the top of the page).

    Returns ``{"path": <screenshot path>, "sections": [...]}``.

    Run on a worker thread so the inner asyncio loop doesn't collide with
    ``run_pipeline``'s outer loop.
    """
    import asyncio
    import threading

    result: dict = {}

    def _runner():
        try:
            result["data"] = asyncio.run(
                _capture_blog_async(url, width, height, out_path)
            )
        except BaseException as e:
            result["error"] = e

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "error" in result:
        raise result["error"]
    return result["data"]


# ---------------------------------------------------------------------------
# Matting + flow + composite (single pass over the video)
# ---------------------------------------------------------------------------

def _build_scroll_curve(
    keyframes: List[tuple],  # [(time_seconds, target_y_offset)]
    duration: float,
    max_offset: float,
    viewport_height: int,
) -> Callable[[float], float]:
    """Build a smooth scroll-offset function `f(t) -> y`.

    Uses linear interpolation between keyframes (constant velocity per
    segment, no ease-in/out velocity bursts), then applies a hard pixels-
    per-second velocity cap so abrupt jumps between distant sections are
    spread out across multiple seconds instead of flashing past in one frame.
    """
    if not keyframes:
        # Linear top→bottom over the whole duration as a fallback.
        keyframes = [(0.0, 0.0), (max(duration, 1e-3), max_offset)]
    keyframes = sorted(keyframes, key=lambda kv: kv[0])
    if keyframes[0][0] > 0.0:
        keyframes.insert(0, (0.0, keyframes[0][1]))
    if keyframes[-1][0] < duration:
        keyframes.append((duration, keyframes[-1][1]))

    # Velocity cap: at most ~quarter of a viewport per second. Slow enough to read.
    max_v = max(1.0, viewport_height * 0.25)

    def linear(t: float) -> float:
        if t <= keyframes[0][0]:
            return float(keyframes[0][1])
        if t >= keyframes[-1][0]:
            return float(keyframes[-1][1])
        for i in range(len(keyframes) - 1):
            t0, y0 = keyframes[i]
            t1, y1 = keyframes[i + 1]
            if t0 <= t <= t1:
                if t1 - t0 <= 1e-6:
                    return float(y1)
                u = (t - t0) / (t1 - t0)
                return float(y0 + (y1 - y0) * u)
        return float(keyframes[-1][1])

    # Velocity-limited closure: remembers the last (t, y) it returned and
    # never moves more than `max_v` pixels per real second between calls.
    state = {"last_t": -1.0, "last_y": float(keyframes[0][1])}

    def clamped(t: float) -> float:
        target = linear(t)
        last_t = state["last_t"]
        last_y = state["last_y"]
        if last_t < 0:
            y = target
        else:
            dt = max(1e-3, t - last_t)
            max_step = max_v * dt
            dy = target - last_y
            if dy > max_step:
                y = last_y + max_step
            elif dy < -max_step:
                y = last_y - max_step
            else:
                y = target
        y = max(0.0, min(float(max_offset), y))
        state["last_t"] = t
        state["last_y"] = y
        return y

    return clamped


def replace_background(
    video_path: str,
    blog_url: str,
    out_path: str,
    work_dir: str,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    segments: Optional[List[Segment]] = None,
    cancel_check: Optional[Callable[[], None]] = None,
    safe_zones: Optional[list] = None,
) -> str:
    """Run the full background-swap on `video_path`, write to `out_path`.

    `progress_cb(frac, label)` is called periodically with `frac` in [0,1].
    `segments` (optional) are the transcript segments — when supplied, we use
    the LLM to align them to blog sections so the scroll tracks the speech.
    """
    info = get_video_info(video_path)
    W, H = info["width"], info["height"]
    duration = float(info.get("duration") or 0.0)

    def _p(frac: float, label: str):
        if progress_cb:
            try:
                progress_cb(max(0.0, min(1.0, frac)), label)
            except Exception:
                pass

    _p(0.01, "Capturing blog screenshot")
    blog_png = os.path.join(work_dir, "blog_fullpage.png")
    capture_data = capture_blog(blog_url, W, H, blog_png)
    page_sections = capture_data.get("sections", [])

    blog_img = cv2.imread(blog_png, cv2.IMREAD_COLOR)
    if blog_img is None:
        raise RuntimeError(f"Failed to read blog screenshot {blog_png}")
    bH, bW = blog_img.shape[:2]
    # Resize width to match the video width (preserves aspect of the page).
    if bW != W:
        scale = W / bW
        new_h = max(int(round(bH * scale)), H)
        blog_img = cv2.resize(blog_img, (W, new_h), interpolation=cv2.INTER_AREA)
        bH, bW = blog_img.shape[:2]
    # If the page is shorter than the viewport, pad it so we always have at
    # least one viewport-height of pixels to crop.
    if bH < H:
        pad = np.zeros((H - bH, W, 3), dtype=blog_img.dtype)
        blog_img = np.vstack([blog_img, pad])
        bH = blog_img.shape[0]
    max_offset = max(bH - H, 0)

    # --- Open video, prepare ffmpeg writer pipe ---
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video {video_path}")
    # Use ffprobe-derived fps (exact fraction) instead of OpenCV's rounded
    # value, otherwise the bg-replaced mp4 ends up with the wrong duration
    # and downstream audio sync drifts.
    fps_str = info.get("fps_str", "30/1")
    fps = float(info.get("fps", cap.get(cv2.CAP_PROP_FPS) or 30.0))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    _load_matte()

    # --- Build the scroll curve (semantic alignment if we have segments) ---
    # The page Y offsets we got from Playwright are in CSS pixels at the
    # captured viewport width; the screenshot was rescaled to match the video
    # width, so multiply Y by the same scale factor.
    blog_scale = W / float(info["width"])  # 1.0 right now, but kept for clarity
    keyframes: List[tuple] = []
    if segments and page_sections:
        try:
            from services.llm_tasks import align_segments_to_blog_sections
            mapping = align_segments_to_blog_sections(segments, page_sections)
            for seg_idx, sec_idx in mapping.items():
                if 0 <= sec_idx < len(page_sections) and 0 <= seg_idx < len(segments):
                    # Aim the section a third of the way down the viewport
                    # so the speaker isn't reading off the very top edge.
                    target_y = page_sections[sec_idx]["y"] * blog_scale - H * 0.5
                    keyframes.append((float(segments[seg_idx].start), float(target_y)))
        except Exception as e:
            print(f"[bg] semantic scroll alignment failed: {e} — falling back to linear")
            keyframes = []
    # `max_offset` is computed below from the rendered blog image height; we
    # build the actual scroll function once we know it.
    pending_keyframes = keyframes

    # ffmpeg writer (raw BGR -> mp4, video only; we'll mux audio after)
    silent_out = os.path.join(work_dir, "_bg_silent.mp4")
    ff = subprocess.Popen(
        [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{W}x{H}", "-r", fps_str,  # exact fraction from ffprobe
            "-i", "-",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
            "-crf", "18",
            "-vsync", "cfr", "-r", fps_str,
            silent_out,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert ff.stdin is not None

    # Now that we know the rendered blog height, build the scroll curve.
    scroll_at = _build_scroll_curve(
        pending_keyframes,
        duration if duration > 0 else (n_frames / fps if fps else 1.0),
        float(max_offset),
        H,
    )

    idx = 0

    try:
        with torch.no_grad():
            while True:
                ok, frame_bgr = cap.read()
                if not ok:
                    break

                if cancel_check is not None and idx % 5 == 0:
                    cancel_check()

                # Pull the precomputed safe zone for this frame, if available.
                cur_zone = None
                if safe_zones is not None and idx < len(safe_zones):
                    cur_zone = safe_zones[idx]
                alpha = _matte_frame(frame_bgr, safe_zone=cur_zone)  # (H,W) float in [0,1]
                fgr_bgr = frame_bgr

                # --- Composite at the smoothly-eased scroll offset ---
                t_now = idx / fps if fps else 0.0
                off = int(round(scroll_at(t_now)))
                bg_crop = blog_img[off:off + H, 0:W]
                if bg_crop.shape[0] != H:
                    # Safety pad
                    pad = np.zeros((H - bg_crop.shape[0], W, 3), dtype=bg_crop.dtype)
                    bg_crop = np.vstack([bg_crop, pad])

                a = alpha[..., None]  # H,W,1
                composed = (fgr_bgr.astype(np.float32) * a +
                            bg_crop.astype(np.float32) * (1.0 - a))
                composed = np.clip(composed, 0, 255).astype(np.uint8)

                ff.stdin.write(composed.tobytes())

                idx += 1
                if n_frames > 0 and idx % 5 == 0:
                    _p(0.05 + 0.9 * (idx / n_frames), f"Replacing background ({idx}/{n_frames})")

    finally:
        cap.release()
        try:
            ff.stdin.close()
        except Exception:
            pass
        ff.wait()

    # --- Force the silent video to *exactly* match the original duration ---
    # cv2 sometimes drops the last frame or reads a stale fps, which leaves the
    # bg-replaced video a few hundred ms shorter or longer than the source. The
    # downstream dub timeline assumes the original duration, so any drift here
    # turns into late/early lip sync. Trim or clone-pad to match.
    fixed_silent = os.path.join(work_dir, "_bg_silent_fixed.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", silent_out,
            "-vf", f"tpad=stop_mode=clone:stop_duration={duration}",
            "-t", f"{duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
            "-crf", "18",
            "-r", fps_str,
            fixed_silent,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    _p(0.97, "Muxing audio")
    # Mux original audio into the duration-fixed composited video.
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", fixed_silent,
            "-i", video_path,
            "-c:v", "copy",
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            out_path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for tmp in (silent_out, fixed_silent):
        try:
            os.remove(tmp)
        except OSError:
            pass

    _p(1.0, "Background replaced")
    return out_path
