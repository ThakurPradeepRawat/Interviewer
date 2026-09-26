import json
from io import BytesIO

from docx import Document as DocxDocument
from openai import AsyncOpenAI
from pypdf import PdfReader

from app.config import settings
from app.schemas import CandidateProfile

client = AsyncOpenAI(api_key=settings.openai_api_key)

EXTRACT_PROFILE_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_candidate_profile",
        "description": "Extract structured candidate profile fields from resume text.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "years_experience": {"type": "number"},
                "skills": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["skills"],
        },
    },
}


def extract_text(filename: str, content: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if filename.lower().endswith(".docx"):
        doc = DocxDocument(BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs)
    return content.decode("utf-8", errors="ignore")


async def parse_resume_to_profile(resume_text: str) -> CandidateProfile:
    """Uses OpenAI function calling to force a structured extraction instead of
    free-text parsing — this is the same tool-calling pattern used for scoring."""
    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": "Extract the candidate's profile from the resume text. "
                "Call extract_candidate_profile with the structured fields.",
            },
            {"role": "user", "content": resume_text[:12000]},
        ],
        tools=[EXTRACT_PROFILE_TOOL],
        tool_choice={"type": "function", "function": {"name": "extract_candidate_profile"}},
    )
    tool_call = response.choices[0].message.tool_calls[0]
    args = json.loads(tool_call.function.arguments)
    return CandidateProfile(**args)
