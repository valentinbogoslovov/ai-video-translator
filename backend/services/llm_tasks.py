"""LLM-backed transcription, translation and alignment tasks.

Prompts and response parsing live here; the model call itself goes through
`services.providers.generate`, which you implement for your provider.
"""
from __future__ import annotations
import json
import re
from typing import List

from models import Segment, WordTiming
from services.providers import generate


def align_segments_to_blog_sections(
    segments: list[Segment],
    sections: list[dict],
) -> dict[int, int]:
    if not segments or not sections:
        return {}

    seg_payload = [
        {"i": i, "t": round(s.start, 2), "text": (s.translated or s.original or "")[:240]}
        for i, s in enumerate(segments)
    ]
    sec_payload = [
        {"i": i, "tag": s.get("tag", ""), "text": (s.get("text", ""))[:240]}
        for i, s in enumerate(sections)
    ]

    prompt = (
        "You are aligning a spoken transcript to sections of a blog post.\n"
        "For each transcript segment, pick the index of the blog section it is "
        "most likely talking about. The mapping should be MONOTONIC — segment "
        "indices increase, so section indices should generally not move "
        "backwards. If a segment doesn't clearly correspond to any section, "
        "skip it.\n\n"
        f"TRANSCRIPT SEGMENTS (index, start_time_seconds, text):\n{json.dumps(seg_payload, ensure_ascii=False)}\n\n"
        f"BLOG SECTIONS (index, tag, text):\n{json.dumps(sec_payload, ensure_ascii=False)}\n\n"
        "Return ONLY a JSON array of objects: "
        '[{"seg": <segment index>, "sec": <section index>}, ...]. '
        "No prose, no code fences."
    )

    text = generate(prompt)
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except Exception as e:
        print(f"[llm.align] could not parse response: {e}; raw={text[:300]}")
        return {}

    out: dict[int, int] = {}
    last_sec = -1
    for row in data:
        try:
            seg_i = int(row["seg"])
            sec_i = int(row["sec"])
        except Exception:
            continue
        if sec_i < last_sec:
            sec_i = last_sec
        out[seg_i] = sec_i
        last_sec = sec_i
    return out


def transcribe_with_word_timestamps(audio_path: str, target_language: str) -> list[dict]:
    prompt = (
        f"Transcribe this audio in {target_language}.\n"
        "Return ONLY a JSON object with this shape:\n"
        '{"words": [{"word": "<token>", "start": <seconds float>, "end": <seconds float>}, ...]}\n'
        "Rules:\n"
        "- One entry per spoken word, in spoken order.\n"
        "- Timestamps are in seconds from the start of the audio.\n"
        "- start <= end for every word, and entries must be monotonically ordered by start.\n"
        "- Use the exact spelling spoken; do not include punctuation as a separate word.\n"
        "- No markdown, no code fences, no extra commentary."
    )
    raw = generate([prompt, audio_path])
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"[llm.words] could not parse: {e}; raw={raw[:300]}")
        return []
    items = data.get("words", []) or []
    out: list[dict] = []
    last_end = 0.0
    for it in items:
        try:
            w = str(it["word"]).strip()
            s = float(it["start"])
            e = float(it["end"])
        except Exception:
            continue
        if not w:
            continue
        s = max(s, last_end)
        if e <= s:
            e = s + 0.05
        out.append({"word": w, "start": s, "end": e})
        last_end = e
    return out


