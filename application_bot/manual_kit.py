"""将已审阅的私有申请材料包制作为离线手动申请包。"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from string import Template
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from private_paths import APPLICATION_OUTPUT, PRIVATE_ROOT  # noqa: E402

TEMPLATES = Path(__file__).with_name("templates") / "manual_kit"
STATUSES = ("未开始", "填写中", "已提交", "受阻", "跳过")
ROLE_FILES = (
    "Application_Data.json", "Answers.txt", "Cover_Letter.txt",
    "Job_Description.txt", "Observed_Form_Fields.json", "Online_Profile_CN.txt",
)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def asset(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or not path.parts:
        raise ValueError("材料路径必须相对于源材料包")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError("引用的材料不存在或位于材料包之外")
    return resolved


def link(label: str, url: str, *, external: bool = False) -> str:
    if external:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
            raise ValueError("申请链接必须是无凭据的 HTTPS 网址")
        attrs = ' target="_blank" rel="noopener noreferrer"'
    else:
        url = quote(url, safe="/.")
        attrs = ""
    return f'<a href="{esc(url)}"{attrs}>{esc(label)}</a>'


def page(template: str, **values: str) -> str:
    values.update(
        style=(TEMPLATES / "style.css").read_text(encoding="utf-8"),
        script=(TEMPLATES / "manual_kit.js").read_text(encoding="utf-8"),
    )
    return Template((TEMPLATES / template).read_text(encoding="utf-8")).substitute(values)


def answer_sections(data: dict) -> str:
    sections = []
    counter = 0
    titles = {
        "common_fields": "基本填写资料", "regional_authorization": "工作许可与赞助",
        "role_specific_answers": "本岗答案", "education": "教育经历",
        "work_experience": "工作与研究经历", "projects": "项目经历",
        "portal_notes": "门户提示", "transcript_verified_education_facts": "成绩单说明",
    }
    for key, title in titles.items():
        value = data.get(key)
        if not value:
            continue
        groups = value if isinstance(value, list) else [value]
        rows = []
        for group in groups:
            fields = group.items() if isinstance(group, dict) else [(title, group)]
            for label, answer in fields:
                counter += 1
                if answer is None or answer == "":
                    text = "未提供：仅如实填写，不能编造"
                elif isinstance(answer, (dict, list)):
                    text = json.dumps(answer, ensure_ascii=False, indent=2)
                else:
                    text = str(answer)
                rows.append(f'<tr><td>{esc(label)}</td><td id="v{counter}">{esc(text)}</td>'
                            f'<td><button data-copy="v{counter}">复制</button></td></tr>')
        sections.append(f'<section><h2>{esc(title)}</h2><table><tbody>'
                        + "".join(rows) + "</tbody></table></section>")
    return "".join(sections)


def render_kit(manifest_path: Path, output: Path, progress_key: str) -> dict:
    """将已审阅材料复制到新的私有目录，并保留原始序号/顺序。

    进度始终从用户的浏览器中读取；清单中的状态值不会初始化进度。此函数不会打开门户，也不会访问数据库。
    """
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", progress_key):
        raise ValueError("请使用明确且稳定的批次进度键")
    source = manifest_path.resolve().parent
    output = output.resolve()
    if not output.is_relative_to(PRIVATE_ROOT.resolve()) or output == PRIVATE_ROOT.resolve():
        raise ValueError("输出必须位于规范私有根目录内")
    if output.exists():
        raise FileExistsError("输出目录已存在；必须保留现有材料包和进度")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    jobs = manifest["jobs"]
    if not jobs:
        raise ValueError("清单必须至少包含一个目标岗位")
    ranks = [j["rank"] for j in jobs]
    folders = [j["folder"] for j in jobs]
    if any(type(r) is not int or r < 1 for r in ranks) or len(set(ranks)) != len(ranks):
        raise ValueError("原始序号必须是唯一的正整数")
    if len(set(folders)) != len(folders):
        raise ValueError("岗位文件夹名称必须唯一")
    copies: dict[str, Path] = {}
    rendered: dict[str, str] = {}
    cards = []
    support = []

    def include(relative: str, expected_hash: str | None = None) -> None:
        f = asset(source, relative)
        if expected_hash and hashlib.sha256(f.read_bytes()).hexdigest() != expected_hash:
            raise ValueError("材料哈希与已审阅清单不匹配")
        copies[relative] = f

    for document in manifest.get("supporting_documents", []):
        relative = "Supporting_Documents/" + document["filename"]
        include(relative, document["sha256"])
        support.append(link(document["filename"], relative))
    for job in jobs:
        rank, folder = job["rank"], job["folder"]
        if len(PurePosixPath(folder).parts) != 1 or folder in (".", "..") or "\\" in folder:
            raise ValueError("每个岗位文件夹必须是单层相对目录")
        if job["role_kind"] not in ("full_time", "internship", "unknown"):
            raise ValueError("无法识别的岗位类型")
        pdfs = job["pdfs"]
        if not pdfs:
            raise ValueError("岗位必须包含已审阅的 PDF 材料")
        for pdf in pdfs:
            if PurePosixPath(pdf["pdf"]).suffix.lower() != ".pdf":
                raise ValueError("已审阅的 PDF 条目必须指向 PDF 文件")
            if pdf.get("visual_review") != "passed":
                raise ValueError("PDF 材料必须明确标记为视觉审阅通过")
            include(pdf["pdf"], pdf["sha256"])
        for filename in ROLE_FILES:
            relative = folder + "/" + filename
            if (source / relative).exists():
                include(relative)
        data = json.loads(asset(source, folder + "/Application_Data.json").read_text(encoding="utf-8"))
        if data.get("rank") != rank:
            raise ValueError("岗位数据与原始序号不匹配")
        if data.get("official_job_url") and data["official_job_url"] != job["url"]:
            raise ValueError("岗位数据与清单中的目标网址不匹配")
        title = f'{rank:02d} {job["company"]} · {job["title"]}'
        apply = job.get("apply_url") or job["url"]
        portal = link("进入申请入口", apply, external=True)
        docs = [(p["document"], p["pdf"]) for p in pdfs]
        summary_links = [link("打开本岗填写页", folder + "/index.html")]
        summary_links.extend(link(label, path) for label, path in docs)
        summary_links.append(portal)
        detail_links = [link("返回总表", "../index.html"), portal]
        detail_links.extend(link(label, "../" + path) for label, path in docs)
        detail_links.extend(link(name, name) for name in ROLE_FILES if folder + "/" + name in copies)
        detail_support = " ".join(link(d["filename"], "../Supporting_Documents/" + d["filename"])
                                  for d in manifest.get("supporting_documents", []))
        gap = esc(job.get("known_gaps") or "按实际门户逐项核对资格及材料要求。")
        rendered[folder + "/index.html"] = page(
            "job.html", title=esc(title), metadata=esc(f'{job.get("location", "")} · {job.get("requisition", "")}'),
            links=" ".join(detail_links), gaps=gap, supporting=detail_support,
            sections=answer_sections(data),
        )
        options = "".join(f'<option>{esc(s)}</option>' for s in STATUSES)
        kind = "实习" if job["role_kind"] == "internship" else "正职" if job["role_kind"] == "full_time" else "待分类"
        cards.append(f'<section class="card" data-kind="{esc(job["role_kind"])}" data-rank="{rank}">'
                     f'<h2>{esc(title)}</h2><p>{esc(job.get("location", ""))} · {esc(job.get("requisition", ""))} '
                     f'<span class="tag">{kind}</span></p><div class="actions">{" ".join(summary_links)}</div>'
                     f'<p class="warn">{gap}</p><label>进度 <select id="status-{rank}">{options}</select></label> '
                     f'<label>回执 <input id="receipt-{rank}" placeholder="成功提示、编号或备注"></label></section>')
    title = manifest.get("title") or f"{len(jobs)} 岗手动投递包"
    handoff_prompt = (
        f"读取投递包 {output} 中的 AGENT_HANDOFF.md 和 Manifest.json，"
        "再读项目 AGENTS.md、根目录 AGENT_HANDOFF.md 和最新私有交接。"
        "保留原序号、目标、材料和我的浏览器进度，"
        "继续处理我提供的当前岗位或成功回执。实际手动进度须据实核对，"
        "不要代我最终提交。"
    )
    rendered["index.html"] = page("index.html", title=esc(title), progress_key=esc(progress_key),
                                  supporting=" ".join(support), cards="".join(cards),
                                  handoff_prompt=esc(handoff_prompt))
    rendered["AGENT_HANDOFF.md"] = Template(
        (TEMPLATES / "handoff.md").read_text(encoding="utf-8")
    ).substitute(output=str(output), count=str(len(jobs)), progress_key=progress_key,
                 manifest_sha256=hashlib.sha256(manifest_text.encode("utf-8")).hexdigest())
    # 所有源文件校验和渲染都会在创建任何输出目录之前完成。
    output.mkdir(parents=True, mode=0o700)
    try:
        for relative, f in copies.items():
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(f, target)
            target.chmod(0o600)
        for relative, content in rendered.items():
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_text(content, encoding="utf-8")
            target.chmod(0o600)
        target = output / "Manifest.json"
        target.write_text(manifest_text, encoding="utf-8")
        target.chmod(0o600)
    except Exception:
        shutil.rmtree(output)
        raise
    return {"jobs": len(jobs), "files_copied": len(copies), "pages": len(rendered), "output": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=APPLICATION_OUTPUT / "manual_kit")
    parser.add_argument("--progress-key", required=True, help="此批次专用且稳定的浏览器键")
    args = parser.parse_args()
    try:
        result = render_kit(args.manifest, args.output, args.progress_key)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, "生成手动材料包失败：请检查清单、已审阅材料和新的私有输出目录。\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
