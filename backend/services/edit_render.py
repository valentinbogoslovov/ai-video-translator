"""Render an Edit Decision List from the post-translation editor into
a single mp4 via FFmpeg's filter_complex.

Inputs (`payload` dict, validated and normalised here):
  clips:         list of {id, track, src, sourceStart, sourceEnd,
                          timelineStart, speed}
  captions:      list of {start, end, text}
  caption_box:   {x, y, scale, rotation}
  caption_style: dict (same schema as the live preview)

The render mirrors the production pipeline's audio mix architecture:
voice clips are time-stretched + delayed + amix'd, the music bed is
sidechain-ducked by the voice, and the two are summed. Captions are
re-built from the editor's events using the same `_build_styled_subs`
helper that powers the live preview (so what the user saw IS what gets
burned).
"""
from __future__ import annotations
import os
import subprocess

from services.captions import generate_edited_ass
from services.ffmpeg import build_atempo_chain


def _resolve(work_dir: str, lang_dir: str, rel_src: str) -> str:
    """Resolve a clip's `src` (relative to lang_dir) under work_dir,
    rejecting any path that escapes the job root."""
    if not rel_src:
        raise ValueError("Empty clip src")
    work_real = os.path.realpath(work_dir)
    candidate = os.path.realpath(os.path.join(lang_dir, rel_src))
    if candidate != work_real and not candidate.startswith(work_real + os.sep):
        raise ValueError(f"Clip path escapes job root: {rel_src}")
    if not os.path.isfile(candidate):
        raise ValueError(f"Clip source not found: {rel_src}")
    return candidate


def _audio_clip_filter(in_idx: int, c: dict, out_label: str) -> str:
    """Build the per-clip audio filter chain:
    atrim (in source) → asetpts → optional atempo → optional adelay.
    """
    src_start = max(0.0, float(c.get("sourceStart", 0.0)))
    src_end = float(c["sourceEnd"])
    if src_end <= src_start:
        raise ValueError(f"Clip {c.get('id')} has non-positive source span")
    parts = [
        f"[{in_idx}:a]"
        f"atrim={src_start:.6f}:{src_end:.6f},"
        f"asetpts=PTS-STARTPTS"
    ]
    speed = float(c.get("speed", 1.0))
    if speed > 0 and abs(speed - 1.0) > 1e-3:
        # build_atempo_chain handles ranges outside [0.5, 2.0] by stacking.
        parts.append(build_atempo_chain(speed))
    delay_ms = max(0, int(round(float(c.get("timelineStart", 0.0)) * 1000)))
    if delay_ms > 0:
        parts.append(f"adelay={delay_ms}|{delay_ms}")
    return ",".join(parts) + f"[{out_label}]"


def _write_edited_ass(
    payload: dict, manifest: dict, lang_dir: str
) -> str:
    """Build a fresh ASS file from the EDL's caption events and write it
    to disk for the ffmpeg subtitles= filter to read.
    """
    ass_text = generate_edited_ass(
        payload.get("captions") or [],
        int(manifest["video_width"]),
        int(manifest["video_height"]),
        font_name="Rubik",
        caption_box=payload.get("caption_box") or manifest.get("caption_box") or {},
        caption_style=payload.get("caption_style") or manifest.get("caption_style") or {},
    )
    out_path = os.path.join(lang_dir, "captions_edited.ass")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(ass_text)
    return out_path


