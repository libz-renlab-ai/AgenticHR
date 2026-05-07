# -*- coding: utf-8 -*-
"""PDF resume parsing with text extraction, regex fields, and AI vision support."""
import re
import json
import logging
from pathlib import Path

from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)


def parse_pdf(file_path: str) -> str:
    """Extract all text from a PDF file. Returns empty string on failure."""
    path = Path(file_path)
    if not path.exists():
        logger.warning(f"PDF not found: {file_path}")
        return ""
    try:
        reader = PdfReader(file_path)
        parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        return "\n".join(parts)
    except Exception as e:
        logger.error(f"PDF parse error [{file_path}]: {e}")
        return ""


def is_image_pdf(file_path: str) -> bool:
    """Check if a PDF is image-based or has garbled text that needs vision model."""
    text = parse_pdf(file_path)
    clean = re.sub(r'\s+', '', text)

    if len(clean) < 50:
        return True

    readable = len(re.findall(r'[\u4e00-\u9fffa-zA-Z0-9@.,;:!?\-/()]', clean))
    ratio = readable / max(len(clean), 1)
    if ratio < 0.5:
        return True

    if re.search(r'[a-zA-Z0-9_]{40,}', clean[:200]):
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', clean[:500]))
        if chinese_count < 10:
            return True

    return False


def extract_boss_qr(pdf_path: str, output_path: str) -> str:
    """从简历 PDF 首页**左上角固定坐标 (0, 0, 80, 80) 点**裁剪二维码图片。

    Boss / 拉勾 / 51job 等多数招聘网站把候选人扫码 QR 放在简历首页左上角顶部，
    QR 本体约 60-65 pt 宽，外加 10-15 pt 安全边距。80 pt 区域足以完整覆盖
    QR 的 3 个对齐标记（右上 / 左上 / 左下），保证扫码可读。

    300 DPI 渲染 → 334×334 px PNG，约 5KB，扫码识别率良好。

    成功返回 output_path，失败返回空字符串。
    """
    try:
        import fitz
        doc = fitz.open(pdf_path)
        if doc.page_count == 0:
            doc.close()
            return ""
        page = doc[0]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        crop_rect = fitz.Rect(0, 0, 80, 80)
        pix = page.get_pixmap(clip=crop_rect, dpi=300)
        pix.save(output_path)
        doc.close()
        logger.info(f"QR extracted (top-left 80pt fixed crop): {output_path}")
        return output_path
    except Exception as e:
        logger.error(f"QR extract error [{pdf_path}]: {e}")
        return ""


def pdf_to_images(file_path: str, max_pages: int = 3, dpi: int = 120) -> list[bytes]:
    """Convert PDF pages to PNG images using PyMuPDF."""
    try:
        import fitz
        doc = fitz.open(file_path)
        images = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(dpi=dpi)
            images.append(pix.tobytes("png"))
        doc.close()
        return images
    except Exception as e:
        logger.error(f"PDF to image error [{file_path}]: {e}")
        return []


def parse_boss_filename(filename: str) -> dict:
    """Parse Boss Zhipin PDF filename to extract name, phone, email."""
    result = {"name": "", "phone": "", "email": ""}
    if not filename:
        return result

    base = re.sub(r'\.pdf$', '', filename, flags=re.IGNORECASE)

    phone_match = re.search(r'1[3-9]\d{9}', base)
    if phone_match:
        result["phone"] = phone_match.group()

    email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', base)
    if email_match:
        result["email"] = email_match.group()

    name_text = base
    if result["phone"]:
        name_text = name_text.replace(result["phone"], "")
    if result["email"]:
        name_text = name_text.replace(result["email"], "")

    parts = re.split(r'[-_\s]+', name_text)
    skip = {'main', 'resume', 'pdf', 'doc'}
    for part in parts:
        part = part.strip()
        if not part or part.lower() in skip:
            continue
        if re.match(r'^[\u4e00-\u9fff]{2,4}$', part):
            result["name"] = part
            break

    return result


def extract_resume_fields(text: str) -> dict:
    """Extract structured fields from resume text using regex."""
    fields = {"name": "", "phone": "", "email": "", "education": "", "skills": "", "work_experience": ""}

    phone_match = re.search(r"1[3-9]\d{9}", text)
    if phone_match:
        fields["phone"] = phone_match.group()

    email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    if email_match:
        fields["email"] = email_match.group()

    for edu in ["博士", "硕士", "本科", "大专"]:
        if edu in text:
            fields["education"] = edu
            break

    return fields


VISION_PROMPT = (
    "请从这张简历图片中提取信息，只返回一个JSON对象，不要输出任何其他内容。\n"
    "所有字段值必须是简单的字符串或数字，不要嵌套对象或数组。\n"
    "每个文本字段不超过200字。学历只填：博士/硕士/本科/大专。\n"
    "bachelor_school填本科学校名，master_school填硕士/研究生学校名，phd_school填博士学校名；没有就填空字符串。\n"
    '{"name":"姓名","phone":"手机号","email":"邮箱","education":"最高学历",'
    '"bachelor_school":"本科学校","master_school":"硕士学校","phd_school":"博士学校",'
    '"work_years":工作年限数字,"skills":"技能1,技能2,技能3",'
    '"work_experience":"公司-职位-工作内容摘要",'
    '"project_experience":"项目名称-项目描述摘要",'
    '"self_evaluation":"自我评价摘要","job_intention":"求职意向",'
    '"seniority":"候选人职级，从work_experience推断，取值：初级/中级/高级/专家（无法判断输出\'中级\'）"}'
)

