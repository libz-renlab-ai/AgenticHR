"""岗位管理与硬性条件筛选"""
from sqlalchemy.orm import Session

from app.modules.screening.job_helpers import effective_education_min
from app.modules.screening.models import Job
from app.modules.screening.schemas import JobCreate, JobUpdate
from app.modules.resume.models import Resume

EDUCATION_LEVELS = {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}


class ScreeningService:
    def __init__(self, db: Session):
        self.db = db

    def create_job(self, data: JobCreate) -> Job:
        job = Job(**data.model_dump())
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def get_job(self, job_id: int) -> Job | None:
        return self.db.query(Job).filter(Job.id == job_id).first()

    def list_jobs(self, active_only: bool = False, user_id: int | None = None) -> dict:
        query = self.db.query(Job)
        if user_id is not None:
            query = query.filter(Job.user_id == user_id)
        if active_only:
            query = query.filter(Job.is_active == True)
        items = query.order_by(Job.created_at.desc()).all()
        return {"total": len(items), "items": items}

    def update_job(self, job_id: int, data: JobUpdate) -> Job | None:
        job = self.get_job(job_id)
        if not job:
            return None
        for key, value in data.model_dump(exclude_none=True).items():
            setattr(job, key, value)
        self.db.commit()
        self.db.refresh(job)
        return job

    def delete_job(self, job_id: int) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        self.db.delete(job)
        self.db.commit()
        return True

    def screen_resumes(self, job_id: int, resume_ids: list[int] | None = None) -> dict:
        job = self.get_job(job_id)
        if not job:
            return {"job_id": job_id, "total": 0, "passed": 0, "rejected": 0, "results": []}

        # 排除已归档（archived）的简历，per-job 状态由 matching_results 管理
        query = self.db.query(Resume).filter(Resume.status != "rejected")
        if resume_ids:
            query = query.filter(Resume.id.in_(resume_ids))
        resumes = query.all()

        results = []
        passed_count = 0
        rejected_count = 0

        # F1: 决定使用 competency_model 还是扁平字段
        use_model = (
            job.competency_model is not None
            and job.competency_model_status == "approved"
        )

        # BUG-124: 学历门槛走统一 helper, 与 list_matched_for_job 同口径
        edu_req = effective_education_min(job)

        if use_model:
            cm = job.competency_model
            exp = cm.get("experience") or {}
            years_min = int(exp.get("years_min") or 0)
            years_max_val = exp.get("years_max")
            years_max = int(years_max_val) if years_max_val is not None else 99
            must_have_skills = [
                s["name"] for s in (cm.get("hard_skills") or []) if s.get("must_have")
            ]
        else:
            years_min = job.work_years_min
            years_max = job.work_years_max
            must_have_skills = [
                s.strip() for s in (job.required_skills or "").split(",") if s.strip()
            ]

        for resume in resumes:
            reject_reasons = []

            if edu_req:
                min_level = EDUCATION_LEVELS.get(edu_req, 0)
                resume_level = EDUCATION_LEVELS.get(resume.education, 0)
                if resume_level < min_level:
                    reject_reasons.append(
                        f"学历不符：要求{edu_req}，实际{resume.education or '未知'}"
                    )

            if resume.work_years < years_min:
                reject_reasons.append(
                    f"工作年限不足：要求{years_min}年，实际{resume.work_years}年"
                )
            if resume.work_years > years_max:
                reject_reasons.append(
                    f"工作年限超出：最高{years_max}年，实际{resume.work_years}年"
                )

            if job.salary_max > 0 and resume.expected_salary_min > 0:
                if resume.expected_salary_min > job.salary_max:
                    reject_reasons.append(
                        f"薪资期望过高：岗位上限{job.salary_max}，期望{resume.expected_salary_min}"
                    )

            if must_have_skills:
                resume_skills = (resume.skills or "").lower()
                resume_text = (resume.raw_text or "").lower()
                for skill in must_have_skills:
                    sk = skill.lower()
                    if sk not in resume_skills and sk not in resume_text:
                        reject_reasons.append(f"缺少必备技能：{skill}")

            is_passed = len(reject_reasons) == 0
            if is_passed:
                passed_count += 1
            else:
                rejected_count += 1

            results.append({
                "resume_id": resume.id,
                "resume_name": resume.name,
                "passed": is_passed,
                "reject_reasons": reject_reasons,
            })

        return {
            "job_id": job_id,
            "total": len(resumes),
            "passed": passed_count,
            "rejected": rejected_count,
            "results": results,
        }