async def transcribe_and_translate(audio_path: str, target_language: str) -> list[Segment]:
    prompt = f"""Transcribe this audio with precise timestamps, identify different speakers, and translate it to {target_language}.

Return ONLY a valid JSON array (no markdown, no code fences) where each element represents a sentence or phrase:
[
  {{
    "start": 0.0,
    "end": 2.5,
    "speaker": "Speaker 1",
    "original": "original text here",
    "translated": "translated text in {target_language}"
  }}
]

Rules:
- "start" and "end" are timestamps in seconds (float)
- "speaker" must consistently identify each unique voice (e.g. "Speaker 1", "Speaker 2", "Speaker 3")
- The SAME person must always have the SAME speaker label throughout
- Keep segments short (1-2 sentences max)
- Be precise with timestamps
- Return ONLY valid JSON, nothing else"""

    raw = generate([audio_path, prompt])
    raw = (raw or "").strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*([}\]])", r"\1", raw)
        if not fixed.endswith("]"):
            fixed = fixed.rstrip().rstrip(",") + "]"
        try:
            data = json.loads(fixed)
        except json.JSONDecodeError:
            print(f"[llm] JSON repair failed, retrying transcription...")
            raw = generate([audio_path, prompt])
            raw = (raw or "").strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            data = json.loads(raw)

    segments = []
    for item in data:
        start = float(item["start"])
        end = float(item["end"])
        translated = item["translated"]
        speaker = item.get("speaker", "Speaker 1")

        words = translated.split()
        if len(words) > 0:
            duration = end - start
            word_duration = duration / len(words)
            word_timings = [
                WordTiming(
                    word=w,
                    start=round(start + i * word_duration, 3),
                    end=round(start + (i + 1) * word_duration, 3),
                )
                for i, w in enumerate(words)
            ]
        else:
            word_timings = []

        segments.append(Segment(
            start=start,
            end=end,
            original=item["original"],
            translated=translated,
            words=word_timings,
            speaker=speaker,
        ))

    return segments


async def align_script_to_segments(
    script_text: str,
    base_segments: list[Segment],
    target_language: str,
) -> tuple[list[Segment], list[str]]:
    warnings: list[str] = []

    seg_payload = [
        {
            "index": i,
            "start": round(s.start, 3),
            "end": round(s.end, 3),
            "speaker": s.speaker,
            "original": s.original,
        }
        for i, s in enumerate(base_segments)
    ]

    prompt = f"""You are aligning a pre-translated script to a list of video segments.

Target language: {target_language}

VIDEO SEGMENTS (each has a fixed start/end timestamp, a speaker label, and the
original transcribed text):
{json.dumps(seg_payload, ensure_ascii=False)}

PRE-TRANSLATED SCRIPT (already in the target language; may contain speaker
labels like "Speaker 1:" or "[NAME]:" and may contain inline timecodes such as
"[00:12]"; treat these as hints — they may also be absent):
\"\"\"
{script_text}
\"\"\"

Task:
- Produce a JSON array with EXACTLY {len(base_segments)} elements, one per
  video segment, in the same order.
- Each element MUST be an object: {{"index": <int>, "translated": <string>}}
- "translated" is the portion of the script that corresponds to that segment,
  in the target language. Split or merge script lines as needed so each
  segment receives the dialogue actually spoken during its timeframe.
- Use any speaker labels and timecodes in the script as alignment hints, but
  the authoritative timestamps and speakers come from the video segments.
- If the script clearly has more or less content than the video segments,
  do a best-effort distribution and add a note in a top-level "warnings" array.

Return ONLY a JSON object of the form:
{{"segments": [...], "warnings": [...]}}
No markdown, no code fences."""

    raw = generate([prompt])
    raw = (raw or "").strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*([}\]])", r"\1", raw)
        try:
            data = json.loads(fixed)
        except json.JSONDecodeError:
            print(f"[llm.align_script] JSON repair failed, retrying...")
            raw = generate([prompt])
            raw = (raw or "").strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            data = json.loads(raw)

    aligned_items = data.get("segments", [])
    warnings.extend(data.get("warnings", []) or [])

    by_index = {int(item["index"]): item.get("translated", "") for item in aligned_items}
    if len(aligned_items) != len(base_segments):
        warnings.append(
            f"Script alignment returned {len(aligned_items)} entries for "
            f"{len(base_segments)} segments — using best-effort mapping."
        )

    new_segments: list[Segment] = []
    for i, base in enumerate(base_segments):
        translated = by_index.get(i, base.original).strip() or base.original
        words = translated.split()
        duration = base.end - base.start
        if words:
            wd = duration / len(words)
            word_timings = [
                WordTiming(
                    word=w,
                    start=round(base.start + j * wd, 3),
                    end=round(base.start + (j + 1) * wd, 3),
                )
                for j, w in enumerate(words)
            ]
        else:
            word_timings = []
        new_segments.append(Segment(
            start=base.start,
            end=base.end,
            original=base.original,
            translated=translated,
            words=word_timings,
            speaker=base.speaker,
        ))

    return new_segments, warnings
