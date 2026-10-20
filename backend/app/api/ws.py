import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models import SessionLocal
from app.services.pipeline_service import get_pipeline_status

router = APIRouter()


@router.websocket("/ws/projects/{project_id}/status")
async def pipeline_ws(websocket: WebSocket, project_id: str):
    await websocket.accept()
    try:
        while True:
            # Poll pipeline status every 2 seconds and push to client
            db = SessionLocal()
            try:
                status = get_pipeline_status(db, project_id)
                current = status.get("current_run")
                if current:
                    stages = [
                        {"stage": sr.stage, "status": sr.status}
                        for sr in current.stage_results
                    ]
                    await websocket.send_json({
                        "run_id": current.id,
                        "run_status": current.status,
                        "stages": stages,
                    })
                else:
                    # No active run — send latest status
                    runs = status.get("runs", [])
                    if runs:
                        latest = runs[0]
                        stages = [
                            {"stage": sr.stage, "status": sr.status}
                            for sr in latest.stage_results
                        ]
                        await websocket.send_json({
                            "run_id": latest.id,
                            "run_status": latest.status,
                            "stages": stages,
                        })
                    else:
                        await websocket.send_json({"run_id": None, "run_status": "no_runs", "stages": []})
            finally:
                db.close()

            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
