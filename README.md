# Agentic AI Interview Assistant

**An automated technical interview platform.** Parses a resume, runs a multi-turn
interview through an agentic planner → evaluator → critic loop, executes
candidate-submitted code in a sandbox, and scores every answer against a
structured rubric — all served through a FastAPI backend backed by
PostgreSQL and Redis.

![Python](https://img.shields.io/badge/python-3.11-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![LangGraph](https://img.shields.io/badge/LangGraph-multi--agent-6f42c1)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791)
![Redis](https://img.shields.io/badge/Redis-7-DC382D)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## Table of Contents

- [Why this exists](#why-this-exists)
- [Architecture](#architecture)
- [Design decisions](#design-decisions)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [API reference](#api-reference)
- [Load testing](#load-testing)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Why this exists

Manual technical screening doesn't scale, and free-text LLM grading is
unreliable — ask the same model to "rate this answer" twice and you'll get
two different rationales in two different shapes. This project treats
interview evaluation as a **structured extraction problem**, not a
conversation: every score is produced through OpenAI function calling
against a fixed schema, every question is proposed by an agent that has
seen the candidate's actual skill set and prior performance, and every
session survives request restarts and cache evictions because Postgres —
not the graph's in-memory state — is the source of truth.

## Architecture

```mermaid
flowchart TB
    subgraph Ingestion
        A[Resume upload
        PDF / DOCX / TXT] -->|extract text| B[OpenAI function calling
        extract_candidate_profile]
        B --> C[(PostgreSQL
        candidates)]
    end

    subgraph "Interview loop — LangGraph"
        D[POST /interviews/start] --> E[Planner
        proposes next question]
        E --> F[(Postgres: session + turn)]
        E --> G[(Redis: hot session state
        TTL-bound cache)]

        H[POST .../answer] --> I{Code submitted?}
        I -->|yes| J[Sandboxed executor
        subprocess · rlimits · blocked imports]
        I -->|no| K[Evaluator node
        OpenAI function calling → rubric]
        J --> K
        K --> L[Critic node
        follow-up / next topic / end]
        L -->|continue| E
        L -->|end| M[GET .../report
        aggregated score]
    end
```

**Request flow for one answer:**

1. Client `POST`s an answer (text and/or code) to `/interviews/{id}/answer`.
2. If code was submitted, it runs in an isolated subprocess first; its
   stdout/stderr/exit code are fed into evaluation as evidence.
3. The **evaluator** node calls OpenAI with `tool_choice` forced to
   `record_evaluation` — the model cannot return anything except a
   schema-valid object with `correctness`, `clarity`, `depth`, `feedback`,
   and `follow_up_needed`.
4. The **critic** node reads that evaluation and decides: probe the same
   topic again, move to a new one, or end the interview (turn budget
   exhausted).
5. State is written to Postgres (durability) and Redis (fast reads for the
   next turn) before the response returns.

## Design decisions

A few choices here are deliberate trade-offs, not defaults — documented so
a reviewer doesn't have to guess the reasoning:

| Decision | Rationale | Trade-off accepted |
|---|---|---|
| Structured scoring via **function calling**, not prompt-and-parse | Deterministic shape, no regex/JSON-repair on LLM output | Slightly more prompt engineering up front |
| **Redis is a cache, not the source of truth** | Postgres survives cache eviction / restarts; Redis just avoids rebuilding full history on every turn | Extra code path (`_rebuild_state_from_db`) to keep in sync |
| Graph is **not** run start-to-finish in one `ainvoke` | A real interview must pause between "ask" and "answer" across an HTTP round trip | Planner and evaluator/critic are invoked separately per turn, coordinated by the router, instead of one continuous LangGraph run |
| Code sandbox is **subprocess + rlimits**, not a VM/container-per-run | Good enough for trusted/low-stakes evaluation with near-zero infra | Explicitly *not* safe for arbitrary untrusted internet users — see [Known limitations](#known-limitations) |
| Tables auto-created on startup (`init_models`) | Fast local iteration | Not a substitute for Alembic migrations in any real deployment |

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| API framework | FastAPI | Async-native, Pydantic validation, OpenAPI docs for free |
| Agent orchestration | LangGraph | Explicit state machine for planner/evaluator/critic instead of ad-hoc prompt chaining |
| LLM | OpenAI API (function calling) | Schema-enforced structured output |
| Persistent store | PostgreSQL (async, SQLAlchemy 2.0) | Relational integrity for candidates → sessions → turns |
| Session cache | Redis | Sub-millisecond reads for hot multi-turn state |
| Code execution | Python subprocess + `resource` limits | Isolation without container-per-request overhead |
| Load testing | Locust | Simulates full start → answer × N → report flows per virtual user |

## Project structure

```
app/
├── main.py              FastAPI app assembly, startup hook (table creation)
├── config.py            Settings, loaded from environment / .env
├── database.py          Async SQLAlchemy engine, session factory
├── models.py            Candidate, InterviewSession, InterviewTurn (ORM)
├── schemas.py           Pydantic request/response contracts
├── redis_client.py      Session-state cache (get/set/drop, TTL-bound)
├── resume_parser.py     PDF/DOCX text extraction + structured parsing
├── code_executor.py     Sandboxed Python execution
├── interview_graph.py   LangGraph: planner, evaluator node, critic node
└── routers/
    ├── resume.py        POST /resumes/parse
    └── interview.py     POST /interviews/start
                          POST /interviews/{id}/answer
                          GET  /interviews/{id}/report
locustfile.py             Load test: start → 3 turns → report, per user
docker-compose.yml         Postgres + Redis + API
Dockerfile
requirements.txt
.env.example
```

## Getting started

### Prerequisites
- Python 3.11+
- Docker (for Postgres + Redis) — or point `DATABASE_URL` / `REDIS_URL` at
  existing instances
- An OpenAI API key

### Setup

```bash
git clone <this-repo>
cd interview_assistant

cp .env.example .env        # then set OPENAI_API_KEY
docker compose up -d postgres redis

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

Interactive API docs: `http://localhost:8000/docs`

### Running with Docker Compose (API included)

```bash
docker compose up --build
```

## Configuration

All settings are environment-driven (`app/config.py`, backed by
`pydantic-settings`):

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Required. No fallback — the app will fail fast without it in real use. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used for parsing, planning, and evaluation |
| `DATABASE_URL` | `postgresql+asyncpg://interview:interview@localhost:5432/interview_assistant` | Async SQLAlchemy DSN |
| `REDIS_URL` | `redis://localhost:6379/0` | Session cache connection |
| `SESSION_TTL_SECONDS` | `3600` | How long a cached session survives inactivity |
| `MAX_QUESTIONS_PER_INTERVIEW` | `6` | Turn budget before the interview auto-completes |
| `CODE_EXEC_TIMEOUT_SECONDS` | `5` | Hard wall-clock + CPU limit per code submission |

## API reference

### `POST /resumes/parse`
Multipart file upload (`.pdf`, `.docx`, `.txt`). Extracts text, then uses
OpenAI function calling to extract a structured profile.

```bash
curl -F "file=@resume.pdf" http://localhost:8000/resumes/parse
```
```json
{
  "candidate_id": "b3f1...",
  "profile": { "name": "...", "email": "...", "years_experience": 1.5, "skills": ["Python", "FastAPI", "Redis"] }
}
```

### `POST /interviews/start`
```json
{ "candidate_id": "b3f1...", "role": "Backend Software Engineer" }
```
Returns the first question, already persisted to Postgres and cached in Redis.

### `POST /interviews/{session_id}/answer`
```json
{ "answer": "I'd use a write-through cache in front of Postgres." }
```
or, for a coding question:
```json
{ "code": "def two_sum(nums, target): ..." }
```
Returns the structured evaluation for that turn, the code execution result
(if applicable), and either the next question or a `completed` status.

### `GET /interviews/{session_id}/report`
Returns every turn (question, answer, code, evaluation) plus the aggregated
`overall_score` once the interview is complete.

Full request/response schemas are in `app/schemas.py` and auto-documented at
`/docs`.

## Load testing

```bash
locust -f locustfile.py --host http://localhost:8000 \
       --users 120 --spawn-rate 10 --run-time 5m --headless --csv=results
```

Each virtual user runs a full `start → 3 answers → report` cycle end to
end, so 100+ concurrent Locust users approximates 100+ concurrent
multi-turn interview sessions. In practice, OpenAI API latency dominates
p99, not FastAPI/Redis/Postgres overhead — profile `results_stats.csv`
accordingly before assuming a bottleneck is in this codebase.

## Known limitations

Stated explicitly rather than discovered in review:

- **Code sandbox is subprocess-based**, not container- or VM-isolated.
  Suitable for trusted or low-stakes evaluation; not hardened for
  arbitrary untrusted users at scale. A production version would use
  gVisor, Firecracker microVMs, or a hosted execution API.
- **No database migrations.** `init_models()` calls `create_all()` on
  startup for local-dev convenience. Any real deployment needs Alembic.
- **No retrieval over a curated question bank.** The planner generates
  questions from a prompt, not from a vetted, deduplicated question store —
  phrasing can occasionally repeat across sessions.
- **No authentication/authorization layer.** Every endpoint is open; this
  is an evaluation engine, not a deployable multi-tenant product as-is.
- **Single-region, single-instance assumptions.** No multi-region Redis or
  read-replica routing for Postgres — fine at demo scale, not at real
  production scale.

## Roadmap

- [ ] Alembic migrations
- [ ] Auth (API keys or OAuth2) per interviewer/organization
- [ ] Curated + versioned question bank with retrieval, instead of pure generation
- [ ] Streaming responses (SSE) for question generation and evaluation feedback
- [ ] Container-based code execution for untrusted submissions

## Contributing

Issues and PRs welcome. For anything nontrivial, open an issue first
describing the change so we can agree on approach before code is written.

## License

MIT — see `LICENSE`.