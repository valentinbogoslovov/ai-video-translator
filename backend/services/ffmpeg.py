from __future__ import annotations
import subprocess
import json
import os
from typing import List

# ffmpeg / ffprobe must be on PATH and built with libass (subtitle burn-in).


def get_video_info(video_path: str) -> dict:
    """Get video resolution and duration using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", video_path
        ],
        capture_output=True, text=True, check=True
    )
    data = json.loads(result.stdout)
    video_stream = next(s for s in data["streams"] if s["codec_type"] == "video")
    # r_frame_rate is a fraction string like "30000/1001". Keep it as both
    # the original fraction (for ffmpeg, exact) and a float (convenience).
    fps_str = video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate") or "30/1"
    try:
        num, den = fps_str.split("/")
        fps_float = float(num) / float(den) if float(den) != 0 else 30.0
    except Exception:
        fps_float = 30.0
        fps_str = "30/1"
    return {
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "duration": float(data["format"]["duration"]),
        "fps": fps_float,
        "fps_str": fps_str,
    }


def extract_audio(video_path: str, output_path: str) -> str:
    """Extract audio from video as 16kHz mono WAV."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            output_path
        ],
        capture_output=True, check=True
    )
    return output_path


def replace_audio(video_path: str, audio_path: str, output_path: str) -> str:
    """Replace video audio track with new audio.

    Audio is encoded as AAC at 256 kbps / 48 kHz so the final mp4 doesn't
    bottleneck whatever the upstream pipeline produced.
    """
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path, "-i", audio_path,
            "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
            "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
            "-shortest", output_path
        ],
        capture_output=True, check=True
    )
    return output_path


def mix_voice_with_accompaniment(
    voice_wav: str,
    accompaniment_wav: str,
    output_wav: str,
    voice_gain_db: float = 0.0,
    music_gain_db: float = -3.0,
    duck_db: float = -9.0,
) -> str:
    """Layer the dubbed voice over the original accompaniment with sidechain
    ducking. Whenever the voice is speaking, the music drops by ``duck_db``;
    between segments it returns to ``music_gain_db``.

    Output is a stereo WAV at 44.1 kHz.
    """
    # sidechaincompress: voice triggers compression of the music bus.
    # threshold low + ratio high → effective ducking; attack/release tuned
    # to match speech envelopes (5 ms / 250 ms).
    filter_complex = (
        f"[0:a]volume={voice_gain_db}dB,aresample=44100,aformat=channel_layouts=stereo[voice];"
        f"[1:a]volume={music_gain_db}dB,aresample=44100,aformat=channel_layouts=stereo[music_pre];"
        f"[music_pre][voice]sidechaincompress="
        f"threshold=0.05:ratio=8:attack=5:release=250:makeup=1[music_ducked];"
        f"[voice][music_ducked]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[out]"
    )
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", voice_wav,
            "-i", accompaniment_wav,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:a", "pcm_s16le",
            output_wav,
        ],
        capture_output=True, check=True,
    )
    return output_wav


def burn_captions(video_path: str, ass_path: str, output_path: str, fonts_dir: str) -> str:
    """Burn ASS subtitles into video."""
    # ffmpeg ≥ 8 no longer accepts `\:` inside filter args. Use the named
    # `filename=` option and single-quote both paths so ':' / spaces are safe.
    def _q(p: str) -> str:
        return "'" + p.replace("\\", "\\\\").replace("'", "\\'") + "'"

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"ass=filename={_q(ass_path)}:fontsdir={_q(fonts_dir)}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:a", "copy", output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        raise RuntimeError(
            f"ffmpeg burn_captions failed (exit {proc.returncode}):\n{tail}"
        )
    return output_path


def build_atempo_chain(factor: float) -> str:
    """Build atempo filter chain for factors outside 0.5-2.0 range."""
    if abs(factor - 1.0) < 0.01:
        return ""
    filters = []
    f = factor
    while f > 2.0:
        filters.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        filters.append("atempo=0.5")
        f *= 2.0
    filters.append(f"atempo={f:.4f}")
    return ",".join(filters)


def stretch_audio_segment(input_path: str, output_path: str, factor: float) -> str:
    """Time-stretch an audio segment by the given factor."""
    chain = build_atempo_chain(factor)
    if not chain:
        # No stretching needed, just copy
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-c", "copy", output_path],
            capture_output=True, check=True
        )
    else:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-filter:a", chain,
                "-acodec", "pcm_s16le", output_path
            ],
            capture_output=True, check=True
        )
    return output_path


