"""统一配置管理"""
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 应用
    app_name: str = "招聘助手"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    debug: bool = False

    # 数据库
    database_url: str = "sqlite:///./data/recruitment.db"

    # AI 功能开关
    ai_enabled: bool = False
    ai_provider: str = "openai_compatible"
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    ai_model_competency: str = ""
    """F1 能力模型抽取专用模型. 为空则回退 ai_model."""

    # F2 简历匹配模块配置
    matching_enabled: bool = True
    matching_evidence_llm_enabled: bool = True
    matching_trigger_days_back: int = 90
    matching_skill_sim_exact: float = 0.75
    matching_skill_sim_edge: float = 0.60
    matching_industry_sim: float = 0.70

    # 邮件 SMTP
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_ssl: bool = True

    # 邮件 IMAP
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_check_interval: int = 300

    # 飞书
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    # F-interview-eval：AI 面评完成后是否给触发分析的 HR 也推飞书卡片。默认 False —
    # HR 在 UI 已看到 done 状态，重复卡片噪声大；面试官始终推送。
    feishu_notify_trigger_hr: bool = False

    # 腾讯会议 - 账号池（逗号分隔的标签），每个标签对应 data/meeting_browser_{label}/ 的
    # Playwright 持久化 Chrome 目录，首次用新标签时需人工扫码登录一次
    tencent_meeting_accounts: str = "default"

    # F-interview-eval：AI 面评（默认关闭，凭证未配置时整模块不挂载）
    interview_eval_enabled: bool = False
    tencent_cloud_secret_id: str = ""
    tencent_cloud_secret_key: str = ""
    tencent_cloud_asr_region: str = "ap-shanghai"
    interview_eval_recording_retention_days: int = 180
    # 心跳自愈：worker 在每次 _set_status 时打心跳；超过 stale_threshold 无心跳→判死
    interview_eval_heartbeat_interval_seconds: int = 30
    interview_eval_stale_threshold_seconds: int = 180
    interview_eval_reconcile_period_seconds: int = 300

    # Boss 直聘
    boss_adapter: str = "edge_extension"
    boss_max_operations_per_hour: int = 30
    boss_max_operations_per_day: int = 200
    boss_delay_min: float = 3.0
    boss_delay_max: float = 8.0

    # F3 Boss 推荐牛人自动打招呼
    f3_default_greet_threshold: int = 60
    f3_default_daily_cap: int = 1000
    f3_ai_parse_enabled: bool = False

    # F4 IM 智能接待（后端调度器 + 扩展发件箱消费）
    f4_hard_max_asks: int = 3
    f4_pdf_timeout_hours: int = 72
    f4_ask_cooldown_hours: int = 24  # hard 槽位冷却期：上次问后 N 小时内不重问
    f4_soft_question_max: int = 3
    f4_daily_cap: int = 200  # Per-user daily autoscan tick cap
    ai_model_intake: str = ""
    jwt_secret: str = "agentichr-jwt-secret-change-in-production"
    cors_origins: str = "http://localhost:5173,http://localhost:8000,http://127.0.0.1:5173,http://127.0.0.1:8000"

    # F4 backend scheduler
    f4_scheduler_enabled: bool = False
    f4_scheduler_interval_sec: int = 300
    f4_expires_days: int = 14
    f4_claim_stale_minutes: int = 10  # reap claimed outbox older than this; extension polls 30s
    f4_outbox_max_age_hours: int = 24  # auto-expire pending rows scheduled this many hours ago — defends against
                                        # scheduler tick that ran while extension was offline; row stays stale-pending
                                        # for days, then fires when extension reconnects (e.g. after weekend).
    f4_max_chat_messages: int = 500

    # F3.1 Boss chat deep link
    boss_chat_url_template: str = "https://www.zhipin.com/web/chat/index?id={boss_id}"

    # 简历存储路径
    resume_storage_path: str = "./data/resumes"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