def _escape_for_ffmpeg_filter(path: str) -> str:
    """Escape a file path for use inside an ffmpeg filter expression
    (subtitles=...). Colons and quotes are the troublesome ones."""
    # Order matters: backslash first.
    return (
        path.replace("\\", "\\\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
    )


def render_edit(
    payload: dict,
    manifest: dict,
    job_id: str,
    language: str,
    work_dir: str,
    lang_dir: str,
    fonts_dir: str,
) -> str:
    """Render the EDL into final_edited.mp4. Returns the absolute path
    to the output file.

    v1 limits: exactly one video clip is supported (multi-clip video is
    a v2 feature). Voice and music tracks accept any number of clips.
    """
    clips = payload.get("clips") or []
    if not clips:
        raise ValueError("EDL has no clips")

    video_clips = [c for c in clips if c.get("track") == "video"]
    voice_clips = [c for c in clips if c.get("track") == "voice"]
    music_clips = [c for c in clips if c.get("track") == "music"]

    if len(video_clips) != 1:
        raise ValueError(
            f"v1 supports exactly one video clip; got {len(video_clips)}"
        )
    video_clip = video_clips[0]

    # Resolve all clip source paths up-front so we fail fast on missing
    # files before invoking ffmpeg.
    video_src = _resolve(work_dir, lang_dir, video_clip["src"])
    voice_srcs = [_resolve(work_dir, lang_dir, c["src"]) for c in voice_clips]
    music_srcs = [_resolve(work_dir, lang_dir, c["src"]) for c in music_clips]

    # Build a fresh ASS file from the (possibly user-edited) caption
    # events. The render bakes this into the final mp4.
    ass_path = _write_edited_ass(payload, manifest, lang_dir)

    # Compose ffmpeg input args. Index 0 is the video; voice clips are
    # 1..N; music clips follow.
    input_args: list[str] = ["-i", video_src]
    for p in voice_srcs:
        input_args += ["-i", p]
    for p in music_srcs:
        input_args += ["-i", p]

    filters: list[str] = []

    # Voice clips → individual filtered streams → amix.
    voice_labels: list[str] = []
    for i, c in enumerate(voice_clips):
        label = f"vc{i}"
        filters.append(_audio_clip_filter(1 + i, c, label))
        voice_labels.append(label)

    if len(voice_labels) > 1:
        joined = "".join(f"[{l}]" for l in voice_labels)
        filters.append(
            f"{joined}amix=inputs={len(voice_labels)}:duration=longest:"
            "dropout_transition=0:normalize=0[voice]"
        )
    elif voice_labels:
        filters.append(f"[{voice_labels[0]}]anull[voice]")

    # Music clips → individual streams → amix.
    music_offset = 1 + len(voice_clips)
    music_labels: list[str] = []
    for j, c in enumerate(music_clips):
        label = f"mc{j}"
        filters.append(_audio_clip_filter(music_offset + j, c, label))
        music_labels.append(label)

    if len(music_labels) > 1:
        joined = "".join(f"[{l}]" for l in music_labels)
        filters.append(
            f"{joined}amix=inputs={len(music_labels)}:duration=longest:"
            "dropout_transition=0:normalize=0[music_pre]"
        )
    elif music_labels:
        filters.append(f"[{music_labels[0]}]anull[music_pre]")

    # Final audio mix. Match the production pipeline's sidechain ducker
    # so the music drops behind the voice the same way it did before.
    # NB: ffmpeg consumes a label exactly once per filter graph, so [voice]
    # must be `asplit`-ed into two copies — one as the sidechain key for
    # the compressor, the other for the final amix.
    if voice_labels and music_labels:
        filters.append("[voice]asplit=2[voice_main][voice_key]")
        filters.append(
            "[music_pre][voice_key]sidechaincompress=threshold=0.04:ratio=4:"
            "attack=10:release=180[music_ducked]"
        )
        filters.append(
            "[voice_main][music_ducked]amix=inputs=2:duration=longest:"
            "dropout_transition=0:normalize=0[audio_out]"
        )
    elif voice_labels:
        filters.append("[voice]anull[audio_out]")
    elif music_labels:
        filters.append("[music_pre]anull[audio_out]")
    else:
        raise ValueError("EDL has no audio clips (need at least one voice or music)")

    # Video filter: trim (in source) → setpts (speed) → burn captions.
    src_start = max(0.0, float(video_clip.get("sourceStart", 0.0)))
    src_end = float(video_clip["sourceEnd"])
    if src_end <= src_start:
        raise ValueError("Video clip has non-positive source span")
    speed = float(video_clip.get("speed", 1.0))
    pts_factor = 1.0 / speed if speed > 0 else 1.0

    vfilter = (
        f"[0:v]trim={src_start:.6f}:{src_end:.6f},"
        f"setpts={pts_factor:.6f}*PTS,"
        f"subtitles={_escape_for_ffmpeg_filter(ass_path)}:"
        f"fontsdir={_escape_for_ffmpeg_filter(fonts_dir)}"
        "[video_out]"
    )
    filters.append(vfilter)

    filter_complex = ";".join(filters)
    output_path = os.path.join(lang_dir, "final_edited.mp4")

    cmd = [
        "ffmpeg", "-y", *input_args,
        "-filter_complex", filter_complex,
        "-map", "[video_out]",
        "-map", "[audio_out]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "256k",
        "-ar", "48000",
        "-movflags", "+faststart",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        # ffmpeg stderr is the diagnostic users actually want.
        tail = proc.stderr.decode(errors="replace")[-1500:]
        raise RuntimeError(f"ffmpeg exit {proc.returncode}: {tail}")
    return output_path