def extract_speaker_audio(audio_path: str, segments: list, speaker: str, output_path: str) -> str:
    """Extract and concatenate all audio clips for a specific speaker.

    Used to get a clean sample of each speaker's voice for cloning.
    """
    from pydub import AudioSegment

    full_audio = AudioSegment.from_wav(audio_path)
    speaker_audio = AudioSegment.silent(duration=0)

    for seg in segments:
        if seg.speaker == speaker:
            start_ms = int(seg.start * 1000)
            end_ms = int(seg.end * 1000)
            speaker_audio += full_audio[start_ms:end_ms]

    # Ensure at least 5 seconds for decent voice cloning
    if len(speaker_audio) < 5000:
        # Repeat what we have to get to 5s
        while len(speaker_audio) < 5000:
            speaker_audio += speaker_audio

    speaker_audio.export(output_path, format="wav")
    return output_path


def retime_video_by_segments(
    video_path: str,
    retimes: list[tuple[float, float, float]],
    video_duration: float,
    output_path: str,
    work_dir: str,
) -> str:
    """Stretch specific [orig_start, orig_end] windows of a video to a new
    duration, leaving the rest of the timeline untouched. Audio is dropped
    (the caller mux's a fresh track).

    `retimes` is a list of (orig_start, orig_end, new_duration). Windows must
    not overlap. Used when a translated voiceover is too long for its slot:
    instead of crushing the audio, the underlying scene is slowed down.
    """
    import shutil as _shutil

    if not retimes:
        # Nothing to do — just strip audio so caller can remux cleanly.
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-an", "-c:v", "copy", output_path],
            capture_output=True, check=True,
        )
        return output_path

    # Tile the full video with (start, end, new_dur) pieces; gaps stay 1:1.
    pieces: list[tuple[float, float, float]] = []
    cursor = 0.0
    for s, e, nd in sorted(retimes):
        if s > cursor + 0.001:
            pieces.append((cursor, s, s - cursor))
        pieces.append((s, e, nd))
        cursor = e
    if cursor < video_duration - 0.001:
        pieces.append((cursor, video_duration, video_duration - cursor))

    parts_dir = os.path.join(work_dir, "_retime_parts")
    os.makedirs(parts_dir, exist_ok=True)
    part_paths: list[str] = []

    for i, (s, e, nd) in enumerate(pieces):
        orig_dur = e - s
        if orig_dur <= 0.001 or nd <= 0.001:
            continue
        part_path = os.path.join(parts_dir, f"part_{i:04d}.mp4")
        ratio = nd / orig_dur  # >1 → slow down (longer presentation)
        vf = f"setpts={ratio:.6f}*PTS"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", f"{s:.3f}", "-to", f"{e:.3f}",
                "-i", video_path,
                "-an", "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                part_path,
            ],
            capture_output=True, check=True,
        )
        part_paths.append(part_path)

    list_file = os.path.join(parts_dir, "concat.txt")
    with open(list_file, "w") as f:
        for p in part_paths:
            f.write(f"file '{p}'\n")

    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_file, "-an", "-c:v", "copy", output_path,
        ],
        capture_output=True, check=True,
    )

    _shutil.rmtree(parts_dir, ignore_errors=True)
    return output_path


def get_audio_duration(audio_path: str) -> float:
    """Get duration of an audio file in seconds.

    Robust to ffprobe quirks: WAVs produced by silenceremove + loudnorm
    chains sometimes don't carry `format.duration`. We then fall back to the
    stream-level duration, then to `nb_samples / sample_rate`, and finally
    return 0.0 (caller treats this as "empty slice").
    """
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", audio_path,
        ],
        capture_output=True, text=True, check=True
    )
    data = json.loads(result.stdout)
    fmt = data.get("format") or {}
    if "duration" in fmt:
        try:
            return float(fmt["duration"])
        except (TypeError, ValueError):
            pass
    for stream in data.get("streams") or []:
        if stream.get("codec_type") != "audio":
            continue
        if "duration" in stream:
            try:
                return float(stream["duration"])
            except (TypeError, ValueError):
                pass
        nb = stream.get("nb_samples") or stream.get("duration_ts")
        sr = stream.get("sample_rate")
        if nb is not None and sr:
            try:
                return float(nb) / float(sr)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    return 0.0


