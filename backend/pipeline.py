from __future__ import annotations
import asyncio
import json
import os
import shutil
import time
from typing import Callable, Dict, List, Optional
from pydub import AudioSegment
from models import JobStatus, Segment, StepTiming, WordTiming
from services.ffmpeg import (
    extract_audio, get_video_info, get_audio_duration,
    stretch_audio_segment, replace_audio, burn_captions,
    extract_speaker_audio, retime_video_by_segments,
    mix_voice_with_accompaniment,
)
from services.audio_separation import separate_vocals

# Fit priority: video deforms first, audio only as a fallback.
#
#   MIN_VIDEO_PLAYBACK_RATE = 0.85  → shot may slow to at most 85 % of native
#                                     (orig_slot may grow to ≤1/0.85 ≈ 1.176×).
#   MAX_VIDEO_COMPRESS = inf        → shot may be sped up without limit
#                                     (orig_slot may shrink to any positive value).
#   MAX_AUDIO_SPEEDUP = 1.05        → audio time-stretch capped at +5 %.
#   MIN_AUDIO_SPEEDUP = 0.95        → audio time-stretch capped at −5 %.
#
# Asymmetric on purpose: a slowed shot reads as slow-motion past ~85 %
# playback, but a sped-up shot is invisible (FPS unchanged, frames
# re-paced), so we let the speed-up absorb any load. Audio stretch is
# capped tight either way because anything beyond ±5 % is audible.
MIN_VIDEO_PLAYBACK_RATE = 0.85
MAX_VIDEO_STRETCH = 1.0 / MIN_VIDEO_PLAYBACK_RATE   # ≈ 1.1765
MAX_VIDEO_COMPRESS = float("inf")
MAX_AUDIO_SPEEDUP = 1.05
MIN_AUDIO_SPEEDUP = 0.95
from services.llm_tasks import (
    transcribe_and_translate,
    align_script_to_segments,
    transcribe_with_word_timestamps,
)
from services.providers import (
    clone_voice,
    generate_tts_segment_with_timestamps,
    delete_voice,
    align_clips_parallel,
)
from services.captions import generate_ass_captions
from config import settings


# In-memory job store
jobs: dict[str, JobStatus] = {}

# Tracks live cleanup state for in-flight jobs so a shutdown / cancellation can
# wipe voices and working files even if the pipeline never reaches its `finally`.
# { job_id: {"voice_ids": set[str], "work_dir": str} }
active_sessions: dict[str, dict] = {}

# Maps job_id -> the asyncio.Task running its pipeline, so /api/cancel can stop it.
running_tasks: dict[str, "object"] = {}

# Cooperative-cancellation flag set. /api/cancel adds the job_id here AND
# calls task.cancel(); the pipeline polls this set at every coarse step so
# synchronous sections (BiRefNet matting, ffmpeg, demucs, time.sleep retries)
# can bail out without waiting for an `await` point.
cancelled_jobs: set[str] = set()

# Serial job queue — only one pipeline runs at a time.
_job_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None


def _get_queue() -> asyncio.Queue:
    global _job_queue
    if _job_queue is None:
        _job_queue = asyncio.Queue()
    return _job_queue


async def _queue_worker():
    """Process jobs one at a time from the queue."""
    q = _get_queue()
    while True:
        item = await q.get()
        job_id = item["job_id"]
        try:
            # Skip if already cancelled while queued.
            if job_id in cancelled_jobs:
                if job_id in jobs:
                    jobs[job_id].status = "error"
                    jobs[job_id].error = "Cancelled"
                continue

            # Transition from queued → processing.
            if job_id in jobs:
                jobs[job_id].status = "processing"
                jobs[job_id].current_step = "Starting..."

            fn = item.get("fn") or run_pipeline
            await fn(**item["kwargs"])
        except Exception:
            pass  # pipeline_fn handles its own errors
        finally:
            running_tasks.pop(job_id, None)
            q.task_done()


