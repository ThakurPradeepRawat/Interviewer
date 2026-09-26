"""
Agentic interview flow, built with LangGraph.

Three roles, matching the "planner / retriever-evaluator / critic" pattern:
  - planner   : proposes the next interview question given the candidate's
                profile, role, and what's already been covered.
  - evaluator : scores a submitted answer (and code result, if any) against a
                structured rubric via OpenAI function calling.
  - critic    : decides whether the last answer needs a follow-up probe on the
                same topic, or whether the interview should move to a new
                topic / wrap up.

Because a real interview must pause between "ask" and "answer" while it waits
on the HTTP request/response cycle, this isn't run as one uninterrupted
graph.ainvoke() from start to finish. Instead:
  - `run_planner`            is invoked to produce each new question.
  - `evaluate_and_route`     (the compiled evaluator -> critic graph) is
                              invoked once a candidate submits an answer.
Session state that would normally live in the graph's checkpointer is instead
persisted in Redis (hot path) and Postgres (source of truth) between calls —
see redis_client.py and the interview router.
"""

import json
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph
from openai import AsyncOpenAI

from app.config import settings
from app.schemas import TurnEvaluation

client = AsyncOpenAI(api_key=settings.openai_api_key)


class InterviewState(TypedDict, total=False):
    candidate_profile: dict
    role: str
    history: list[dict]          # [{question, question_type, answer, evaluation}, ...]
    current_question: str
    question_type: str           # "conceptual" | "coding"
    candidate_answer: Optional[str]
    code_result: Optional[dict]
    max_turns: int
    turn_index: int
    last_evaluation: Optional[dict]
    route: str                   # "follow_up" | "next_topic" | "end"
    finished: bool


SCORE_TOOL = {
    "type": "function",
    "function": {
        "name": "record_evaluation",
        "description": "Record a structured evaluation of the candidate's answer.",
        "parameters": {
            "type": "object",
            "properties": {
                "correctness": {"type": "integer", "minimum": 0, "maximum": 10},
                "clarity": {"type": "integer", "minimum": 0, "maximum": 10},
                "depth": {"type": "integer", "minimum": 0, "maximum": 10},
                "feedback": {"type": "string"},
                "follow_up_needed": {"type": "boolean"},
            },
            "required": ["correctness", "clarity", "depth", "feedback", "follow_up_needed"],
        },
    },
}


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

async def run_planner(state: InterviewState) -> dict:
    """Generates the next question. Adapts difficulty/topic to the candidate's
    resume skills and to how prior answers scored."""
    covered = [h["question"] for h in state.get("history", [])]
    weak_areas = [
        h["question"] for h in state.get("history", [])
        if h.get("evaluation", {}).get("correctness", 10) < 5
    ]

    prompt = (
        f"You are interviewing a candidate for the role of {state['role']}.\n"
        f"Candidate skills: {state['candidate_profile'].get('skills', [])}\n"
        f"Questions already asked: {covered}\n"
        f"Areas the candidate struggled with: {weak_areas or 'none yet'}\n\n"
        "Propose exactly one new interview question. Prefer a coding question "
        "when it fits a listed skill, otherwise a conceptual/system-design "
        "question. Respond as compact JSON: "
        '{"question": "...", "question_type": "conceptual"|"coding"}'
    )
    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    return {"current_question": data["question"], "question_type": data.get("question_type", "conceptual")}


# ---------------------------------------------------------------------------
# Evaluator node
# ---------------------------------------------------------------------------

async def evaluator_node(state: InterviewState) -> InterviewState:
    code_note = ""
    if state.get("code_result"):
        cr = state["code_result"]
        code_note = f"\nCode execution — stdout: {cr.get('stdout')!r} stderr: {cr.get('stderr')!r} exit_code: {cr.get('exit_code')}"

    messages = [
        {
            "role": "system",
            "content": "You are grading one interview answer. Call record_evaluation "
            "with an honest, specific structured score. Be strict but fair.",
        },
        {
            "role": "user",
            "content": f"Question ({state['question_type']}): {state['current_question']}\n"
            f"Candidate answer: {state.get('candidate_answer') or '(no verbal answer given)'}"
            f"{code_note}",
        },
    ]
    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        tools=[SCORE_TOOL],
        tool_choice={"type": "function", "function": {"name": "record_evaluation"}},
    )
    args = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
    state["last_evaluation"] = args
    return state


# ---------------------------------------------------------------------------
# Critic node — decides where to route next
# ---------------------------------------------------------------------------

def critic_node(state: InterviewState) -> InterviewState:
    ev = state["last_evaluation"]
    turns_left = state["max_turns"] - (state["turn_index"] + 1)

    if turns_left <= 0:
        state["route"] = "end"
        state["finished"] = True
    elif ev.get("follow_up_needed") and ev.get("correctness", 10) < 6:
        state["route"] = "follow_up"
        state["finished"] = False
    else:
        state["route"] = "next_topic"
        state["finished"] = False
    return state


def _route(state: InterviewState) -> str:
    return "end" if state["route"] == "end" else "continue"


def _build_graph():
    graph = StateGraph(InterviewState)
    graph.add_node("evaluate", evaluator_node)
    graph.add_node("critic", critic_node)
    graph.set_entry_point("evaluate")
    graph.add_edge("evaluate", "critic")
    graph.add_conditional_edges("critic", _route, {"continue": END, "end": END})
    return graph.compile()


_evaluation_graph = _build_graph()


async def evaluate_and_route(state: InterviewState) -> InterviewState:
    """Runs the evaluate -> critic subgraph for one submitted answer."""
    return await _evaluation_graph.ainvoke(state)
