"""Provider interface — the only place the pipeline talks to AI services.

This project ships WITHOUT any third-party AI integration. Every function
below raises ``NotImplementedError`` until you plug in a provider of your
choice (a local model, a hosted API, …). Everything else in the pipeline —
ffmpeg, Demucs separation, caption timing/rendering, re-timing, the editor —
runs locally and does not need this module.

Implement the functions you need; the docstrings describe the exact shapes
the rest of the code expects.
"""
from __future__ import annotations

import asyncio
from typing import List, Tuple


def _missing(name: str):
    raise NotImplementedError(
        f"No provider configured for `{name}`. "
        "Implement it in backend/services/providers.py."
    )


# --- Language model -------------------------------------------------------

def generate(contents) -> str:
    """Run a (multimodal) language-model prompt and return the raw text reply.

    ``contents`` is either a plain prompt string or a list mixing prompt
    strings and local audio file paths (``.wav``, ``.mp3``, ``.m4a``, …).
    Callers expect JSON in the reply and parse it themselves; use a low
    temperature. Used for transcription, translation and alignment.
    """
    _missing("generate")


# --- Voice ----------------------------------------------------------------

def clone_voice(audio_path: str, name: str) -> str:
    """Create a voice from a speaker's audio sample. Returns a voice id."""
    _missing("clone_voice")


def delete_voice(voice_id: str) -> None:
    """Free a voice created by ``clone_voice``. Must not raise."""
    pass


def generate_tts_segment_with_timestamps(
    text: str, voice_id: str, output_path: str,
) -> Tuple[str, List[dict]]:
    """Synthesize ``text`` with ``voice_id`` into a WAV at ``output_path``.

    Returns ``(output_path, words)`` where ``words`` is
    ``[{"word": str, "start": float_s, "end": float_s}, ...]`` relative to
    the start of the clip. Return an empty list if timings are unavailable;
    captions then fall back to even spacing.
    """
    _missing("generate_tts_segment_with_timestamps")


def forced_align_with_timestamps(audio_path: str, text: str) -> List[dict]:
    """Per-word timings of ``text`` spoken in ``audio_path``.

    Same shape as the ``words`` list above.
    """
    _missing("forced_align_with_timestamps")


async def align_clips_parallel(
    clips: list[tuple[str, str]],
    concurrency: int = 6,
) -> list[list[dict]]:
    """Forced-align a batch of (slice_path, expected_text) pairs concurrently.
    Returns one list of {word,start,end} dicts per clip. Empty inner list on
    failure for that slice (caller falls back to even spacing for just that
    segment).
    """
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _one(slice_path: str, expected_text: str) -> list[dict]:
        async with sem:
            try:
                return await asyncio.to_thread(
                    forced_align_with_timestamps, slice_path, expected_text
                )
            except Exception as e:
                print(f"[align] forced alignment failed: {e}")
                return []

    return await asyncio.gather(*[_one(p, t) for p, t in clips])
