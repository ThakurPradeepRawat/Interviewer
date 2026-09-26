import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Candidate(Base):
    """A parsed candidate profile, extracted from an uploaded resume."""

    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=True)
    years_experience: Mapped[float] = mapped_column(Float, nullable=True)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    raw_resume_text: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    sessions: Mapped[list["InterviewSession"]] = relationship(back_populates="candidate")


class InterviewSession(Base):
    """One end-to-end interview run for a candidate. Persistent source of truth;
    Redis holds a hot cache of the same state for fast multi-turn reads."""

    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"))
    role: Mapped[str] = mapped_column(String(255), default="Backend Software Engineer")
    status: Mapped[str] = mapped_column(String(32), default="in_progress")  # in_progress | completed
    current_question_index: Mapped[int] = mapped_column(Integer, default=0)
    overall_score: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    candidate: Mapped["Candidate"] = relationship(back_populates="sessions")
    turns: Mapped[list["InterviewTurn"]] = relationship(back_populates="session", order_by="InterviewTurn.index")


class InterviewTurn(Base):
    """One question/answer/evaluation cycle within a session."""

    __tablename__ = "interview_turns"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id"))
    index: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    question_type: Mapped[str] = mapped_column(String(32), default="conceptual")  # conceptual | coding
    candidate_answer: Mapped[str] = mapped_column(Text, nullable=True)
    code_submission: Mapped[str] = mapped_column(Text, nullable=True)
    code_exec_result: Mapped[dict] = mapped_column(JSON, nullable=True)
    evaluation: Mapped[dict] = mapped_column(JSON, nullable=True)  # structured scoring, via OpenAI function calling
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["InterviewSession"] = relationship(back_populates="turns")
