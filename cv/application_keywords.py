"""供简历与申请材料准备共用的、限定于证据范围内的关键词选择工具。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

from private_paths import APPLICATION_KEYWORDS


def _present(term: str, text: str) -> bool:
    term = re.sub(r"\s+", " ", term.casefold()).strip()
    text = re.sub(r"\s+", " ", text.casefold())
    if not term:
        return False
    if term.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None
    return term in text


def select_keywords(role: str, job_description: str = "", *,
                    library_path: Path | None = None, preset: str | None = None) -> dict:
    """每次调用时读取当前私有数据；绝不将 JD 词语直接转成技能。"""
    path = Path(library_path) if library_path is not None else APPLICATION_KEYWORDS
    result = {"available": False, "library_path": str(path), "preset": None,
              "technical": [], "collaboration": [], "usage_rules": []}
    if not path.is_file():
        return {**result, "reason": "keyword_library_missing"}
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("schema_version") != 1:
        raise ValueError("不支持的申请关键词库 schema")
    entries = data["technical_keywords"] + data["collaboration_personality_keywords"]
    ids = [x["id"] for x in entries]
    if len(set(ids)) != len(ids):
        raise ValueError("申请关键词 ID 重复")
    for entry in entries:
        if not all(entry.get(k) for k in ("english", "chinese", "evidence", "example_en", "source_ids", "claim_status")):
            raise ValueError(f"关键词缺少证据或必需字段：{entry['id']}")
        if any(s not in data["sources"] for s in entry["source_ids"]):
            raise ValueError(f"未知的证据来源：{entry['id']}")
    presets = {x["id"]: x for x in data["role_presets"]}
    technical_ids = {x['id'] for x in data['technical_keywords']}
    soft_ids = {x['id'] for x in data['collaboration_personality_keywords']}
    for item in presets.values():
        if not set(item['technical_ids']) <= technical_ids or not set(item['collaboration_ids']) <= soft_ids:
            raise ValueError(f"关键词预设引用无效：{item['id']}")
    if preset is not None:
        if preset not in presets:
            raise ValueError(f"未知关键词预设：{preset}")
        chosen = presets[preset]
    else:
        # 优先使用职位名称；长篇 JD 中的通用要求不得覆盖它。
        chosen = None
        for text in (role, job_description):
            hits = [(sum(_present(t, text) for t in x.get('role_match_terms', [])), x)
                    for x in presets.values()]
            hits = [(score, x) for score, x in hits if score]
            if hits:
                chosen = max(hits, key=lambda pair: pair[0])[1]
                break
    text = role + "\n" + job_description
    def choose(key: str, preset_key: str, allowed: set[str], limit: int) -> list[dict]:
        candidates = [x for x in data[key] if x['claim_status'] in allowed]
        by_id = {x['id']: x for x in candidates}
        preferred = chosen[preset_key] if chosen else []
        # 岗位预设定义相关性；JD 中的明确匹配项决定其条目顺序。
        order = {value: i for i, value in enumerate(preferred)}
        scored = []
        for entry in candidates:
            terms = [entry['english'], entry['chinese'], *entry.get('match_terms', [])]
            matched = list(dict.fromkeys(t for t in terms if _present(t, text)))
            if entry['id'] in preferred or (not chosen and matched):
                scored.append((entry, matched))
        scored.sort(key=lambda pair: (-len(pair[1]), order.get(pair[0]['id'], len(order))))
        return [{**copy.deepcopy(by_id[e['id']]), 'matched_terms': terms,
                 'selection_reason': 'role_preset' if e['id'] in preferred else 'literal_job_match'}
                for e, terms in scored[:limit]]
    return {**result, 'available': True, 'library_sha256': hashlib.sha256(raw).hexdigest(),
            'library_updated_at': data.get('updated_at'),
            'preset': chosen['id'] if chosen else None,
            'sources': data['sources'], 'usage_rules': data['usage_rules'],
            'technical': choose('technical_keywords', 'technical_ids', {'supported_by_existing_records'}, 10),
            'collaboration': choose('collaboration_personality_keywords', 'collaboration_ids',
                                    {'documented_behavior', 'behavior_based_interpretation'}, 5)}


def apply_keyword_selection(profile: dict, selection: dict) -> dict:
    """保留手动填写的技能/答案；技能列表为空时，根据有证据支持的术语填充。"""
    profile = copy.deepcopy(profile)
    previous = profile.get('application_keywords', {})
    labels = [x['english'] for x in selection['technical']]
    old_generated = previous.get('generated_skills')
    if selection['available'] and (not profile.get('skills') or
                                  (old_generated is not None and profile.get('skills') == old_generated)):
        profile['skills'] = labels
        selection = {**selection, 'generated_skills': labels}
    profile['application_keywords'] = copy.deepcopy(selection)
    return profile


def render_keyword_notes(selection: dict) -> str:
    lines = ['## 申请关键词', '']
    if not selection['available']:
        return '\n'.join(lines + ['关键词库不可用；现有材料保持不变。', ''])
    lines += [f"岗位预设：{selection['preset'] or '仅匹配字面词项'}",
              f"资料库 SHA-256：{selection['library_sha256']}", '',
              '技术标签限定于以下证据范围。协作示例是基于行为的建议，',
              '不是已经确认的性格自评。', '']
    for label, key in [('技术', 'technical'), ('协作/工作方式', 'collaboration')]:
        lines += ['### ' + label, '']
        for entry in selection[key]:
            lines += [f"- **{entry['english']}** ({entry['chinese']}): {entry['example_en']}",
                      f"  证据/范围：{entry['evidence']}"]
        lines += ['']
    lines += ['### 使用规则', ''] + ['- ' + rule for rule in selection['usage_rules']] + ['']
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', required=True)
    parser.add_argument('--job-description', type=Path)
    parser.add_argument('--library', type=Path)
    parser.add_argument('--preset')
    args = parser.parse_args()
    jd = args.job_description.read_text(encoding='utf-8') if args.job_description else ''
    print(json.dumps(select_keywords(args.role, jd, library_path=args.library, preset=args.preset),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
