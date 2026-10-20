import os
import uuid
import json as _json
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from models import JobStatus, Segment, WordTiming
from pipeline import jobs, run_pipeline, update_job, cleanup_all_sessions, enqueue_job
from utils import LANGUAGES
from services.script_parser import parse_script_docx, parse_script_text


def _resolve_job_asset(job_id: str, rel_path: str) -> str:
    """Resolve a relative asset path under uploads/{job_id}/, with a
    path-traversal guard. Raises HTTPException(400) if the resolved
    path escapes the job's working directory."""
    work_dir = os.path.realpath(os.path.join(settings.upload_dir, job_id))
    if not os.path.isdir(work_dir):
        raise HTTPException(404, "Job not found")
    rel_path = (rel_path or "").lstrip("/")
    candidate = os.path.realpath(os.path.join(work_dir, rel_path))
    if candidate != work_dir and not candidate.startswith(work_dir + os.sep):
        raise HTTPException(400, "Path traversal rejected")
    return candidate


def _job_has_completed_language(work_dir: str) -> bool:
    """True iff any language subdir contains a manifest.json (i.e. a
    finished translation that the Edit feature can load)."""
    if not os.path.isdir(work_dir):
        return False
    for entry in os.listdir(work_dir):
        sub = os.path.join(work_dir, entry)
        if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "manifest.json")):
            return True
    return False

app = FastAPI(title="Video Translator")


@app.on_event("startup")
async def _wipe_stale_sessions():
    """On boot, delete any leftover INCOMPLETE upload dirs from a previous
    (crashed) run. Completed jobs (any language with manifest.json) are
    preserved so the post-translation Edit feature can still load them.
    Manual cleanup of completed dirs is the user's responsibility.
    """
    import shutil
    upload_dir = settings.upload_dir
    if os.path.isdir(upload_dir):
        for entry in os.listdir(upload_dir):
            full = os.path.join(upload_dir, entry)
            if not os.path.isdir(full):
                continue
            if _job_has_completed_language(full):
                continue
            shutil.rmtree(full, ignore_errors=True)


@app.on_event("shutdown")
async def _cleanup_on_shutdown():
    """On graceful shutdown, kill voices + wipe work dirs for any in-flight jobs."""
    cleanup_all_sessions()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Auth ---

async def verify_auth(request: Request):
    pass  # Auth disabled


# --- Routes ---

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/languages")
async def get_languages():
    return LANGUAGES


