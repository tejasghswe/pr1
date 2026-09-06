from __future__ import annotations

from fastapi import APIRouter

from app.agents.ask import answer_question
from schemas import AskRequest, AskResponse

router = APIRouter()


@router.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    return await answer_question(request.patient_id, request.question)
