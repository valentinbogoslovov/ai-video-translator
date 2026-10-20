"""Voiceover ingestion: pre-recorded audio files supplied by the user.

When the user uploads a voiceover for a language, we:
  1. Skip TTS entirely for that language.
  2. Optionally take the matching script section as the source of caption text;
     if no script is provided we transcribe the voiceover with the LLM.
  3. Ask the LLM to find, for each original-video segment, the time range in
     the uploaded audio that contains the matching dialogue.
  4. Slice the audio at those ranges and feed the slices into the existing
     fit_segments_to_timeline machinery.

This module owns the filename → language mapping and the LLM alignment
call. The pipeline glue lives in pipeline.py.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from config import settings
from models import Segment
from services.script_parser import _header_lang
from services.providers import generate


def language_from_filename(filename: str) -> Optional[str]:
    """Fuzzy-extract a language code from an uploaded VO filename.

    Tries the whole stem first, then each space/punct-separated piece, using
    the same lookup table the .docx script parser uses (so `en.mp3`,
    `EN_final.wav`, `english.m4a`, `🇩🇪 deutsch.wav` all resolve).
    """
    if not filename:
        return None
    stem = Path(filename).stem
    return _header_lang(stem)


_SILENCE_RMS_DB = -60.0


def _measure_overall_rms_db(audio_path: str) -> float:
    """Return the overall RMS level of ``audio_path`` in dBFS (e.g. -20.0).
    Returns -inf when the file is fully silent or the measurement fails.
    """
    proc = subprocess.run(
        [
            "ffmpeg", "-nostats", "-hide_banner",
            "-i", audio_path,
            "-af", "astats=measure_perchannel=0:measure_overall=RMS_level",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    for line in reversed((proc.stderr or "").splitlines()):
        m = re.search(r"RMS level dB:\s+(-?\d+\.?\d*|-inf)", line)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return float("-inf")
    return float("-inf")


def _loudnorm_measure(audio_path: str) -> Optional[dict]:
    """Run ffmpeg loudnorm in measurement-only mode (pass 1 of 2) and return
    the JSON stats. None if parsing fails.
    """
    proc = subprocess.run(
        [
            "ffmpeg", "-nostats", "-hide_banner",
            "-i", audio_path,
            "-af", "loudnorm=I=-20:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    out = proc.stderr or ""
    # loudnorm prints a JSON block at the end; grab the last {...} span.
    start = out.rfind("{")
    end = out.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(out[start : end + 1])
    except Exception:
        return None


def _measure_integrated_loudness(audio_path: str) -> Optional[float]:
    """Return integrated loudness in LUFS via ffmpeg ebur128, or None if it
    can't be measured (e.g. clip too short)."""
    proc = subprocess.run(
        [
            "ffmpeg", "-nostats", "-hide_banner",
            "-i", audio_path,
            "-filter_complex", "ebur128=peak=true",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    # ebur128 prints a summary with a line like "    I:         -20.5 LUFS"
    for line in reversed((proc.stderr or "").splitlines()):
        m = re.match(r"\s*I:\s+(-?\d+\.?\d*)\s+LUFS", line)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def _probe_active_channels(audio_path: str) -> list[int]:
    """Return 0-indexed channel indices that carry signal above -60 dB RMS.

    Uses ffmpeg's ``astats`` per-channel report. Channels quieter than
    ``_SILENCE_RMS_DB`` are treated as dead (common when a stereo file only
    has content on one side).
    """
    proc = subprocess.run(
        [
            "ffmpeg", "-nostats", "-hide_banner",
            "-i", audio_path,
            "-af", "astats=measure_perchannel=RMS_level:measure_overall=0",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    active: list[int] = []
    current_ch: Optional[int] = None
    for line in (proc.stderr or "").splitlines():
        m = re.search(r"Channel:\s+(\d+)", line)
        if m:
            current_ch = int(m.group(1)) - 1  # astats is 1-indexed
            continue
        if current_ch is not None and "RMS level dB:" in line:
            val_str = line.split("RMS level dB:", 1)[1].strip()
            try:
                val = float(val_str)
            except ValueError:
                val = float("-inf")
            if val > _SILENCE_RMS_DB:
                active.append(current_ch)
            current_ch = None
    return active


def _ffprobe_channel_count(audio_path: str) -> int:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=channels",
            "-of", "default=nw=1:nk=1",
            audio_path,
        ],
        capture_output=True, text=True,
    )
    try:
        return int((proc.stdout or "1").strip() or "1")
    except ValueError:
        return 1


def preprocess_voiceover(
    vo_path: str,
    reference_audio_path: str,
    out_path: str,
) -> str:
    """Normalize an uploaded VO file: dual-mono stereo + loudness-matched to
    the original video's audio.

    1. Probe the VO's channel layout and per-channel signal. Remap so the
       output is always 48 kHz stereo with real content on both L and R:
         - Mono             → duplicated to both channels.
         - L-only / R-only  → the live side duplicated to both channels.
         - True stereo      → kept as-is.
         - >2 channels      → downmixed to stereo.
    2. Measure the reference audio's integrated loudness (LUFS) and apply a
       single-pass loudnorm on the VO with that as the target, so the dub
       sits at the same perceived volume as the original dialogue bed.

    If loudness measurement fails, falls back to a sensible default (-20 LUFS)
    rather than leaving the file untouched.
    """
    n_channels = _ffprobe_channel_count(vo_path)
    if n_channels <= 1:
        chan_filter = "pan=stereo|c0=c0|c1=c0"
    elif n_channels == 2:
        active = _probe_active_channels(vo_path)
        if 0 in active and 1 in active:
            chan_filter = "pan=stereo|c0=c0|c1=c1"
        elif 0 in active:
            chan_filter = "pan=stereo|c0=c0|c1=c0"
        elif 1 in active:
            chan_filter = "pan=stereo|c0=c1|c1=c1"
        else:
            # Everything below -60 dB: pass through, loudnorm will try to lift.
            chan_filter = "aformat=channel_layouts=stereo"
    else:
        chan_filter = "aformat=channel_layouts=stereo"

    target_I = _measure_integrated_loudness(reference_audio_path)
    if target_I is None:
        target_I = -20.0
    # Clamp to a sane broadcast range — ebur128 on very short / silent clips
    # can return pathological values.
    target_I = max(min(target_I, -10.0), -30.0)

    # Two-pass loudnorm: measure the VO first, then apply with those values.
    # This is the ffmpeg-recommended mode and is far more accurate than
    # single-pass, which can under-gain quiet inputs (we were seeing
    # near-silent output from single-pass on real voiceovers).
    measured = _loudnorm_measure(vo_path)
    if measured and all(
        k in measured
        for k in (
            "input_i", "input_tp", "input_lra", "input_thresh", "target_offset",
        )
    ):
        loudnorm_filter = (
            f"loudnorm=I={target_I:.1f}:TP=-1.5:LRA=11:"
            f"measured_I={measured['input_i']}:"
            f"measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:"
            f"measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:"
            f"linear=true:print_format=summary"
        )
    else:
        # Measurement failed (very short clip, etc.) — fall back to single-pass.
        loudnorm_filter = f"loudnorm=I={target_I:.1f}:TP=-1.5:LRA=11"

    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner",
            "-i", vo_path,
            "-af", f"{chan_filter},{loudnorm_filter}",
            "-ar", "48000",
            "-acodec", "pcm_s16le",
            out_path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Safety net: if the whole preprocess chain produced effectively silent
    # output, don't hand the pipeline a dead file — return the input path
    # instead so slicing/alignment still has real audio to work with.
    out_rms = _measure_overall_rms_db(out_path)
    if out_rms < -55.0:
        print(
            f"[voiceover] preprocess output was silent ({out_rms:.1f} dBFS); "
            f"falling back to raw input"
        )
        return vo_path
    return out_path


def slice_audio(
    audio_path: str,
    start: float,
    end: float,
    out_path: str,
    *,
    clean_silences: bool = True,
    lead_pad: float = 0.25,
    tail_pad: float = 0.25,
    silence_db: float = -25.0,
) -> str:
    """ffmpeg-cut `audio_path` from `start`..`end` (seconds) into `out_path`.

    The output is a 16 kHz mono WAV — same format the TTS path produces, so
    the rest of the pipeline can consume it without any changes.

    When `clean_silences` is True (default), the slice is also passed through
    ffmpeg's `silenceremove` filter, which:
      • trims any leading silence (so the slice starts on the first phoneme),
      • trims trailing silence (so it ends on the last phoneme),
      • collapses any internal silent gap >300 ms down to 100 ms.

    This is essential for uploaded voiceovers, which often have pickup/cut
    handles or breaths between sentences that would otherwise eat into the
    speaker's time slot and mess up lip sync once `fit_segments_to_timeline`
    speed-stretches the audio.
    """
    if end <= start:
        end = start + 0.05  # avoid zero-length cut

    # LLM alignment is only word-accurate to ~100–200 ms, so we widen
    # the raw cut by ``lead_pad`` / ``tail_pad`` on each side and then rely
    # on silenceremove to trim back to the real speech edges. Without this
    # pad, a slightly-late ``start`` or slightly-early ``end`` from the
    # aligner chops the attack/release off the word and the output sounds
    # like the voiceover is missing the first or last syllable.
    #
    # The caller is expected to clamp these pads to less than half the gap
    # to the neighbouring segment so we can't steal their word. If the
    # caller passes 0.0 for a side, slicing is tight on that side.
    cut_start = max(0.0, start - max(0.0, lead_pad))
    cut_end = end + max(0.0, tail_pad)

    cmd = [
        "ffmpeg", "-y",
        "-i", audio_path,
        "-ss", f"{cut_start:.3f}",
        "-to", f"{cut_end:.3f}",
        # Keep voice quality high: mono 48 kHz, 16-bit PCM. The previous
        # 16 kHz mono crushed VO files into telephone-grade audio before
        # anything downstream had a chance to touch them.
        "-ac", "1", "-ar", "48000",
    ]
    if clean_silences:
        # Trim the widened cut back to actual speech boundaries. Threshold
        # is configurable via `silence_db` (default -25 dB — the pipeline's
        # unified silence convention). RMS detection, 150 ms sustained: short
        # enough to catch the padding we add, long enough to ignore inter-
        # word gaps (~50–120 ms) so the trim never cuts between two words
        # inside the slice.
        #
        # After the trim we pad ~80 ms in front / ~120 ms after the speech
        # so the slice never starts/ends right on a phoneme.
        thr = f"{silence_db:g}dB"
        cmd += [
            "-af",
            (
                # 1. Strip leading silence longer than 150 ms.
                "silenceremove=start_periods=1:"
                f"start_silence=0.15:start_threshold={thr}:detection=rms,"
                # 2. Strip trailing silence (reverse trick).
                "areverse,"
                "silenceremove=start_periods=1:"
                f"start_silence=0.15:start_threshold={thr}:detection=rms,"
                "areverse,"
                # 3. Prepend 80 ms of silence (margin before first phoneme).
                "adelay=80|80,"
                # 4. Append 120 ms of silence (margin after last phoneme).
                "apad=pad_dur=0.12,"
                # 5. Tiny 10 ms fade-in to clean any edge click on the
                # leading boundary. The trailing boundary is already buffered
                # by the apad silence above, so no tail fade is needed —
                # and chaining `afade,areverse,afade,areverse` here was
                # silencing the entire clip on real voice input (PTS drift
                # after repeated areverse + silenceremove).
                "afade=t=in:st=0:d=0.01"
                # NOTE: loudness is handled once globally by
                # preprocess_voiceover before slicing — do NOT re-normalise
                # per slice, it would undo the match to the original bed.
            ),
        ]
    cmd += ["-acodec", "pcm_s16le", out_path]

    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # If silenceremove + loudnorm ate the whole slice (very short or fully
    # silent input), fall back to the raw cut so downstream `get_audio_duration`
    # has something to measure and the slice still occupies its slot.
    if clean_silences:
        try:
            from services.ffmpeg import get_audio_duration as _gad
            if _gad(out_path) <= 0.02:
                subprocess.run(
                    [
                        "ffmpeg", "-y",
                        "-i", audio_path,
                        "-ss", f"{max(0.0, start):.3f}",
                        "-to", f"{end:.3f}",
                        "-ac", "1", "-ar", "48000",
                        "-acodec", "pcm_s16le",
                        out_path,
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass

    return out_path


def align_voiceover_to_segments(
    vo_audio_path: str,
    segments: list[Segment],
    target_language: str,
) -> list[tuple[float, float]]:
    """Locate, inside `vo_audio_path`, the time range that corresponds to each
    of `segments` (which already carry the target-language `translated` text).

    Returns a list of (audio_start, audio_end) one per segment, in seconds.

    Implementation: a single multimodal LLM call that's given the audio
    file plus the list of script chunks. The model transcribes / forced-aligns
    internally and returns per-chunk timestamps. The script and the actual
    spoken audio don't have to match exactly — the model is told to be tolerant
    of minor wording differences.
    """
    if not segments:
        return []

    chunks_payload = [
        {"index": i, "text": (s.translated or s.original or "")[:600]}
        for i, s in enumerate(segments)
    ]

    prompt = f"""You are aligning a list of pre-translated script chunks to a
voiceover audio recording in {target_language}.

The recording reads ALL of the script chunks below in order. The wording
in the recording may differ slightly from the written script (paraphrasing,
filler words, omissions) — be tolerant.

SCRIPT CHUNKS (each is one segment of dialogue, in order):
{json.dumps(chunks_payload, ensure_ascii=False)}

For each chunk, find the time range inside the audio file where it is spoken.

Return ONLY a JSON object of the form:
{{"chunks": [{{"index": <int>, "start": <seconds float>, "end": <seconds float>}}, ...]}}

Rules:
- One entry per chunk, in the same order, even if you have to estimate.
- Timestamps must be monotonic (each entry's start >= previous entry's end).
- Cover the whole audio: the first entry's start should be near 0, the last
  entry's end should be near the audio's full duration.
- No markdown, no code fences."""

    raw = generate([prompt, vo_audio_path])
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"voiceover alignment: bad JSON from the LLM: {e}; raw={raw[:300]}")

    items = data.get("chunks", [])
    by_index: dict[int, tuple[float, float]] = {}
    last_end = 0.0
    for it in items:
        try:
            i = int(it["index"])
            s = float(it["start"])
            e = float(it["end"])
        except Exception:
            continue
        s = max(s, last_end)
        if e <= s:
            e = s + 0.05
        by_index[i] = (s, e)
        last_end = e

    # Fill any gaps with proportional fallback so we always return N entries.
    out: list[tuple[float, float]] = []
    for i in range(len(segments)):
        if i in by_index:
            out.append(by_index[i])
        else:
            # Linear interpolation between the previous and next known.
            prev = out[-1] if out else (0.0, 0.0)
            out.append((prev[1], prev[1] + 0.5))
    return out


def transcribe_voiceover(audio_path: str, target_language: str) -> str:
    """Plain LLM transcription of an uploaded VO file. Returned as a single
    string of paragraphs in `target_language`. Used as the script text when
    the user uploaded audio without an accompanying .docx section.
    """
    prompt = (
        f"Transcribe this audio in {target_language}. Return ONLY the spoken "
        "text as plain paragraphs separated by blank lines. No timestamps, no "
        "speaker labels, no markdown."
    )
    raw = generate([prompt, audio_path])
    return (raw or "").strip()