AI_TEXT_PROMPT = (
    '你是一个专业的简历解析助手。请从以下简历文本中提取信息。\n'
    '所有字段值必须是简单的字符串或数字，不要嵌套对象或数组。\n'
    '没有的字段填空字符串，学历只填：博士/硕士/本科/大专。\n'
    'bachelor_school填本科学校名，master_school填硕士/研究生学校名，phd_school填博士学校名。\n'
    '只输出JSON，不要输出任何其他内容。\n\n'
    '## 简历内容\n{text}\n\n'
    '## JSON格式：\n'
    '{{"name":"姓名","phone":"手机号","email":"邮箱","education":"最高学历",'
    '"bachelor_school":"本科学校","master_school":"硕士学校","phd_school":"博士学校",'
    '"work_years":工作年限数字,"skills":"技能1,技能2,技能3",'
    '"work_experience":"公司-职位-工作内容摘要",'
    '"project_experience":"项目名称-项目描述摘要",'
    '"self_evaluation":"自我评价摘要","job_intention":"求职意向",'
    '"seniority":"候选人职级，从work_experience推断，取值：初级/中级/高级/专家（无法判断输出\'中级\'）"}}'
)


def _extract_json(text: str) -> dict:
    """Extract JSON from AI response that may contain markdown code blocks."""
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return json.loads(text.strip())


# BUG-126: 部分 LLM 偶尔输出 "研究生|硕士" / "本科 / 硕士" / "硕士在读" / "研究生" 等
# 非规范学历字符串, 导致 EDUCATION_LEVELS / meets_education 拿到 0 ord 直接学历不达标。
# 在写库前规范化为 大专 / 本科 / 硕士 / 博士 / "" 之一, 并取候选中最高的那级。
_EDU_KEYWORDS = (
    ("博士", "博士"),
    ("phd", "博士"),
    ("doctor", "博士"),
    ("硕士", "硕士"),
    ("研究生", "硕士"),
    ("master", "硕士"),
    ("mba", "硕士"),
    ("emba", "硕士"),
    ("本科", "本科"),
    ("学士", "本科"),
    ("bachelor", "本科"),
    ("undergrad", "本科"),
    ("大专", "大专"),
    ("专科", "大专"),
    ("高职", "大专"),
    ("associate", "大专"),
)
_EDU_RANK = {"": 0, "大专": 1, "本科": 2, "硕士": 3, "博士": 4}


def normalize_education(raw: str | None) -> str:
    """规范化任意 LLM 输出的学历字符串到 大专/本科/硕士/博士/'' 之一。

    多个候选词出现时, 取最高那级 (e.g. "本科/硕士在读" → "硕士").
    """
    if not raw:
        return ""
    t = str(raw).lower()
    best_rank = 0
    best_label = ""
    for kw, label in _EDU_KEYWORDS:
        if kw in t:
            rank = _EDU_RANK[label]
            if rank > best_rank:
                best_rank = rank
                best_label = label
    return best_label


async def ai_parse_resume_vision(file_path: str, ai_provider) -> dict:
    """Use vision model to parse image-based or garbled PDFs page by page."""
    import base64
    import httpx

    images = pdf_to_images(file_path, max_pages=3, dpi=120)
    if not images:
        logger.error(f"Cannot convert PDF to images: {file_path}")
        return {}

    merged = {}

    for page_idx, img_bytes in enumerate(images):
        img_b64 = base64.standard_b64encode(img_bytes).decode('utf-8')
        content = [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            {"type": "text", "text": VISION_PROMPT},
        ]

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{ai_provider.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {ai_provider.api_key}", "Content-Type": "application/json"},
                    json={"model": "glm-4v-flash", "messages": [{"role": "user", "content": content}], "max_tokens": 1024},
                )
                resp.raise_for_status()
                page_result = _extract_json(resp.json()["choices"][0]["message"]["content"])
                logger.info(f"Vision page {page_idx+1}: {page_result.get('name', '?')}")

                for key, val in page_result.items():
                    # flatten dict/list to string
                    if isinstance(val, (dict, list)):
                        val = str(val)
                    if not val:
                        continue
                    if not merged.get(key):
                        merged[key] = val
                    elif key in ("skills", "work_experience", "project_experience"):
                        if str(val) not in str(merged[key]):
                            sep = ", " if key == "skills" else "\n"
                            merged[key] = f"{merged[key]}{sep}{val}"

        except Exception as e:
            logger.error(f"Vision page {page_idx+1} failed: {e}")

    if merged:
        logger.info(f"Vision parse done: {merged.get('name', '?')}")
    return merged


async def ai_parse_resume(raw_text: str, ai_provider) -> dict:
    """Use text AI model to parse resume text into structured fields.
    """
    import httpx

    prompt = AI_TEXT_PROMPT.format(text=raw_text[:8000])

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{ai_provider.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {ai_provider.api_key}", "Content-Type": "application/json"},
                json={"model": ai_provider.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            )
            resp.raise_for_status()
            result = _extract_json(resp.json()["choices"][0]["message"]["content"])
            logger.info(f"AI text parse done: {result.get('name', '?')}")
            return result
    except Exception as e:
        logger.error(f"AI text parse failed: {e}")
        return {}
