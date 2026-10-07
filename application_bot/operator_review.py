"""将独立记录的审阅轮次绑定到当前私有证据文件。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from private_paths import APPLICATION_OUTPUT, PRIVATE_ROOT, APPLICATION_PROFILE, EVIDENCE_PROFILE, PROJECT_ROOT

CHECKS = ("qualification", "fields", "attachments")


def review_path(application_id: int) -> Path:
    return APPLICATION_OUTPUT / "operator_reviews" / f"application_{application_id}.json"


def evidence_hash(path: str) -> str:
    target = Path(path).expanduser().resolve()
    if not target.is_relative_to(PRIVATE_ROOT.resolve()) or not target.is_file():
        raise ValueError("审查证据须为私有目录中的现存文件")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def validate_review(application_id: int, required_rounds: int, path: Path | None = None, expected_files: dict | None = None) -> None:
    try:
        data = json.loads((path or review_path(application_id)).read_text(encoding="utf-8"))
        if type(data["application_id"]) is not int or data["application_id"] != application_id:
            raise ValueError("审查记录与岗位编号不一致")
        rounds = data["rounds"]
        if not isinstance(rounds, list) or len(rounds) < required_rounds:
            raise ValueError("投递前审查轮数不足")
        for index, row in enumerate(rounds[:required_rounds], 1):
            if type(row["round"]) is not int or row["round"] != index or not isinstance(row["reviewer"], str) or not row["reviewer"].strip() or not isinstance(row["reviewed_at"], str) or not row["reviewed_at"].strip():
                raise ValueError("审查轮号、审查者或时间缺失")
            for check in CHECKS:
                item = row[check]
                if item["passed"] is not True or not isinstance(item["note"], str) or not item["note"].strip():
                    raise ValueError("资格、字段和附件必须逐项审查通过并填写说明")
                evidence = item["evidence"]
                if not isinstance(evidence, list) or not evidence:
                    raise ValueError("每项审查须有文件证据")
                for entry in evidence:
                    if evidence_hash(entry["path"]) != entry["sha256"]:
                        raise ValueError("审查后证据已变化，请重新审查")
                actual = {str(Path(entry["path"]).resolve()) for entry in evidence}
                required = {str(Path(file).resolve()) for file in (expected_files or {}).get(check, [])}
                if not required.issubset(actual):
                    raise ValueError("审查证据未覆盖当前岗位绑定的个人档案或实际附件")
                if check == "attachments" and not any(str(entry["path"]).lower().endswith(".pdf") for entry in evidence):
                    raise ValueError("附件审查须包含已视觉审阅的PDF文件")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("缺少有效的投递前审查记录，请先完成Agent任务包") from exc


def job_fingerprint(url: str, description: str) -> str:
    return hashlib.sha256(json.dumps([url or "", description or ""], ensure_ascii=False).encode()).hexdigest()


def validate_application_review(conn, application_id: int, required_rounds: int) -> None:
    row = conn.execute("SELECT applications.profile_path,jobs.url,jobs.description FROM applications JOIN jobs ON jobs.id=applications.job_id WHERE applications.id=?", (application_id,)).fetchone()
    if row is None:
        raise ValueError("不存在指定投递记录")
    profile = Path(row[0] or APPLICATION_PROFILE)
    if not profile.is_absolute():
        profile = PROJECT_ROOT / profile
    try:
        evidence_hash(str(profile))
        docs = json.loads(profile.read_text(encoding="utf-8"))["documents"]
        attachments = []
        for key in ("resume_path", "cover_letter_path"):
            if docs.get(key):
                document = Path(docs[key])
                if not document.is_absolute():
                    document = PROJECT_ROOT / document
                attachments.append(document)
        if not attachments or not any(file.suffix.lower() == ".pdf" for file in attachments):
            raise ValueError("当前岗位未绑定可审查的PDF附件")
        record = json.loads(review_path(application_id).read_text(encoding="utf-8"))
        if record.get("job_fingerprint") != job_fingerprint(row[1], row[2]):
            raise ValueError("岗位JD或URL已变化，请重新审查")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("缺少岗位绑定档案、附件或审查记录") from exc
    validate_review(application_id, required_rounds, expected_files={
        "qualification": [APPLICATION_PROFILE, EVIDENCE_PROFILE], "fields": [profile], "attachments": attachments})


def review_template(application_id: int, required_rounds: int) -> dict:
    return {"application_id": application_id, "job_fingerprint": "", "rounds": [
        {"round": number, "reviewer": "", "reviewed_at": "", **{
            check: {"passed": False, "note": "", "evidence": []} for check in CHECKS}}
        for number in range(1, required_rounds + 1)]}