@app.post("/api/translate", dependencies=[Depends(verify_auth)])
async def translate_video(
    video: UploadFile = File(...),
    languages: str = Form(""),  # comma-separated language codes (ignored when script is uploaded)
    script: Optional[UploadFile] = File(None),
    script_text: Optional[str] = Form(None),  # pasted translation text (alternative to .docx)
    # Caption text source — independent of the translation source.
    # If provided, captions render this text (aligned to the actual
    # final audio) instead of whatever the translation pipeline
    # generated. Same per-language docx-with-headers convention as
    # `script` and the same pasted-text convention as `script_text`.
    caption_script: Optional[UploadFile] = File(None),
    caption_script_text: Optional[str] = Form(None),
    caption_box: Optional[str] = Form(None),  # JSON: {x,y,scale,rotation}
    background_urls: Optional[str] = Form(None),  # JSON: [{"url":"...","lang":"en"}, ...]
    caption_style: Optional[str] = Form(None),  # JSON: {font_name, primary_color, ...}
    voiceovers: list[UploadFile] = File(default=[]),  # filename = lang code
):
    import json as _json
    parsed_box = None
    if caption_box:
        try:
            parsed_box = _json.loads(caption_box)
        except Exception:
            parsed_box = None
    parsed_style = None
    if caption_style:
        try:
            parsed_style = _json.loads(caption_style)
        except Exception:
            parsed_style = None
    job_id = str(uuid.uuid4())
    work_dir = os.path.join(settings.upload_dir, job_id)
    os.makedirs(work_dir, exist_ok=True)

    # Save the uploaded video.
    video_name = Path(video.filename or "video").stem
    video_path = os.path.join(work_dir, "original.mp4")
    with open(video_path, "wb") as f:
        f.write(await video.read())

    # If a script .docx is provided, parse it (it can dictate language sections).
    scripts: dict[str, str] = {}
    if script is not None and script.filename:
        if not script.filename.lower().endswith(".docx"):
            raise HTTPException(400, "Script must be a .docx file")
        script_path = os.path.join(work_dir, "script.docx")
        with open(script_path, "wb") as f:
            f.write(await script.read())
        try:
            scripts = parse_script_docx(script_path)
        except Exception as e:
            raise HTTPException(400, f"Failed to parse script: {e}")
        if not scripts:
            raise HTTPException(
                400,
                "No language sections detected in the script. Use bold headers like **EN**, **DE**, **GR**.",
            )
        unknown = [l for l in scripts.keys() if l not in LANGUAGES]
        if unknown:
            raise HTTPException(400, f"Script contains unsupported languages: {unknown}")

    # If pasted translation text is provided (and no .docx), parse it.
    if not scripts and script_text and script_text.strip():
        try:
            scripts = parse_script_text(script_text)
        except Exception as e:
            raise HTTPException(400, f"Failed to parse pasted script: {e}")
        if not scripts:
            raise HTTPException(
                400,
                "No language sections detected in the pasted text. Use headers like EN, DE, GR.",
            )
        unknown = [l for l in scripts.keys() if l not in LANGUAGES]
        if unknown:
            raise HTTPException(400, f"Pasted script contains unsupported languages: {unknown}")

    # Independent caption-text source. Same parsing convention as
    # `script`/`script_text` but written to a separate file and stored
    # as `caption_scripts: dict[lang, str]`. When set for a language,
    # the pipeline aligns this text (not the translation) to that
    # language's final audio and uses it for the burned captions.
    caption_scripts: dict[str, str] = {}
    if caption_script is not None and caption_script.filename:
        if not caption_script.filename.lower().endswith(".docx"):
            raise HTTPException(400, "caption_script must be a .docx file")
        cs_path = os.path.join(work_dir, "caption_script.docx")
        with open(cs_path, "wb") as f:
            f.write(await caption_script.read())
        try:
            caption_scripts = parse_script_docx(cs_path)
        except Exception as e:
            raise HTTPException(400, f"Failed to parse caption_script: {e}")
        if not caption_scripts:
            raise HTTPException(
                400,
                "No language sections detected in caption_script. Use headers like **EN**, **DE**.",
            )
        unknown = [l for l in caption_scripts.keys() if l not in LANGUAGES]
        if unknown:
            raise HTTPException(400, f"caption_script contains unsupported languages: {unknown}")
    if not caption_scripts and caption_script_text and caption_script_text.strip():
        try:
            caption_scripts = parse_script_text(caption_script_text)
        except Exception as e:
            raise HTTPException(400, f"Failed to parse pasted caption_script_text: {e}")
        if not caption_scripts:
            raise HTTPException(
                400,
                "No language sections in pasted caption_script_text. Use headers like EN, DE.",
            )
        unknown = [l for l in caption_scripts.keys() if l not in LANGUAGES]
        if unknown:
            raise HTTPException(400, f"caption_script_text contains unsupported languages: {unknown}")

    # Save uploaded voiceover audio files. Filename → language code via the
    # same fuzzy lookup the .docx parser uses. Files that don't resolve to a
    # known language are rejected with a clear error.
    from services.voiceover import language_from_filename
    voiceover_audio: dict[str, str] = {}
    if voiceovers:
        for f in voiceovers:
            if not f.filename:
                continue
            lang = language_from_filename(f.filename)
            if not lang or lang not in LANGUAGES:
                raise HTTPException(
                    400,
                    f"Could not infer language from voiceover filename '{f.filename}'. "
                    "Name files like en.mp3 / de.wav / english.m4a.",
                )
            ext = Path(f.filename).suffix or ".wav"
            vo_path = os.path.join(work_dir, f"voiceover_{lang}{ext}")
            with open(vo_path, "wb") as out:
                out.write(await f.read())
            voiceover_audio[lang] = vo_path

    # Decide the final language list:
    #  - If voiceover files are present, ONLY their languages are processed
    #    (the user explicitly drove the lang context via uploaded files).
    #  - Else if a .docx script is present, langs come from the script sections.
    #  - Else fall back to the form's `languages` CSV.
    if voiceover_audio:
        lang_list = sorted(voiceover_audio.keys())
    elif scripts:
        lang_list = [l for l in scripts.keys() if l in LANGUAGES]
    else:
        lang_list = [l.strip() for l in languages.split(",") if l.strip()]
        invalid = [l for l in lang_list if l not in LANGUAGES]
        if invalid:
            raise HTTPException(400, f"Invalid languages: {invalid}")

    if not lang_list:
        raise HTTPException(400, "No languages selected")

    # Optional per-language background URLs.
    bg_urls: dict[str, str] = {}
    if background_urls:
        try:
            raw = _json.loads(background_urls)
            if isinstance(raw, list):
                for entry in raw:
                    if not isinstance(entry, dict):
                        continue
                    u = (entry.get("url") or "").strip()
                    l = (entry.get("lang") or "").strip()
                    if u and l and l in LANGUAGES:
                        bg_urls[l] = u
        except Exception as e:
            raise HTTPException(400, f"Invalid background_urls JSON: {e}")

    # Init job status — starts as "queued"; the worker sets it to "processing".
    jobs[job_id] = JobStatus(
        job_id=job_id,
        status="queued",
        current_step="Waiting in queue...",
        languages_total=lang_list,
    )

    # Add to the serial queue (one job runs at a time).
    enqueue_job(
        job_id,
        video_path=video_path,
        languages=lang_list,
        video_name=video_name,
        scripts=scripts,
        caption_scripts=caption_scripts,
        caption_box=parsed_box,
        background_urls=bg_urls,
        caption_style=parsed_style,
        voiceover_audio=voiceover_audio,
    )

    return {"job_id": job_id, "languages": lang_list, "script_used": bool(scripts)}


