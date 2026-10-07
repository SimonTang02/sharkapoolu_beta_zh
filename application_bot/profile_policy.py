"""明确授权的申请答案与地域保护措施。"""

from __future__ import annotations

import re
from typing import Any


CHINA_LOCATION_MARKERS = (
    "china",
    "中国",
    "北京",
    "上海",
    "深圳",
    "广州",
    "成都",
    "武汉",
    "杭州",
    "南京",
    "苏州",
    "西安",
    "合肥",
    "天津",
    "珠海",
    "东莞",
    "佛山",
    "宁波",
    "厦门",
    "青岛",
)
HONG_KONG_LOCATION_MARKERS = ("hong kong", "香港")


def location_in_authorized_scope(location: str, scopes: list[str]) -> bool:
    normalized = (location or "").casefold()
    # 招聘职位涉及多个国家时，仅覆盖其中一个办公室并不足够。
    parts = re.split(r"[;|/]|\bor\b", normalized)
    if len(parts) > 1:
        return all(location_in_authorized_scope(part.strip(), scopes) for part in parts)
    if re.search(
        r"\b(united states|usa|us|united kingdom|uk|singapore|india|canada|"
        r"taiwan|macau|macao|australia|germany|netherlands|japan)\b|"
        r"美国|英国|新加坡|印度|加拿大|台湾|澳門|澳门|澳大利亚|德国|日本",
        normalized,
    ):
        return False
    # “Hong Kong, China” 不得继承仅适用于中国大陆的工作许可。
    if any(marker in normalized for marker in HONG_KONG_LOCATION_MARKERS):
        remainder = re.sub(r"hong kong|香港|china|中国|sar|特别行政区", "", normalized)
        return "hong_kong" in scopes and not re.sub(r"[\s,，()（）.\-]+", "", remainder)
    if "mainland_china" in scopes and any(
        marker.casefold() in normalized for marker in CHINA_LOCATION_MARKERS
    ):
        # 拒绝无法识别的地域片段，不要因为字符串中含有
        # “China” 就接受（例如“China, Shanghai, London”）。
        known = list(CHINA_LOCATION_MARKERS) + [
            "shanghai", "beijing", "shenzhen", "guangzhou", "chengdu", "wuhan",
            "hangzhou", "nanjing", "suzhou", "xi'an", "xian", "hefei",
            "tianjin", "zhuhai", "dongguan", "foshan", "ningbo", "xiamen",
            "qingdao", "mainland", "中国大陆", "中国内地",
        ]
        remainder = normalized
        for marker in sorted(known, key=len, reverse=True):
            remainder = re.sub(re.escape(marker), "", remainder)
        return not re.sub(r"[\s,，()（）.\-]+", "", remainder)
    return False


def is_work_permission_question(question: str) -> bool:
    """即使在旧版自定义答案映射中，也识别法律/签证问题的答案。"""
    return bool(re.search(
        r"authori[sz]ed to work|right to work|sponsor|work permit|"
        r"employer support.*authori[sz]ation|合法工作|工作许可|工作簽證|工作签证",
        question, re.I,
    ))


def has_confirmed_work_permission_scope(profile: dict[str, Any], job_location: str) -> bool:
    authorization = profile.get("explicit_authorization", {})
    return bool(
        authorization.get("user_confirmed") is True
        and location_in_authorized_scope(
            job_location, authorization.get("location_scopes", [])
        )
    )


def apply_explicit_authorization(
    profile: dict[str, Any],
    *,
    gender: str,
    work_authorized: bool,
    sponsorship_required: bool,
    scopes: list[str],
    amd_privacy_accepted: bool,
) -> dict[str, Any]:
    """仅保存用户明确提供的答案。"""
    gender_value = {
        "male": "Male",
        "female": "Female",
        "decline": "Decline to State",
    }[gender]
    yes_no_authorized = "Yes" if work_authorized else "No"
    yes_no_sponsorship = "Yes" if sponsorship_required else "No"

    authorization = profile.setdefault("explicit_authorization", {})
    authorization.update(
        {
            "user_confirmed": True,
            "location_scopes": list(scopes),
            "work_authorized": work_authorized,
            "sponsorship_required": sponsorship_required,
            "company_consents": {
                "amd_applicant_privacy_statement": amd_privacy_accepted,
            },
        }
    )
    profile.setdefault("voluntary_disclosures", {})["gender"] = gender_value
    answers = profile.setdefault("custom_answers", {})
    answers[
        "Are you legally authorized to work in the country where this position is located?"
    ] = yes_no_authorized
    answers[
        "Will you require employer support to obtain or maintain authorization to work in that country?"
    ] = yes_no_sponsorship
    profile["answer_rules"] = [
        {
            "id": "work_authorization",
            "patterns": [
                "legally authorized to work",
                "authorized to work in",
                "right to work",
                "合法工作身分",
                "合法工作身份",
                "工作许可",
            ],
            "answer": yes_no_authorized,
            "requires_location_scope": True,
        },
        {
            "id": "sponsorship",
            "patterns": [
                "require sponsorship",
                "visa sponsorship",
                "employer support to obtain or maintain authorization",
                "协助处理工作签证",
                "協助處理工作簽證",
            ],
            "answer": yes_no_sponsorship,
            "requires_location_scope": True,
        },
    ]
    safety = profile.setdefault("safety", {})
    safety["allow_sensitive_answers"] = True
    safety["allow_server_draft"] = True
    safety["allow_submit"] = False
    return profile
