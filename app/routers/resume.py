from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Candidate
from app.resume_parser import extract_text, parse_resume_to_profile
from app.schemas import ResumeParseResponse

router = APIRouter(prefix="/resumes", tags=["resumes"])

ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt")


@router.post("/parse", response_model=ResumeParseResponse)
async def parse_resume(file: UploadFile, db: AsyncSession = Depends(get_db)):
    if not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(400, f"Unsupported file type. Allowed: {ALLOWED_EXTENSIONS}")

    content = await file.read()
    text = extract_text(file.filename, content)
    if not text.strip():
        raise HTTPException(422, "Could not extract any text from the uploaded resume.")

    profile = await parse_resume_to_profile(text)

    candidate = Candidate(
        name=profile.name,
        email=profile.email,
        years_experience=profile.years_experience,
        skills=profile.skills,
        raw_resume_text=text,
    )
    db.add(candidate)
    await db.commit()
    await db.refresh(candidate)

    return ResumeParseResponse(candidate_id=candidate.id, profile=profile)
