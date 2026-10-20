from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel


class WordTiming(BaseModel):
    word: str
    start: float
    end: float


class Segment(BaseModel):
    start: float
    end: float
    original: str
    translated: str
    words: List[WordTiming]
    speaker: str = "Speaker 1"
    original_words: List[WordTiming] = []


class TranslateRequest(BaseModel):
    languages: List[str]


class StepTiming(BaseModel):
    name: str
    seconds: float


class JobStatus(BaseModel):
    job_id: str
    status: str  # "queued", "processing", "completed", "error"
    current_step: str = ""
    current_language: str = ""
    languages_done: List[str] = []
    languages_total: List[str] = []
    progress: float = 0.0  # 0..100
    error: Optional[str] = None
    step_history: List[StepTiming] = []
    current_step_started_at: float = 0.0  # epoch seconds
