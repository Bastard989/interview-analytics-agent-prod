"""
HTTP ingestion endpoints for post-meeting uploads.

Задача:
- принимать аудио-чанки через HTTP
- сохранять в blob storage
- ставить задачу в STT очередь
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from apps.api_gateway.deps import auth_dep
from interview_analytics_agent.common.ids import new_idempotency_key
from interview_analytics_agent.common.logging import get_project_logger
from interview_analytics_agent.common.utils import b64_decode
from interview_analytics_agent.queue.dispatcher import enqueue_stt
from interview_analytics_agent.queue.idempotency import check_and_set
from interview_analytics_agent.storage.blob import put_bytes

log = get_project_logger()
router = APIRouter()


class ChunkIngestRequest(BaseModel):
    seq: int = Field(ge=0)
    content_b64: str
    codec: str = "pcm"
    sample_rate: int = 16000
    channels: int = 1
    idempotency_key: str | None = None


class ChunkIngestResponse(BaseModel):
    accepted: bool
    meeting_id: str
    seq: int
    idempotency_key: str
    blob_key: str


@router.post(
    "/meetings/{meeting_id}/chunks",
    response_model=ChunkIngestResponse,
    dependencies=[Depends(auth_dep)],
)
def ingest_chunk(meeting_id: str, req: ChunkIngestRequest) -> ChunkIngestResponse:
    idem_key = req.idempotency_key or new_idempotency_key("http-chunk")
    if not check_and_set("audio_chunk_http", meeting_id, idem_key):
        blob_key = f"meetings/{meeting_id}/chunks/{req.seq}.bin"
        return ChunkIngestResponse(
            accepted=True,
            meeting_id=meeting_id,
            seq=req.seq,
            idempotency_key=idem_key,
            blob_key=blob_key,
        )

    try:
        audio_bytes = b64_decode(req.content_b64)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "bad_audio", "message": "content_b64 не декодируется"},
        ) from e
    blob_key = f"meetings/{meeting_id}/chunks/{req.seq}.bin"
    put_bytes(blob_key, audio_bytes)
    enqueue_stt(meeting_id=meeting_id, chunk_seq=req.seq, blob_key=blob_key)

    log.info(
        "http_chunk_ingested",
        extra={
            "meeting_id": meeting_id,
            "payload": {"seq": req.seq, "codec": req.codec, "sample_rate": req.sample_rate},
        },
    )
    return ChunkIngestResponse(
        accepted=True,
        meeting_id=meeting_id,
        seq=req.seq,
        idempotency_key=idem_key,
        blob_key=blob_key,
    )
