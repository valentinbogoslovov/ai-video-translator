"""Pipeline task execution — runs stages sequentially in a background thread."""

import json
import logging
from typing import Any

from app.models import SessionLocal
from app.models.pipeline_run import PipelineRun
from app.models.stage_result import StageResult
from app.pipeline import STAGE_MAP
from app.services.pipeline_service import STAGE_ORDER, update_stage_status, complete_run

logger = logging.getLogger(__name__)


def run_pipeline(project_id: str, run_id: str) -> None:
    """Execute all pending stages for a pipeline run, sequentially."""
    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            logger.error("Pipeline run %s not found", run_id)
            return

        # Collect artifacts from skipped (prior) stages
        merged_artifacts: dict[str, Any] = {}
        for sr in sorted(run.stage_results, key=lambda s: STAGE_ORDER.index(s.stage)):
            if sr.status == "skipped" and sr.artifacts:
                merged_artifacts.update(json.loads(sr.artifacts))

        # Run each pending stage in order
        for stage_name in STAGE_ORDER:
            sr = next((s for s in run.stage_results if s.stage == stage_name), None)
            if not sr or sr.status != "pending":
                continue

            stage_cls = STAGE_MAP.get(stage_name)
            if not stage_cls:
                update_stage_status(db, run_id, stage_name, "failed", error=f"Unknown stage: {stage_name}")
                complete_run(db, run_id, success=False)
                return

            update_stage_status(db, run_id, stage_name, "running")

            try:
                stage = stage_cls()
                result = stage.execute(db, project_id, run_id, merged_artifacts)
                merged_artifacts.update(result)
                update_stage_status(db, run_id, stage_name, "completed", artifacts=result)
            except Exception as e:
                logger.exception("Stage %s failed", stage_name)
                update_stage_status(db, run_id, stage_name, "failed", error=str(e))
                complete_run(db, run_id, success=False)
                return

        complete_run(db, run_id, success=True)
    finally:
        db.close()
