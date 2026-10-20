from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.orm import Session


class PipelineStage(ABC):
    """Abstract base class for pipeline stages.

    Each stage:
    - Reads inputs from previous stage artifacts or the Segment table
    - Performs processing
    - Returns a dict of artifact paths/data that gets stored in StageResult.artifacts
    """

    name: str = ""

    @abstractmethod
    def execute(
        self,
        db: Session,
        project_id: str,
        run_id: str,
        previous_artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        """Run this pipeline stage.

        Args:
            db: Database session
            project_id: The project being processed
            run_id: The current pipeline run
            previous_artifacts: Merged artifacts from all previous stages

        Returns:
            Dict of artifact keys/paths produced by this stage
        """
        ...
