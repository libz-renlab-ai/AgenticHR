"""F3 RecruitBot HTTP API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.auth.deps import get_current_user_id
from app.modules.auth.models import User
from app.modules.recruit_bot.schemas import (
    DailyCapUpdateRequest,
    GreetRecordRequest,
    RecruitDecision,
    RecruitEvaluateRequest,
    UsageInfo,
)
from app.modules.recruit_bot.service import (
    evaluate_and_record,
    get_daily_usage,
    record_greet_sent,
)

router = APIRouter(prefix="/api/recruit", tags=["recruit"])


@router.post("/evaluate_and_record", response_model=RecruitDecision)
async def evaluate_endpoint(
    body: RecruitEvaluateRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> RecruitDecision:
    try:
        return await evaluate_and_record(
            db, user_id=user_id,
            job_id=body.job_id, candidate=body.candidate,
            education_filter=body.education_filter,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/record-greet")
def record_greet_endpoint(
    body: GreetRecordRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    try:
        record_greet_sent(
            db, user_id=user_id, resume_id=body.resume_id,
            success=body.success, error_msg=body.error_msg,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "recorded"}


@router.get("/daily-usage", response_model=UsageInfo)
def daily_usage_endpoint(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> UsageInfo:
    return get_daily_usage(db, user_id)


@router.put("/daily-cap")
def daily_cap_update_endpoint(
    body: DailyCapUpdateRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    user.daily_cap = body.cap
    db.commit()
    return {"cap": body.cap}
