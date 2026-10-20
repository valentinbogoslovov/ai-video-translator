# AI Video Translator

Self-hosted web app for translating a talking video into many languages at once:
transcribe and translate the speech, dub it with one voice per speaker, re-time the picture
to the new voice-over and burn in word-timed captions.

Backend: FastAPI (Python). Frontend: Next.js.

**No AI services are included.** Transcription, translation, voice cloning, TTS and forced
alignment go through a small provider interface, [`backend/services/providers.py`](backend/services/providers.py),
that you implement with whatever you like (local models or a hosted API). Everything else
runs locally: ffmpeg, Demucs vocal separation, caption grouping and rendering, re-timing,
background replacement and the timeline editor.

## Features

- **Translate + dub**: one upload gives you a dubbed MP4 per selected language (21 European languages built in).
- **One voice per speaker**: speakers are detected, a voice is created per unique speaker and freed after the job.
- **Your own translation**: upload a `.docx` or paste text with per-language headers (`EN:`, `DE:` …) instead of machine translation.
- **Your own voice-over**: upload recorded VO files named by language (`de.wav`, `fr.mp3`). TTS is skipped for those languages.
- **Word-timed captions**: ASS captions with configurable font, colour, position, scale and line breaks.
- **Music preserved**: Demucs splits voice from music/SFX, so only the voice is replaced.
- **Timeline editor**: after a job finishes, adjust captions and clips per language and re-render.
- **Background replacement**: swap the background behind the speaker for a scrolling web page (Playwright capture + person matting).
- **Serial job queue** with progress reporting and cancel.

## Plugging in a provider

Implement the functions in `backend/services/providers.py`. Each docstring gives the exact
input/output shape the pipeline expects:

| Function | Used for |
|---|---|
| `generate(contents) -> str` | LLM calls (text, or text + audio file paths) for transcription, translation and alignment. Prompts and JSON parsing are in `services/llm_tasks.py`. |
| `clone_voice(audio_path, name) -> voice_id` | creating a voice per speaker |
| `delete_voice(voice_id)` | cleanup after the job |
| `generate_tts_segment_with_timestamps(text, voice_id, out_wav)` | dubbing, returns per-word timings |
| `forced_align_with_timestamps(audio_path, text)` | word timings for uploaded voice-overs |

Until a function is implemented, the steps that need it stop with a clear `NotImplementedError`.

## Requirements

- Python 3.11+
- Node.js 20+
- ffmpeg **built with libass** (needed to burn in captions), on `PATH`
- Optional: a GPU speeds up Demucs and background matting (CPU works, it's just slower)

## Installation

```bash
git clone https://github.com/valentinbogoslovov/ai-video-translator.git
cd ai-video-translator

# backend
cd backend
python3 -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium  # only needed for background replacement
cd ..

# frontend
cd frontend
npm install
cd ..
```

## Usage

Start both servers:

```bash
./start.sh
```

or separately:

```bash
cd backend && ./venv/bin/python3 -m uvicorn main:app --port 8000
cd frontend && npm run dev
```

Open http://localhost:3000, drop in a video, pick languages (or attach a script and/or
voice-overs), choose a caption style and start the job. Finished videos can be downloaded
per language or opened in the editor.

Over HTTP:

```bash
curl -F video=@talk.mp4 -F languages=de,fr,es http://localhost:8000/api/translate
# → {"job_id": "...", "languages": ["de","fr","es"], "script_used": false}
curl http://localhost:8000/api/status/<job_id>
curl -o talk_de.mp4 http://localhost:8000/api/download/<job_id>/de
```

Language codes: `GET /api/languages`. Uploads and job outputs are written to
`backend/uploads/` (git-ignored).

## Security note

The API has **no authentication** and is meant to run on `localhost` only. Do not expose
port 8000 to the internet. See [SECURITY.md](SECURITY.md) for how to report vulnerabilities.

## Project layout

```
backend/
  main.py          FastAPI routes
  pipeline.py      translation / dubbing pipeline and job queue
  services/
    providers.py   provider interface (implement this)
    llm_tasks.py   transcription / translation / alignment prompts
    captions.py    caption grouping + ASS rendering
    ffmpeg.py, audio_separation.py, voiceover.py, background.py, script_parser.py, edit_render.py
  app/             early scaffold (not used by main.py)
frontend/
  app/             Next.js UI (upload page, timeline editor)
```

## License

[MIT](LICENSE) © 2026 Valentin Bogoslovov
