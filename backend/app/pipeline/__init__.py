from app.pipeline.base import PipelineStage
from app.pipeline.ingest import IngestStage
from app.pipeline.separate import SeparateStage
from app.pipeline.transcribe import TranscribeStage
from app.pipeline.diarize import DiarizeStage
from app.pipeline.align import AlignStage
from app.pipeline.dub import DubStage
from app.pipeline.mix import MixStage
from app.pipeline.export import ExportStage

STAGE_MAP: dict[str, type[PipelineStage]] = {
    "ingest": IngestStage,
    "separate": SeparateStage,
    "transcribe": TranscribeStage,
    "diarize": DiarizeStage,
    "align": AlignStage,
    "dub": DubStage,
    "mix": MixStage,
    "export": ExportStage,
}

__all__ = ["PipelineStage", "STAGE_MAP"]