@app.post("/api/cancel/{job_id}", dependencies=[Depends(verify_auth)])
async def cancel_job(job_id: str):
    from pipeline import running_tasks, cancelled_jobs
    # Cooperative flag — checked by sync sections of the pipeline.
    cancelled_jobs.add(job_id)
    task = running_tasks.get(job_id)
    if task is None:
        if job_id in jobs and jobs[job_id].status == "processing":
            jobs[job_id].status = "error"
            jobs[job_id].error = "Cancelled"
        return {"ok": True, "already_finished": True}
    task.cancel()
    return {"ok": True}


@app.get("/api/status/{job_id}", dependencies=[Depends(verify_auth)])
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return jobs[job_id]


@app.get("/api/edit/{job_id}")
async def list_edit_languages(job_id: str):
    """Return the languages available to edit for this job. Reads the
    disk (each finished language has a `manifest.json`), so it works
    even after a backend restart wipes the in-memory jobs dict.
    """
    work_dir = _resolve_job_asset(job_id, "")
    langs: list[str] = []
    for entry in sorted(os.listdir(work_dir)):
        sub = os.path.join(work_dir, entry)
        if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "manifest.json")):
            langs.append(entry)
    return {"job_id": job_id, "languages_done": langs}


@app.get("/api/edit/{job_id}/{language}")
async def get_edit_state(job_id: str, language: str):
    """Return the Edit Decision List for a finished job+language.

    Reads `manifest.json` written by the pipeline at the end of each
    language loop and synthesises the editor's view of the world:
    one video clip, one voice clip per segment, one music clip, plus
    a flat list of caption events derived from the segments' words.
    All clip `src` fields are paths under uploads/{job_id}/ so they
    can be loaded via /api/asset.
    """
    work_dir = _resolve_job_asset(job_id, "")
    lang_dir = _resolve_job_asset(job_id, language)
    manifest_path = os.path.join(lang_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise HTTPException(
            404,
            "Edit manifest not found — language not finished or job pre-dates the editor.",
        )

    with open(manifest_path) as f:
        manifest = _json.load(f)

    # Reconstruct Segment objects so we can derive caption events the
    # exact same way generate_ass_captions does. Prefer caption_segments
    # when the pipeline recorded them — older manifests without that
    # field fall back to the same segments the voice track uses.
    cap_seg_dicts = manifest.get("caption_segments") or manifest["segments"]
    segments = [
        Segment(
            start=s["start"],
            end=s["end"],
            speaker=s.get("speaker", "Speaker 1"),
            original=s.get("original", ""),
            translated=s.get("translated", ""),
            words=[
                WordTiming(word=w["word"], start=w["start"], end=w["end"])
                for w in s.get("words", [])
            ],
        )
        for s in cap_seg_dicts
    ]

    from services.captions import derive_caption_events
    captions = derive_caption_events(
        segments,
        video_width=manifest["video_width"],
        video_height=manifest["video_height"],
        caption_box=manifest.get("caption_box") or {},
        caption_style=manifest.get("caption_style") or {},
    )

    # Build clip list. The video track has one clip; voice track has one
    # clip per segment; music track has one clip iff the pipeline
    # produced an accompaniment stem.
    clips = [{
        "id": "video-0",
        "track": "video",
        "src": manifest["video_file"],
        "sourceStart": 0.0,
        "sourceEnd": float(manifest["video_duration"]),
        "timelineStart": 0.0,
        "speed": 1.0,
    }]
    for i, s in enumerate(manifest["segments"]):
        if not s.get("audio_file"):
            continue
        # Resolve the audio file to read its real on-disk duration —
        # the segment's [start, end] is the slot it filled, not the
        # raw TTS clip length, so we'd over-trim if we used end-start.
        af_path = _resolve_job_asset(job_id, os.path.join(language, s["audio_file"]))
        try:
            from services.ffmpeg import get_audio_duration
            af_dur = get_audio_duration(af_path)
        except Exception:
            af_dur = max(0.05, float(s["end"]) - float(s["start"]))
        clips.append({
            "id": f"voice-{i}",
            "track": "voice",
            "src": s["audio_file"],
            "sourceStart": 0.0,
            "sourceEnd": float(af_dur),
            "timelineStart": float(s["start"]),
            "speed": 1.0,
            "text": s.get("translated", ""),
            "speaker": s.get("speaker"),
        })

    if manifest.get("music_file"):
        music_full = _resolve_job_asset(
            job_id, os.path.normpath(os.path.join(language, manifest["music_file"]))
        )
        try:
            from services.ffmpeg import get_audio_duration
            music_dur = get_audio_duration(music_full)
        except Exception:
            music_dur = float(manifest["video_duration"])
        clips.append({
            "id": "music-0",
            "track": "music",
            "src": manifest["music_file"],
            "sourceStart": 0.0,
            "sourceEnd": float(music_dur),
            "timelineStart": 0.0,
            "speed": 1.0,
        })

    return {
        "job_id": job_id,
        "language": language,
        "duration": float(manifest["video_duration"]),
        "video_width": int(manifest["video_width"]),
        "video_height": int(manifest["video_height"]),
        "clips": clips,
        "captions": captions,
        "caption_box": manifest.get("caption_box") or {},
        "caption_style": manifest.get("caption_style") or {},
    }


@app.get("/api/asset/{job_id}/{path:path}")
async def get_job_asset(job_id: str, path: str):
    """Stream a file from a job's uploads directory. Editor uses this
    to load video, per-segment voice, and music files into the timeline.
    Path-traversal-guarded so paths can't escape the job's root.
    """
    full = _resolve_job_asset(job_id, path)
    if not os.path.isfile(full):
        raise HTTPException(404, "Asset not found")
    media_types = {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".json": "application/json",
        ".ass": "text/x-ssa",
        ".srt": "application/x-subrip",
    }
    ext = os.path.splitext(full)[1].lower()
    return FileResponse(full, media_type=media_types.get(ext, "application/octet-stream"))


@app.post("/api/edit/{job_id}/{language}/captions/regroup")
async def regroup_captions(job_id: str, language: str, payload: dict):
    """Re-derive caption line events from the saved word-timed segments
    using the supplied caption_style + caption_box. Lets the user reshape
    breaks (max words per line, break-on-punct, pause threshold) without
    re-running the pipeline. Editor calls this when the user clicks
    "Re-group" after touching one of the grouping controls.
    """
    lang_dir = _resolve_job_asset(job_id, language)
    manifest_path = os.path.join(lang_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise HTTPException(404, "Manifest not found")
    with open(manifest_path) as f:
        manifest = _json.load(f)

    segments = [
        Segment(
            start=s["start"], end=s["end"],
            speaker=s.get("speaker", "Speaker 1"),
            original=s.get("original", ""),
            translated=s.get("translated", ""),
            words=[
                WordTiming(word=w["word"], start=w["start"], end=w["end"])
                for w in s.get("words", [])
            ],
        )
        for s in (manifest.get("caption_segments") or manifest["segments"])
    ]

    from services.captions import derive_caption_events
    box = payload.get("caption_box") if isinstance(payload.get("caption_box"), dict) else (manifest.get("caption_box") or {})
    style = payload.get("caption_style") if isinstance(payload.get("caption_style"), dict) else (manifest.get("caption_style") or {})
    # apply_width_wrap=True: captions wrap to fit the caption box and
    # stack up to `max_lines_per_caption` visual lines per event
    # (Captions-AI style). Overflow past the last line spills into a
    # fresh caption event.
    captions = derive_caption_events(
        segments,
        video_width=int(manifest["video_width"]),
        video_height=int(manifest["video_height"]),
        caption_box=box,
        caption_style=style,
        apply_width_wrap=True,
    )
    return {"captions": captions}


@app.post("/api/edit/{job_id}/{language}/upload")
async def upload_edit_asset(
    job_id: str,
    language: str,
    file: UploadFile = File(...),
):
    """Save a user-uploaded file under uploads/{job_id}/{language}/user_uploads/
    and return the relative `src` path that goes into a new clip's EDL
    entry. The src is relative to the language dir, matching the
    convention used by clips emitted from the pipeline manifest.
    """
    work_dir = _resolve_job_asset(job_id, "")
    lang_dir = _resolve_job_asset(job_id, language)
    if not os.path.isfile(os.path.join(lang_dir, "manifest.json")):
        raise HTTPException(404, "Language not finished — cannot upload to its edit dir yet")
    uploads_sub = os.path.join(lang_dir, "user_uploads")
    os.makedirs(uploads_sub, exist_ok=True)

    # Filename: a uuid prefix + sanitised original extension. Avoids
    # collisions and file-system unfriendly characters from user input.
    raw_name = file.filename or "upload"
    ext = os.path.splitext(raw_name)[1].lower()
    if len(ext) > 6 or not ext.startswith("."):
        ext = ".bin"
    safe_name = f"{uuid.uuid4().hex[:12]}{ext}"
    out_path = os.path.join(uploads_sub, safe_name)
    with open(out_path, "wb") as f:
        f.write(await file.read())

    # Verify the path didn't escape the work_dir (defence in depth even
    # though we built it ourselves).
    real = os.path.realpath(out_path)
    if not real.startswith(os.path.realpath(work_dir) + os.sep):
        os.remove(out_path)
        raise HTTPException(400, "Upload path escaped job root")

    rel_to_lang = os.path.join("user_uploads", safe_name)
    return {
        "src": rel_to_lang,
        "filename": raw_name,
        "size": os.path.getsize(out_path),
    }


@app.post("/api/render/{job_id}/{language}")
async def render_edit(job_id: str, language: str, payload: dict):
    """Take an EDL JSON from the editor and produce final_edited.mp4.

    Payload shape:
        {
          "clips":         [{id, track, src, sourceStart, sourceEnd,
                              timelineStart, speed}, ...],
          "captions":      [{start, end, text}, ...],
          "caption_box":   {x, y, scale, rotation},
          "caption_style": {...}
        }

    Tracks supported: 'video' (1 clip in v1), 'voice' (N clips), 'music'
    (N clips). Captions are baked into the final mp4 via the
    `subtitles=` filter — the user opted for burn-in on export.
    """
    work_dir = _resolve_job_asset(job_id, "")
    lang_dir = _resolve_job_asset(job_id, language)
    manifest_path = os.path.join(lang_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise HTTPException(404, "Manifest not found for this job/language")
    with open(manifest_path) as f:
        manifest = _json.load(f)

    from services.edit_render import render_edit as _do_render
    fonts_dir = os.path.dirname(settings.font_path)
    try:
        out_path = await asyncio.get_running_loop().run_in_executor(
            None,
            _do_render,
            payload,
            manifest,
            job_id,
            language,
            work_dir,
            lang_dir,
            fonts_dir,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        # Surface ffmpeg stderr if it's there — most useful diagnostic
        # the user can act on.
        msg = str(e)
        raise HTTPException(500, f"Render failed: {msg[:1500]}")
    rel = os.path.relpath(out_path, work_dir)
    return {"output_url": f"/api/asset/{job_id}/{rel}"}


@app.post("/api/preview-ass")
async def preview_ass(payload: dict):
    """Build an ASS string for the caption preview using the same
    renderer as the final video. Two modes:

      • `captions` array supplied  → multi-event ASS used by the
        post-translation editor's live preview (events fire at their
        actual times as the user scrubs).
      • no `captions` key          → single always-on event from `text`
        (default "SAMPLE CAPTION") used by the home-page style preview
        on a still-loading video.
    """
    from services.captions import generate_preview_ass, generate_edited_ass
    box = payload.get("caption_box") if isinstance(payload.get("caption_box"), dict) else None
    style = payload.get("caption_style") if isinstance(payload.get("caption_style"), dict) else None
    try:
        w = int(payload.get("video_width") or 1920)
        h = int(payload.get("video_height") or 1080)
    except (TypeError, ValueError):
        w, h = 1920, 1080
    w = max(160, min(7680, w))
    h = max(90, min(4320, h))

    captions = payload.get("captions")
    if isinstance(captions, list):
        ass = generate_edited_ass(captions, w, h, caption_box=box, caption_style=style)
    else:
        text = str(payload.get("text") or "SAMPLE CAPTION")
        ass = generate_preview_ass(text, w, h, caption_box=box, caption_style=style)
    return {"ass": ass}


@app.get("/api/download/{job_id}/{language}", dependencies=[Depends(verify_auth)])
async def download_video(job_id: str, language: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    final_path = os.path.join(settings.upload_dir, job_id, language, "final.mp4")
    if not os.path.exists(final_path):
        raise HTTPException(404, "Video not ready yet")

    return FileResponse(
        final_path,
        media_type="video/mp4",
        filename=f"translated_{language}.mp4",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