def ensure_worker():
    """Start the queue worker if it isn't running yet."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_queue_worker())


def enqueue_job(job_id: str, pipeline_fn=None, **pipeline_kwargs):
    """Add a job to the serial queue. `pipeline_fn` defaults to
    `run_pipeline` (full translate). Pass `run_background_swap` to run
    the background-only mode instead."""
    ensure_worker()
    pipeline_kwargs["job_id"] = job_id
    _get_queue().put_nowait({
        "job_id": job_id,
        "kwargs": pipeline_kwargs,
        "fn": pipeline_fn,
    })


async def _run_sync(fn, *args, **kwargs):
    """Run a blocking function in a thread so the event loop stays responsive."""
    loop = asyncio.get_event_loop()
    import functools
    call = functools.partial(fn, *args, **kwargs)
    return await loop.run_in_executor(None, call)


class JobCancelled(Exception):
    """Raised inside the pipeline when /api/cancel was called for this job."""


def check_cancelled(job_id: str):
    if job_id in cancelled_jobs:
        raise JobCancelled()


def cleanup_session(job_id: str, *, remove_files: bool = True):
    """Delete cloned voices and (optionally) the working directory for a job."""
    sess = active_sessions.pop(job_id, None)
    if not sess:
        return
    for voice_id in sess.get("voice_ids", set()):
        try:
            delete_voice(voice_id)
        except Exception:
            pass
    if remove_files:
        work_dir = sess.get("work_dir")
        if work_dir and os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


def cleanup_all_sessions():
    """Best-effort cleanup of every tracked session. Called on shutdown."""
    for job_id in list(active_sessions.keys()):
        cleanup_session(job_id)


def update_job(job_id: str, **kwargs):
    if job_id in jobs:
        for k, v in kwargs.items():
            setattr(jobs[job_id], k, v)


# Progress weights — must sum to 1.0.
# Prep covers everything before per-language work; per_lang is split per language.
PROGRESS_PREP_SHARE = 0.10
PROGRESS_LANG_SHARE = 0.90
# Sub-step weights inside a single language (must sum to 1.0).
LANG_SUBSTEPS = [
    ("translate", 0.30),  # script align OR transcribe+translate
    ("tts",       0.35),  # generate per-segment TTS
    ("sync",      0.10),  # fit to timeline
    ("replace",   0.10),  # mux audio
    ("captions",  0.05),  # build .ass
    ("burn",      0.10),  # ffmpeg burn-in
]


# Per-job step timing state, kept out-of-band so JobStatus stays serializable.
# { job_id: {"key": str, "label": str, "started_at": float} }
_step_state: dict[str, dict] = {}


def _set_progress(
    job_id: str,
    step_title: str,
    pct: float,
    *,
    step_key: Optional[str] = None,
    **extra,
):
    """Update job progress.

    `step_key` is a stable identifier for the *step*, while `step_title` is the
    human label (which may change every tick to show a counter like "(3/120)").
    When `step_key` changes, the previous step's elapsed time is appended to
    `step_history` and a new timer starts.
    """
    pct = max(0.0, min(100.0, pct))
    job = jobs.get(job_id)
    if job is None:
        return

    key = step_key or step_title
    now = time.time()
    state = _step_state.get(job_id)

    if state is None or state["key"] != key:
        # Close out the previous step.
        if state is not None:
            elapsed = max(0.0, now - state["started_at"])
            job.step_history = list(job.step_history) + [
                StepTiming(name=state["label"], seconds=round(elapsed, 2))
            ]
        _step_state[job_id] = {"key": key, "label": step_title, "started_at": now}
        job.current_step_started_at = now
    else:
        # Same step — refresh the displayed label (e.g. updated counter).
        state["label"] = step_title

    update_job(job_id, current_step=step_title, progress=pct, **extra)


def _finalize_steps(job_id: str):
    """Close the currently-active step (if any). Call once at end of pipeline."""
    state = _step_state.pop(job_id, None)
    job = jobs.get(job_id)
    if state is None or job is None:
        return
    elapsed = max(0.0, time.time() - state["started_at"])
    job.step_history = list(job.step_history) + [
        StepTiming(name=state["label"], seconds=round(elapsed, 2))
    ]


def _lang_progress(lang_idx: int, total_langs: int, substep_key: str, frac_within: float = 1.0) -> float:
    """Return overall percent after `substep_key` of language `lang_idx` is `frac_within` complete."""
    prep = PROGRESS_PREP_SHARE * 100
    per_lang = (PROGRESS_LANG_SHARE * 100) / max(total_langs, 1)
    finished_subs = 0.0
    for key, weight in LANG_SUBSTEPS:
        if key == substep_key:
            finished_subs += weight * frac_within
            break
        finished_subs += weight
    return prep + per_lang * (lang_idx + finished_subs)


def get_unique_speakers(segments: list[Segment]) -> list[str]:
    """Get list of unique speakers in order of first appearance."""
    seen = set()
    speakers = []
    for seg in segments:
        if seg.speaker not in seen:
            seen.add(seg.speaker)
            speakers.append(seg.speaker)
    return speakers


def merge_minor_speakers(
    segments: list[Segment],
    min_share: float = 0.08,
    min_chars: int = 80,
) -> list[Segment]:
    """Mutate `segments` so any speaker contributing less than `min_share` of total
    speaking time AND fewer than `min_chars` of dialogue is reassigned to the
    speaker that talks immediately around them.

    This kills the transcriber's stray "Speaker N" hallucinations on a single short segment
    without forcing a hard cap (so a video with 5 real speakers still works).
    """
    if not segments:
        return segments

    # Tally per-speaker totals.
    durations: dict[str, float] = {}
    char_counts: dict[str, int] = {}
    for s in segments:
        durations[s.speaker] = durations.get(s.speaker, 0.0) + max(0.0, s.end - s.start)
        char_counts[s.speaker] = char_counts.get(s.speaker, 0) + len(s.original or "")
    total_dur = sum(durations.values()) or 1.0

    minors = {
        spk for spk in durations
        if (durations[spk] / total_dur) < min_share and char_counts.get(spk, 0) < min_chars
    }
    if not minors or len(durations) - len(minors) < 1:
        return segments

    # Reassign each minor segment to its nearest non-minor neighbour.
    for i, seg in enumerate(segments):
        if seg.speaker not in minors:
            continue
        prev_spk = next(
            (segments[j].speaker for j in range(i - 1, -1, -1)
             if segments[j].speaker not in minors),
            None,
        )
        next_spk = next(
            (segments[j].speaker for j in range(i + 1, len(segments))
             if segments[j].speaker not in minors),
            None,
        )
        seg.speaker = prev_spk or next_spk or seg.speaker
    return segments


def fit_segments_to_timeline(
    tts_paths: list[str],
    segments: list[Segment],
    video_duration: float,
    work_dir: str,
    output_path: str,
    tts_word_timings: list[list[dict]] | None = None,
) -> tuple[list[Segment], list[tuple[float, float, float]], float]:
    """Speed-adjust each TTS segment to fit its time slot, then concatenate.

    When TTS would need to be sped up beyond `MAX_AUDIO_SPEEDUP`, the segment's
    slot is grown to keep audio at the cap; the caller stretches the underlying
    video scene by the same amount so picture and voice stay in sync.

    Returns:
        fitted_segments: segments with timings on the NEW (possibly stretched) timeline
        retimes: list of (orig_start, orig_end, new_duration) for video stretching
        new_video_duration: total duration of the new audio/video timeline
    """
    # Keep the mix at studio quality (48 kHz mono). pydub's default silent()
    # is 11025 Hz, which crushes everything that gets appended down to that.
    TARGET_SR = 48000
    combined = AudioSegment.silent(duration=0, frame_rate=TARGET_SR)
    fitted_segments: list[Segment] = []
    retimes: list[tuple[float, float, float]] = []
    time_offset = 0.0  # cumulative shift introduced by stretched scenes

    for i, (seg, tts_path) in enumerate(zip(segments, tts_paths)):
        orig_target = seg.end - seg.start
        tts_duration = get_audio_duration(tts_path)

        # Fit-priority decision (per segment):
        #
        # Audio LONGER than slot (ratio > 1):
        #   • Slow the video to absorb up to +10 %.
        #   • If still longer, speed the audio up by ≤5 %.
        #   • Anything beyond ratio == 1.10 × 1.05 ≈ 1.155 will get clipped
        #     to the slot at the end of this loop.
        #
        # Audio SHORTER than slot (ratio < 1):
        #   • Speed the video up to match the audio duration EXACTLY.
        #     Video has no compression cap, so audio always plays at native.
        if orig_target <= 0 or tts_duration <= 0:
            new_target = orig_target
        else:
            ratio = tts_duration / orig_target
            if ratio > 1.0:
                # Audio is longer. First grow the slot using the video stretch
                # budget, capped at MAX_VIDEO_STRETCH.
                grown = min(tts_duration, orig_target * MAX_VIDEO_STRETCH)
                new_target = grown
                retimes.append((seg.start, seg.end, new_target))
                # If even max stretch isn't enough, audio speeds up the rest
                # (capped by MAX_AUDIO_SPEEDUP at the factor calc below).
            elif ratio < 1.0:
                # Audio is shorter. Speed the video up to match — no cap.
                new_target = tts_duration
                retimes.append((seg.start, seg.end, new_target))
            else:
                new_target = orig_target

        # Gap before this segment (preserved on the original timeline; the
        # cumulative offset is applied via time_offset below).
        if i == 0:
            gap = seg.start
        else:
            gap = seg.start - segments[i - 1].end
        if gap > 0:
            combined += AudioSegment.silent(duration=int(gap * 1000), frame_rate=TARGET_SR)

        # Speed-adjust to fit the (possibly grown/shrunk) slot, then clamp
        # the audio time-stretch to ±5 %. Anything beyond that is left for
        # the trim/pad pass below.
        if tts_duration > 0 and new_target > 0:
            factor = tts_duration / new_target
            factor = max(MIN_AUDIO_SPEEDUP, min(MAX_AUDIO_SPEEDUP, factor))
        else:
            factor = 1.0

        stretched_path = os.path.join(work_dir, f"fitted_{i:04d}.wav")
        stretch_audio_segment(tts_path, stretched_path, factor)
        stretched_audio = AudioSegment.from_wav(stretched_path)
        # Force every appended slice to the target rate so concat doesn't
        # silently downmix everything to the lower of the two sample rates.
        if stretched_audio.frame_rate != TARGET_SR:
            stretched_audio = stretched_audio.set_frame_rate(TARGET_SR)
        if stretched_audio.channels != 1:
            stretched_audio = stretched_audio.set_channels(1)

        target_ms = int(new_target * 1000)
        # NEVER hard-trim the audio — that cuts words off the end. If we
        # still overshoot after the speed-fit + 5 % cap, grow the slot to
        # contain the whole utterance and let the video stretch absorb it.
        if len(stretched_audio) > target_ms:
            extra_ms = len(stretched_audio) - target_ms
            new_target = len(stretched_audio) / 1000.0
            target_ms = len(stretched_audio)
            # Mark this segment as needing extra video stretch beyond the
            # original 10 % budget — we'd rather have a slightly slowed
            # shot than missing words.
            if retimes and retimes[-1][0] == seg.start:
                retimes[-1] = (seg.start, seg.end, new_target)
            else:
                retimes.append((seg.start, seg.end, new_target))
        elif len(stretched_audio) < target_ms:
            stretched_audio += AudioSegment.silent(
                duration=target_ms - len(stretched_audio), frame_rate=TARGET_SR
            )

        combined += stretched_audio

        try:
            os.remove(stretched_path)
        except OSError:
            pass

        # Segment timings on the NEW timeline.
        new_start = seg.start + time_offset
        new_end = new_start + new_target
        time_offset += (new_target - orig_target)

        # Word timings: prefer the per-character alignment that
        # the TTS provider returned alongside the TTS audio (frame-accurate,
        # no extra API call). Each TTS-word's `start`/`end` is in the
        # native (un-stretched) clip time; we scale by the same factor
        # the audio just went through (`atempo`) and offset by
        # `new_start` to land on the merged timeline. When timings are
        # missing (the timestamp endpoint failed) we fall back to even
        # spacing across the slot.
        seg_tts_words = (
            tts_word_timings[i]
            if tts_word_timings and i < len(tts_word_timings)
            else None
        )
        new_words: list[WordTiming] = []
        if seg_tts_words:
            stretch_inv = 1.0 / max(0.001, factor)  # factor = tts/new_target; inv = new_target/tts
            min_word_len = 0.02  # 20 ms floor so zero-length words don't break captions
            for w in seg_tts_words:
                ws = new_start + float(w["start"]) * stretch_inv
                we = new_start + float(w["end"]) * stretch_inv
                ws = max(new_start, min(new_end - min_word_len, ws))
                we = max(ws + min_word_len, min(new_end, we))
                new_words.append(WordTiming(
                    word=w["word"],
                    start=round(ws, 3),
                    end=round(we, 3),
                ))
        else:
            translated_words = seg.translated.split()
            if translated_words:
                word_dur = new_target / len(translated_words)
                for j, w in enumerate(translated_words):
                    new_words.append(WordTiming(
                        word=w,
                        start=round(new_start + j * word_dur, 3),
                        end=round(new_start + (j + 1) * word_dur, 3),
                    ))

        fitted_segments.append(Segment(
            start=round(new_start, 3),
            end=round(new_end, 3),
            original=seg.original,
            translated=seg.translated,
            words=new_words,
            speaker=seg.speaker,
            original_words=seg.original_words,
        ))

    new_video_duration = video_duration + time_offset

    # If the combined audio is well short of new_video_duration, the
    # transcription likely dropped the tail of the source (the model sometimes
    # truncates long-output JSON) — or the original ended in silence we
    # don't have audio for. Either way, padding the rest with dead silence
    # would leave a quiet tail in the burned video that the user would
    # see as "voiceover ends early". Clip new_video_duration to the
    # actual audio length so replace_audio's `-shortest` flag trims the
    # video to match. Small gaps (<0.5s) we still pad — they're natural
    # breathing room from segment timing rounding.
    combined_duration = len(combined) / 1000.0
    tail_gap = new_video_duration - combined_duration
    if tail_gap > 0.5:
        print(
            f"[pipeline] audio ends {tail_gap:.1f}s before video would "
            f"({combined_duration:.1f}s vs {new_video_duration:.1f}s) — "
            "trimming video to match audio (likely transcription tail loss)."
        )
        new_video_duration = combined_duration

    current_ms = len(combined)
    target_ms = int(new_video_duration * 1000)
    if current_ms < target_ms:
        combined += AudioSegment.silent(duration=target_ms - current_ms, frame_rate=TARGET_SR)
    elif current_ms > target_ms:
        combined = combined[:target_ms]

    # Export at 48 kHz / 16-bit. pydub honours frame_rate from the segment;
    # set bytes-per-sample explicitly so the file is studio-grade WAV.
    combined = combined.set_frame_rate(TARGET_SR).set_sample_width(2)
    combined.export(output_path, format="wav")
    return fitted_segments, retimes, new_video_duration


async def run_pipeline(
    job_id: str,
    video_path: str,
    languages: list[str],
    video_name: str,
    scripts: dict[str, str] | None = None,
    caption_scripts: dict[str, str] | None = None,
    caption_box: dict | None = None,
    background_urls: dict[str, str] | None = None,
    caption_style: dict | None = None,
    voiceover_audio: dict[str, str] | None = None,
):
    """Run the full translation pipeline for all requested languages.

    `scripts` is an optional `{lang_code: pre_translated_text}` mapping. When a
    language is present in `scripts`, the pre-translated script is aligned to
    the video segments instead of running machine translation, and captions
    are sourced from the script as well.
    """
    scripts = scripts or {}
    voiceover_audio = voiceover_audio or {}
    work_dir = os.path.join(settings.upload_dir, job_id)
    os.makedirs(work_dir, exist_ok=True)

    voice_ids: dict[str, str] = {}  # speaker -> voice_id
    active_sessions[job_id] = {"voice_ids": set(), "work_dir": work_dir}
    try:
        total_langs = len(languages)

        background_urls = background_urls or {}

        check_cancelled(job_id)
        # Step 1: Get video info
        _set_progress(job_id, "Analyzing video...", 1, step_key="analyze")
        info = await _run_sync(get_video_info, video_path)
        video_duration = info["duration"]

        # Step 2: Extract audio
        _set_progress(job_id, "Extracting audio...", 3, step_key="extract_audio")
        audio_path = os.path.join(work_dir, "original_audio.wav")
        await _run_sync(extract_audio, video_path, audio_path)

        # Step 2b: Separate vocals from music/SFX so we can preserve the
        # original soundtrack under the new dub. Best-effort: if Demucs fails
        # (e.g. very short clip), we just continue without an accompaniment.
        accompaniment_path: Optional[str] = None
        try:
            _set_progress(
                job_id, "Separating music from vocals...", 4, step_key="separate_audio"
            )
            stems_dir = os.path.join(work_dir, "stems")
            _vocals_path, accompaniment_path = await _run_sync(separate_vocals, audio_path, stems_dir)
        except Exception as e:
            print(f"[pipeline] demucs separation failed: {e} — proceeding without music bed")
            accompaniment_path = None

        # Step 3: Transcribe first to identify speakers
        _set_progress(job_id, "Identifying speakers...", 5, step_key="transcribe")
        initial_segments = await transcribe_and_translate(audio_path, languages[0])
        # Collapse hallucinated minor speakers into their neighbours.
        merge_minor_speakers(initial_segments)
        speakers = get_unique_speakers(initial_segments)

        # Skip voice cloning entirely if every requested language has a
        # pre-recorded voiceover — we won't be calling TTS at all.
        any_needs_tts = any(l not in voiceover_audio for l in languages)
        if any_needs_tts:
            _set_progress(job_id, f"Found {len(speakers)} speakers, cloning voices...", 7, step_key="clone_voices")
            # Step 4: Clone each speaker's voice
            n_speakers = len(speakers)
            for i, speaker in enumerate(speakers, start=1):
                check_cancelled(job_id)
                speaker_audio_path = os.path.join(work_dir, f"speaker_{speaker.replace(' ', '_')}.wav")
                _set_progress(job_id, f"Extracting sample for {speaker} ({i}/{n_speakers})...", 7, step_key="clone_voices")
                await _run_sync(extract_speaker_audio, audio_path, initial_segments, speaker, speaker_audio_path)
                _set_progress(job_id, f"Cloning {speaker} ({i}/{n_speakers})...", 7, step_key="clone_voices")
                voice_id = await _run_sync(clone_voice, speaker_audio_path, f"{video_name}_{speaker.replace(' ', '_')}")
                voice_ids[speaker] = voice_id
                active_sessions[job_id]["voice_ids"].add(voice_id)
                try:
                    os.remove(speaker_audio_path)
                except OSError:
                    pass

        # --- Analysis pass: precompute the speaker safe-zone trajectory ---
        # We do this ONCE up front, before any per-language work, so the
        # matting in replace_background uses a stable, smoothed bbox per
        # frame instead of re-detecting (and occasionally flickering) each
        # time. Only worth running if at least one language asks for a
        # background swap.
        precomputed_safe_zones = None
        if background_urls:
            try:
                from services.background import precompute_safe_zones
                check_cancelled(job_id)
                _set_progress(job_id, "Locking on speaker...", 9, step_key="lock_speaker")
                precomputed_safe_zones = await _run_sync(
                    precompute_safe_zones,
                    video_path,
                    progress_cb=lambda f, label: _set_progress(
                        job_id, label, 9 + f * 0.5, step_key="lock_speaker"
                    ),
                    cancel_check=lambda: check_cancelled(job_id),
                )
            except Exception as e:
                print(f"[pipeline] safe-zone precompute failed: {e} — falling back to per-frame")
                precomputed_safe_zones = None

        # Process each language
        for lang_idx, lang in enumerate(languages):
            check_cancelled(job_id)
            lang_dir = os.path.join(work_dir, lang)
            os.makedirs(lang_dir, exist_ok=True)

            # Decide the source of segments for this language:
            #  - script provided                      -> align script to initial_segments (no MT)
            #  - voiceover provided without script    -> transcribe VO, use as script
            #  - first language without either        -> reuse initial transcription
            #  - otherwise                            -> re-transcribe & translate
            lang_name = lang.upper()
            # Earlier we also transcribed uploaded VOs here and injected the
            # transcript into `scripts` so the script-alignment path would
            # handle them. That was wrong: align_script_to_segments forces
            # the text to fit `initial_segments` (first-language timings),
            # which produced garbled chunks for non-first languages whose
            # sentence structure didn't match. Downstream VO alignment then
            # fell back to 0.5 s per segment and the output collapsed to a
            # few seconds of disjointed audio. VO-without-script now falls
            # through to the plain `transcribe_and_translate` path below,
            # which produces properly-structured per-language segments that
            # align_voiceover_to_segments can actually match against the VO.
            if lang in scripts and scripts[lang]:
                _set_progress(
                    job_id,
                    f"Aligning script for {lang_name}...",
                    _lang_progress(lang_idx, total_langs, "translate", 0.0),
                    step_key=f"translate:{lang}",
                    current_language=lang,
                )
                segments, warns = await align_script_to_segments(
                    scripts[lang], initial_segments, lang,
                )
                for w in warns:
                    print(f"[script:{lang}] WARN {w}")
            elif lang_idx == 0:
                _set_progress(
                    job_id,
                    f"Preparing {lang_name}...",
                    _lang_progress(lang_idx, total_langs, "translate", 0.0),
                    step_key=f"translate:{lang}",
                    current_language=lang,
                )
                segments = initial_segments
            else:
                _set_progress(
                    job_id,
                    f"Translating to {lang_name}...",
                    _lang_progress(lang_idx, total_langs, "translate", 0.0),
                    step_key=f"translate:{lang}",
                    current_language=lang,
                )
                segments = await transcribe_and_translate(audio_path, lang)

            # Original-video word timings (second base, alongside the VO
            # word timings produced later for the dub). One LLM call per
            # language, bucketed into each segment's [start, end] window by
            # word midpoint. Persisted to the manifest for future cadence-
            # diff features; not consumed by caption rendering itself.
            try:
                orig_words = await _run_sync(
                    transcribe_with_word_timestamps, audio_path, lang,
                )
                if orig_words:
                    seg_bounds = [(float(s.start), float(s.end)) for s in segments]
                    buckets: list[list[WordTiming]] = [[] for _ in segments]
                    j = 0
                    for w in orig_words:
                        ws = float(w["start"]); we = float(w["end"])
                        mid = (ws + we) / 2.0
                        while j < len(seg_bounds) and mid > seg_bounds[j][1]:
                            j += 1
                        if j >= len(seg_bounds):
                            break
                        if mid < seg_bounds[j][0]:
                            continue
                        buckets[j].append(WordTiming(
                            word=str(w["word"]),
                            start=round(ws, 3),
                            end=round(we, 3),
                        ))
                    for s, bucket in zip(segments, buckets):
                        s.original_words = bucket
            except Exception as e:
                print(f"[pipeline] original-word timing failed for {lang}: {e} — skipping")

            # Step 5: Either slice the user's pre-recorded voiceover OR
            # generate per-segment TTS via the provider.
            n_segs = max(len(segments), 1)
            tts_word_timings: list[list[dict]] = []
            if lang in voiceover_audio:
                from services.voiceover import (
                    align_voiceover_to_segments,
                    preprocess_voiceover,
                    slice_audio,
                )
                _set_progress(
                    job_id,
                    f"Preparing voiceover for {lang_name}...",
                    _lang_progress(lang_idx, total_langs, "tts", 0.0),
                    step_key=f"tts:{lang}",
                )
                vo_path = voiceover_audio[lang]
                # Normalise channel layout to dual-mono stereo and match
                # loudness to the original video's audio bed. Falls back to
                # the raw file if anything in the preprocess chain errors.
                try:
                    pre_path = os.path.join(lang_dir, f"vo_preprocessed.wav")
                    vo_path = preprocess_voiceover(vo_path, audio_path, pre_path)
                except Exception as e:
                    print(
                        f"[pipeline] VO preprocess failed for {lang}: {e} "
                        f"— using uploaded file as-is"
                    )
                _set_progress(
                    job_id,
                    f"Aligning voiceover for {lang_name}...",
                    _lang_progress(lang_idx, total_langs, "tts", 0.0),
                    step_key=f"tts:{lang}",
                )
                vo_ranges = align_voiceover_to_segments(vo_path, segments, lang)
                # LLM alignment is only accurate to ~100–200 ms; without
                # a pad on each side the raw cut chops word attacks/releases.
                # We widen up to WIDEN_MAX per side but cap each pad at half
                # the gap to the neighbouring segment so widening can't steal
                # their word. A zero-length gap (back-to-back segments)
                # yields a zero pad on that side — the aligned boundary is
                # taken as authoritative there.
                WIDEN_MAX = 0.25
                tts_paths = []
                for i, (vs, ve) in enumerate(vo_ranges):
                    check_cancelled(job_id)
                    prev_end = vo_ranges[i - 1][1] if i > 0 else 0.0
                    next_start = (
                        vo_ranges[i + 1][0]
                        if i + 1 < len(vo_ranges)
                        else float("inf")
                    )
                    lead_pad = min(WIDEN_MAX, max(0.0, (vs - prev_end) / 2))
                    tail_pad = min(WIDEN_MAX, max(0.0, (next_start - ve) / 2))
                    out_path = os.path.join(lang_dir, f"vo_{i:04d}.wav")
                    slice_audio(
                        vo_path, vs, ve, out_path,
                        lead_pad=lead_pad, tail_pad=tail_pad,
                    )
                    tts_paths.append(out_path)
                    _set_progress(
                        job_id,
                        f"Slicing voiceover for {lang_name} ({i + 1}/{n_segs})...",
                        _lang_progress(lang_idx, total_langs, "tts", (i + 1) / n_segs),
                        step_key=f"tts:{lang}",
                    )
                # Forced-align each VO slice in parallel so the upload path
                # produces per-word timings up front — identical shape to
                # what the TTS branch returns. These ride
                # through fit_segments_to_timeline's atempo scaling and
                # land on the final timeline directly.
                _set_progress(
                    job_id,
                    f"Timestamping voiceover for {lang_name}...",
                    _lang_progress(lang_idx, total_langs, "tts", 1.0),
                    step_key=f"tts:{lang}",
                )
                expected = [
                    (tts_paths[i], segments[i].translated or segments[i].original)
                    for i in range(len(tts_paths))
                ]
                tts_word_timings = await align_clips_parallel(expected)
            else:
                _set_progress(
                    job_id,
                    f"Generating voices for {lang_name}...",
                    _lang_progress(lang_idx, total_langs, "tts", 0.0),
                    step_key=f"tts:{lang}",
                )
                tts_paths = []
                # generate_tts_segment_with_timestamps gives us exact
                # per-character (and thus per-word) timings of the
                # synthesized speech. Empty inner lists mean "fall back
                # to even spacing for this segment" (a failed
                # timestamped call).
                for i, seg in enumerate(segments):
                    check_cancelled(job_id)
                    out_path = os.path.join(lang_dir, f"tts_{i:04d}.wav")
                    speaker_voice = voice_ids.get(seg.speaker) or list(voice_ids.values())[0]
                    _, words = await _run_sync(
                        generate_tts_segment_with_timestamps,
                        seg.translated, speaker_voice, out_path,
                    )
                    tts_paths.append(out_path)
                    tts_word_timings.append(words)
                    _set_progress(
                        job_id,
                        f"Generating voices for {lang_name} ({i + 1}/{n_segs})...",
                        _lang_progress(lang_idx, total_langs, "tts", (i + 1) / n_segs),
                        step_key=f"tts:{lang}",
                    )

            # Step 6: Fit each segment to its exact time slot
            _set_progress(
                job_id,
                f"Syncing to scenes for {lang_name}...",
                _lang_progress(lang_idx, total_langs, "sync", 0.0),
                step_key=f"sync:{lang}",
            )
            synced_audio = os.path.join(lang_dir, "synced_audio.wav")
            synced_segments, video_retimes, new_video_duration = await _run_sync(
                fit_segments_to_timeline,
                tts_paths, segments, video_duration, lang_dir, synced_audio,
                tts_word_timings,
            )

            # Optional: replace the visual background with a per-language blog URL
            # before the audio mux. Pure pre-step for THIS language only — does
            # not touch the original video_path used by other languages.
            lang_video_source = video_path
            if lang in background_urls:
                from services.background import replace_background

                def _bg_progress(frac: float, label: str, _li=lang_idx, _lang=lang):
                    _set_progress(
                        job_id,
                        f"{label} ({lang_name})",
                        _lang_progress(_li, total_langs, "replace", frac * 0.5),
                        step_key=f"background:{_lang}",
                    )

                bg_out = os.path.join(lang_dir, "with_new_bg.mp4")
                await _run_sync(
                    replace_background,
                    video_path=video_path,
                    blog_url=background_urls[lang],
                    out_path=bg_out,
                    work_dir=lang_dir,
                    progress_cb=_bg_progress,
                    segments=segments,
                    cancel_check=lambda _jid=job_id: check_cancelled(_jid),
                    safe_zones=precomputed_safe_zones,
                )
                lang_video_source = bg_out

            # Step 6b: Stretch any video scenes whose voiceover wouldn't fit at
            # the audio speed cap. Only runs when fit_segments_to_timeline
            # actually grew at least one slot.
            if video_retimes:
                retimed_video = os.path.join(lang_dir, "video_retimed.mp4")
                await _run_sync(
                    retime_video_by_segments,
                    lang_video_source,
                    video_retimes,
                    video_duration,
                    retimed_video,
                    lang_dir,
                )
                lang_video_source = retimed_video

            # Step 7: Mix dubbed voice over the original music/SFX bed (with
            # sidechain ducking) and then replace the video's audio track.
            _set_progress(
                job_id,
                f"Mixing music for {lang_name}..." if accompaniment_path else f"Replacing audio for {lang_name}...",
                _lang_progress(lang_idx, total_langs, "replace", 0.5 if lang in background_urls else 0.0),
                step_key=f"replace_audio:{lang}",
            )
            final_audio_path = synced_audio
            if accompaniment_path:
                try:
                    mixed_path = os.path.join(lang_dir, "synced_with_music.wav")
                    await _run_sync(
                        mix_voice_with_accompaniment,
                        synced_audio, accompaniment_path, mixed_path,
                    )
                    final_audio_path = mixed_path
                except Exception as e:
                    print(f"[pipeline] mix with accompaniment failed: {e} — using voice only")
                    final_audio_path = synced_audio
            video_no_captions = os.path.join(lang_dir, "video_no_captions.mp4")
            await _run_sync(replace_audio, lang_video_source, final_audio_path, video_no_captions)

            # Step 8 + 9: Generate + burn captions, unless the user disabled them.
            captions_enabled = True
            if isinstance(caption_style, dict) and caption_style.get("enabled") is False:
                captions_enabled = False

            # Caption source resolution. By default captions follow the
            # translation (synced_segments). If the user uploaded a
            # standalone caption script for this language, we map that
            # text onto the synced timeline to get caption_segments —
            # those become the source of truth for the burned captions.
            # Audio is unaffected; this only changes what the viewer
            # reads on screen.
            caption_segments = synced_segments
            caption_source = "translation"
            if (caption_scripts or {}).get(lang):
                try:
                    _set_progress(
                        job_id,
                        f"Mapping caption script for {lang_name}...",
                        _lang_progress(lang_idx, total_langs, "captions", 0.0),
                        step_key=f"captions:{lang}",
                    )
                    mapped, warns = await align_script_to_segments(
                        caption_scripts[lang], synced_segments, lang,
                    )
                    for w in warns:
                        print(f"[caption_script:{lang}] WARN {w}")
                    if mapped:
                        caption_segments = mapped
                        caption_source = "caption_script"
                    else:
                        print(f"[caption_script:{lang}] alignment produced no segments — falling back to translation text for captions")
                except Exception as e:
                    print(f"[caption_script:{lang}] alignment failed: {e} — falling back to translation text for captions")

            final_path = os.path.join(lang_dir, "final.mp4")
            if not captions_enabled:
                _set_progress(
                    job_id,
                    f"Finalising {lang_name} (no captions)...",
                    _lang_progress(lang_idx, total_langs, "burn", 0.0),
                    step_key=f"burn:{lang}",
                )
                # Just promote video_no_captions to final.mp4 (no re-encode).
                shutil.copyfile(video_no_captions, final_path)
            else:
                _set_progress(
                    job_id,
                    f"Generating captions for {lang_name}...",
                    _lang_progress(lang_idx, total_langs, "captions", 0.0),
                    step_key=f"captions:{lang}",
                )
                ass_path = os.path.join(lang_dir, "captions.ass")
                generate_ass_captions(
                    caption_segments, ass_path,
                    video_width=info["width"],
                    video_height=info["height"],
                    caption_box=caption_box,
                    caption_style=caption_style,
                )

                _set_progress(
                    job_id,
                    f"Burning captions for {lang_name}...",
                    _lang_progress(lang_idx, total_langs, "burn", 0.0),
                    step_key=f"burn:{lang}",
                )
                fonts_dir = os.path.dirname(settings.font_path)
                await _run_sync(burn_captions, video_no_captions, ass_path, final_path, fonts_dir)

            # Persist editor manifest. The post-translation Edit feature
            # reconstructs an Edit Decision List from this — per-segment
            # voice clips, the music bed, the (already-retimed) video,
            # plus the caption events and global style/box. We KEEP the
            # per-segment audio files (`tts_NNNN.wav` or `vo_NNNN.wav`)
            # and the music bed so the editor can stream them back.
            video_file = (
                "video_retimed.mp4"
                if os.path.exists(os.path.join(lang_dir, "video_retimed.mp4"))
                else "../original.mp4"
            )
            manifest = {
                "language": lang,
                "video_file": video_file,
                "music_file": "../stems/accompaniment.wav" if accompaniment_path else None,
                "video_duration": float(new_video_duration),
                "video_width": int(info["width"]),
                "video_height": int(info["height"]),
                "caption_box": caption_box or {},
                "caption_style": caption_style or {},
                # `segments` carry the AUDIO source — synced_segments, with
                # the translation text + audio file ref per segment. The
                # editor's voice track is built from these.
                "segments": [
                    {
                        "start": float(s.start),
                        "end": float(s.end),
                        "speaker": s.speaker,
                        "original": s.original,
                        "translated": s.translated,
                        "audio_file": (
                            os.path.basename(tts_paths[i]) if i < len(tts_paths) else None
                        ),
                        "words": [
                            {"word": w.word, "start": float(w.start), "end": float(w.end)}
                            for w in (s.words or [])
                        ],
                        "original_words": [
                            {"word": w.word, "start": float(w.start), "end": float(w.end)}
                            for w in (s.original_words or [])
                        ],
                    }
                    for i, s in enumerate(synced_segments)
                ],
                # `caption_segments` carry the CAPTION source. Identical to
                # segments by default; differs when the user supplied a
                # separate caption_script. The editor's caption track and
                # the regroup endpoint use these.
                "caption_source": caption_source,
                "caption_segments": [
                    {
                        "start": float(s.start),
                        "end": float(s.end),
                        "speaker": s.speaker,
                        "original": s.original,
                        "translated": s.translated,
                        "words": [
                            {"word": w.word, "start": float(w.start), "end": float(w.end)}
                            for w in (s.words or [])
                        ],
                    }
                    for s in caption_segments
                ],
            }
            with open(os.path.join(lang_dir, "manifest.json"), "w") as _mf:
                json.dump(manifest, _mf, indent=2)

            # Mark language as done
            done = jobs[job_id].languages_done + [lang]
            update_job(job_id, languages_done=done)

            # Cleanup intermediate composites — keep tts_NNNN.wav (or
            # vo_NNNN.wav) and the music bed for the Edit feature.
            for f in [synced_audio, video_no_captions]:
                try:
                    os.remove(f)
                except OSError:
                    pass

        _finalize_steps(job_id)
        _set_progress(job_id, "Done!", 100, step_key="done")
        _finalize_steps(job_id)
        update_job(job_id, status="completed")
        # Success: drop voices but keep the work_dir so /api/download still serves final.mp4.
        cleanup_session(job_id, remove_files=False)

    except BaseException as e:
        # Covers normal exceptions, asyncio.CancelledError, KeyboardInterrupt,
        # and our own cooperative JobCancelled — any interruption wipes voices
        # AND the entire session directory.
        import asyncio
        is_cancel = isinstance(e, (asyncio.CancelledError, JobCancelled))
        _finalize_steps(job_id)
        update_job(
            job_id,
            status="error",
            error="Cancelled" if is_cancel else str(e),
        )
        cleanup_session(job_id, remove_files=True)
        cancelled_jobs.discard(job_id)
        if isinstance(e, JobCancelled):
            return  # don't re-raise our internal sentinel
        raise
