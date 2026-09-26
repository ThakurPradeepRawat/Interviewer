from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.code_executor import run_python_snippet
from app.config import settings
from app.database import get_db
from app.interview_graph import InterviewState, evaluate_and_route, run_planner
from app.models import Candidate, InterviewSession, InterviewTurn
from app.redis_client import cache_session_state, drop_session_state, get_session_state
from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    CodeExecResult,
    InterviewReport,
    QuestionResponse,
    StartInterviewRequest,
    TurnEvaluation,
)

router = APIRouter(prefix="/interviews", tags=["interviews"])


async def _load_candidate(db: AsyncSession, candidate_id: str) -> Candidate:
    candidate = await db.get(Candidate, candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found. Parse a resume first via /resumes/parse.")
    return candidate


@router.post("/start", response_model=QuestionResponse)
async def start_interview(req: StartInterviewRequest, db: AsyncSession = Depends(get_db)):
    candidate = await _load_candidate(db, req.candidate_id)

    session = InterviewSession(candidate_id=candidate.id, role=req.role)
    db.add(session)
    await db.flush()

    state: InterviewState = {
        "candidate_profile": {"skills": candidate.skills or []},
        "role": req.role,
        "history": [],
        "max_turns": settings.max_questions_per_interview,
        "turn_index": 0,
    }
    planned = await run_planner(state)

    turn = InterviewTurn(
        session_id=session.id,
        index=0,
        question=planned["current_question"],
        question_type=planned["question_type"],
    )
    db.add(turn)
    await db.commit()

    state.update(planned)
    await cache_session_state(session.id, state)

    return QuestionResponse(
        session_id=session.id,
        turn_index=0,
        question=planned["current_question"],
        question_type=planned["question_type"],
        is_final=False,
    )


@router.post("/{session_id}/answer", response_model=AnswerResponse)
async def submit_answer(session_id: str, req: AnswerRequest, db: AsyncSession = Depends(get_db)):
    session = await db.get(InterviewSession, session_id)
    if not session:
        raise HTTPException(404, "Interview session not found.")
    if session.status == "completed":
        raise HTTPException(409, "This interview has already been completed.")

    # Hot path: Redis. Cold path (cache expired/evicted): rebuild from Postgres.
    state = await get_session_state(session_id)
    if state is None:
        state = await _rebuild_state_from_db(db, session)

    code_result_payload = None
    if req.code:
        exec_result: CodeExecResult = run_python_snippet(req.code)
        code_result_payload = exec_result.model_dump()

    state["candidate_answer"] = req.answer
    state["code_result"] = code_result_payload

    result_state = await evaluate_and_route(state)
    evaluation = TurnEvaluation(**result_state["last_evaluation"])

    # Persist this turn's answer + evaluation.
    current_turn = await db.scalar(
        select(InterviewTurn).where(
            InterviewTurn.session_id == session_id,
            InterviewTurn.index == state["turn_index"],
        )
    )
    current_turn.candidate_answer = req.answer
    current_turn.code_submission = req.code
    current_turn.code_exec_result = code_result_payload
    current_turn.evaluation = evaluation.model_dump()

    result_state.setdefault("history", []).append(
        {
            "question": state["current_question"],
            "question_type": state["question_type"],
            "answer": req.answer,
            "evaluation": evaluation.model_dump(),
        }
    )

    next_question_payload = None
    if result_state["route"] == "end":
        session.status = "completed"
        session.completed_at = datetime.utcnow()
        all_scores = [
            (t["evaluation"]["correctness"] + t["evaluation"]["clarity"] + t["evaluation"]["depth"]) / 3
            for t in result_state["history"]
        ]
        session.overall_score = round(sum(all_scores) / len(all_scores), 2) if all_scores else None
        await drop_session_state(session_id)
    else:
        next_index = state["turn_index"] + 1
        planned = await run_planner(result_state)
        next_turn = InterviewTurn(
            session_id=session_id,
            index=next_index,
            question=planned["current_question"],
            question_type=planned["question_type"],
        )
        db.add(next_turn)
        session.current_question_index = next_index

        result_state.update(planned)
        result_state["turn_index"] = next_index
        result_state["candidate_answer"] = None
        result_state["code_result"] = None
        await cache_session_state(session_id, result_state)

        next_question_payload = QuestionResponse(
            session_id=session_id,
            turn_index=next_index,
            question=planned["current_question"],
            question_type=planned["question_type"],
            is_final=(next_index == state["max_turns"] - 1),
        )

    await db.commit()

    return AnswerResponse(
        session_id=session_id,
        turn_index=state["turn_index"],
        evaluation=evaluation,
        code_result=CodeExecResult(**code_result_payload) if code_result_payload else None,
        next_question=next_question_payload,
        session_status=session.status,
    )


@router.get("/{session_id}/report", response_model=InterviewReport)
async def get_report(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await db.get(InterviewSession, session_id)
    if not session:
        raise HTTPException(404, "Interview session not found.")

    turns = (
        await db.scalars(
            select(InterviewTurn).where(InterviewTurn.session_id == session_id).order_by(InterviewTurn.index)
        )
    ).all()

    return InterviewReport(
        session_id=session.id,
        candidate_id=session.candidate_id,
        role=session.role,
        status=session.status,
        overall_score=session.overall_score,
        turns=[
            {
                "index": t.index,
                "question": t.question,
                "question_type": t.question_type,
                "answer": t.candidate_answer,
                "code_submission": t.code_submission,
                "evaluation": t.evaluation,
            }
            for t in turns
        ],
    )


async def _rebuild_state_from_db(db: AsyncSession, session: InterviewSession) -> dict:
    """Fallback used only if the Redis cache entry expired/evicted mid-interview."""
    candidate = await db.get(Candidate, session.candidate_id)
    turns = (
        await db.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.session_id == session.id, InterviewTurn.index < session.current_question_index + 1)
            .order_by(InterviewTurn.index)
        )
    ).all()
    current = turns[-1]
    history = [
        {"question": t.question, "question_type": t.question_type, "answer": t.candidate_answer, "evaluation": t.evaluation}
        for t in turns[:-1]
    ]
    return {
        "candidate_profile": {"skills": candidate.skills or []},
        "role": session.role,
        "history": history,
        "max_turns": settings.max_questions_per_interview,
        "turn_index": current.index,
        "current_question": current.question,
        "question_type": current.question_type,
    }
