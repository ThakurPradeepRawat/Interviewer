from typing import Any, Optional

from pydantic import BaseModel, Field


class CandidateProfile(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    years_experience: Optional[float] = None
    skills: list[str] = Field(default_factory=list)


class ResumeParseResponse(BaseModel):
    candidate_id: str
    profile: CandidateProfile


class StartInterviewRequest(BaseModel):
    candidate_id: str
    role: str = "Backend Software Engineer"


class QuestionResponse(BaseModel):
    session_id: str
    turn_index: int
    question: str
    question_type: str
    is_final: bool


class AnswerRequest(BaseModel):
    answer: Optional[str] = None
    code: Optional[str] = None
    language: str = "python"


class CodeExecResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_ms: int


class TurnEvaluation(BaseModel):
    """The structured object the model must return via OpenAI function calling."""

    correctness: int = Field(ge=0, le=10, description="0-10, technical correctness")
    clarity: int = Field(ge=0, le=10, description="0-10, communication clarity")
    depth: int = Field(ge=0, le=10, description="0-10, depth of understanding / edge cases")
    feedback: str
    follow_up_needed: bool = False


class AnswerResponse(BaseModel):
    session_id: str
    turn_index: int
    evaluation: TurnEvaluation
    code_result: Optional[CodeExecResult] = None
    next_question: Optional[QuestionResponse] = None
    session_status: str


class InterviewReport(BaseModel):
    session_id: str
    candidate_id: str
    role: str
    status: str
    overall_score: Optional[float]
    turns: list[dict[str, Any]]
