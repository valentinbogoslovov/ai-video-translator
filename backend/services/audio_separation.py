"""Audio source separation for the dubbing pipeline.

Splits an input audio file into a vocals stem and an accompaniment stem so
the pipeline can replace only the voice while preserving the original
music and sound effects. Runs locally with Demucs (htdemucs_ft) — no
network / API cost.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Optional


# Two-stem mode: only "vocals" vs "no_vocals". Faster than the full 4-stem
# split and that's all the dubbing pipeline actually needs.
#
# htdemucs_ft is the fine-tuned ensemble variant of htdemucs — it runs four
# fine-tuned models and averages them. ~4x slower than plain htdemucs but
# produces dramatically cleaner stems with far less vocal bleed / music
# smearing, which matters a lot when you replace the voice and keep the
# music bed: artifacts in the accompaniment become obvious without the
# original voice masking them.
_MODEL = "htdemucs_ft"


def separate_vocals(
    audio_path: str,
    out_dir: str,
    python_executable: Optional[str] = None,
) -> tuple[str, str]:
    """Return ``(vocals_wav, accompaniment_wav)`` for ``audio_path``.

    Both stems are written into ``out_dir``. Raises ``RuntimeError`` if
    Demucs fails.
    """
    return _separate_vocals_demucs(audio_path, out_dir, python_executable)


def _separate_vocals_demucs(
    audio_path: str,
    out_dir: str,
    python_executable: Optional[str] = None,
) -> tuple[str, str]:
    """Demucs backend. Same return shape as :func:`separate_vocals`."""
    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, "_demucs")
    if os.path.isdir(work):
        shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    py = python_executable or sys.executable
    proc = subprocess.run(
        [
            py, "-m", "demucs",
            "-n", _MODEL,
            "--two-stems=vocals",
            "-o", work,
            audio_path,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"demucs failed (rc={proc.returncode}):\n{proc.stderr or proc.stdout}"
        )

    base = os.path.splitext(os.path.basename(audio_path))[0]
    src_dir = os.path.join(work, _MODEL, base)
    src_vocals = os.path.join(src_dir, "vocals.wav")
    src_accomp = os.path.join(src_dir, "no_vocals.wav")
    if not (os.path.isfile(src_vocals) and os.path.isfile(src_accomp)):
        raise RuntimeError(
            f"demucs output missing: expected {src_vocals} and {src_accomp}"
        )

    vocals = os.path.join(out_dir, "vocals.wav")
    accomp = os.path.join(out_dir, "accompaniment.wav")
    shutil.move(src_vocals, vocals)
    shutil.move(src_accomp, accomp)
    shutil.rmtree(work, ignore_errors=True)
    return vocals, accomp
