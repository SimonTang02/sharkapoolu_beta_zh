"""预览并将人工维护的 UTF-8 CSV 导入权威数据库。"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from private_paths import APPLICATION_OUTPUT, PROJECT_ROOT
from job_bot.bot import JobPosting, connect_db, load_config, score_job, upsert_job, utc_now
from job_bot.application_bot import add_event

JOB_COLUMNS=('company','title','url','location','platform','role_kind','description','external_id','recruitment_category','published_at')
APP_COLUMNS=('job_url','status','confirmed_success','confirmation_number','submitted_at','confirmation_evidence','notes')


def valid_url(url: str) -> bool:
    parsed=urlsplit(url)
    return parsed.scheme=='https' and bool(parsed.hostname) and not parsed.username and not parsed.password


def read_rows(path: Path, columns: tuple[str,...]) -> list[dict]:
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError('CSV列名或顺序不符合模板；请保留首行')
        result=[]
        for number,row in enumerate(reader,2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'CSV第{number}行列数不正确，含逗号/换行的文本须加双引号')
            row={key:value.strip() for key,value in row.items()}
            if any(row.values()): result.append(row)
            if len(result)>1000: raise ValueError('一次最多导入1000行')
    return result


def validate_rows(jobs: list[dict], applications: list[dict], confirm_submissions: bool=False):
    urls=set()
    for number,row in enumerate(jobs,2):
        if not row['company'] or not row['title'] or not valid_url(row['url']):
            raise ValueError(f'岗位第{number}行须有公司、标题、HTTPS官网URL')
        if row['url'] in urls: raise ValueError(f'岗位第{number}行URL重复')
        urls.add(row['url'])
        if row['role_kind'] not in ('internship','full_time','unknown'):
            raise ValueError(f'岗位第{number}行role_kind须为internship/full_time/unknown')
        if row['published_at']:
            try: datetime.fromisoformat(row['published_at'].replace('Z','+00:00'))
            except ValueError as exc: raise ValueError(f'岗位第{number}行发布日期格式不正确') from exc
    urls=set()
    for number,row in enumerate(applications,2):
        if not valid_url(row['job_url']) or row['job_url'] in urls:
            raise ValueError(f'投递第{number}行URL无效或重复')
        urls.add(row['job_url'])
        if row['status'] not in ('queued','draft','manual_required','submitted'):
            raise ValueError(f'投递第{number}行状态不在允许范围')
        if row['confirmed_success'] not in ('','false','true'):
            raise ValueError(f'投递第{number}行confirmed_success须为空/false/true')
        if row['status']=='submitted':
            if not confirm_submissions or row['confirmed_success']!='true' or not row['confirmation_evidence']:
                raise ValueError(f'投递第{number}行submitted须有本人成功确认、依据，并使用--confirm-submissions')
        elif any(row[key] for key in ('confirmation_number','submitted_at','confirmation_evidence')) or row['confirmed_success']=='true':
            raise ValueError(f'投递第{number}行尚未提交，不能填收件信息或成功确认')
        if row['submitted_at']:
            try:
                value=datetime.fromisoformat(row['submitted_at'].replace('Z','+00:00'))
                if value.tzinfo is None: raise ValueError('timezone required')
            except ValueError as exc: raise ValueError(f'投递第{number}行提交时间须为带时区的ISO时间') from exc


def import_rows(config: dict, jobs: list[dict], applications: list[dict], confirm_submissions: bool=False) -> dict:
    validate_rows(jobs,applications,confirm_submissions)
    conn=connect_db(config)
    counts={'jobs_created':0,'jobs_existing_preserved':0,'applications_created':0,'applications_existing_preserved':0,'submissions_recorded':0}
    try:
        conn.execute('BEGIN')
        for row in jobs:
            existing=conn.execute('SELECT id FROM jobs WHERE url=?',(row['url'],)).fetchone()
            if existing:
                counts['jobs_existing_preserved']+=1
                continue
            job=JobPosting(source_name='manual_csv',company=row['company'],title=row['title'],url=row['url'],location=row['location'],
                           platform=row['platform'] or 'manual',role_kind=row['role_kind'],description=row['description'],external_id=row['external_id'],published_at=row['published_at'])
            upsert_job(conn,job,config)
            conn.execute('UPDATE jobs SET recruitment_category=? WHERE url=?',(row['recruitment_category'] or None,row['url']))
            counts['jobs_created']+=1
        for number,row in enumerate(applications,2):
            job=conn.execute('SELECT id FROM jobs WHERE url=?',(row['job_url'],)).fetchone()
            if not job: raise ValueError(f'投递第{number}行岗位尚未入库，请同时提供岗位CSV')
            existing=conn.execute('SELECT id,status FROM applications WHERE job_id=?',(job[0],)).fetchall()
            if len(existing)>1: raise ValueError(f'投递第{number}行对应多条申请，须由Agent按application_id核对')
            if existing and (existing[0][1]=='submitted' or row['status']!='submitted'):
                counts['applications_existing_preserved']+=1
                continue
            now=utc_now()
            if existing: appid=existing[0][0]
            else:
                appid=conn.execute('INSERT INTO applications(job_id,status,notes,created_at,updated_at) VALUES (?,?,?,?,?)',
                    (job[0],'queued',row['notes'] or None,now,now)).lastrowid
                counts['applications_created']+=1
            if row['status']=='submitted':
                submitted_at=row['submitted_at'] or now
                conn.execute('UPDATE applications SET status=?,confirmation_number=?,submitted_at=?,last_error=NULL,updated_at=? WHERE id=?',
                    ('submitted',row['confirmation_number'] or None,submitted_at,now,appid))
                if conn.execute("SELECT name FROM sqlite_master WHERE type=? AND name=?",('table','application_campaign_jobs')).fetchone():
                    conn.execute('UPDATE application_campaign_jobs SET status=?,last_error=NULL WHERE application_id=?',('submitted',appid))
                counts['submissions_recorded']+=1
            else:
                conn.execute('UPDATE applications SET status=?,updated_at=? WHERE id=?',(row['status'],now,appid))
            add_event(conn,appid,'manual_csv_recorded',{'status':row['status'],'confirmation_evidence':row['confirmation_evidence'] or None,
                'time_basis':'candidate_supplied' if row['submitted_at'] else 'recording_time' if row['status']=='submitted' else None})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally: conn.close()
    return counts


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs',type=Path)
    parser.add_argument('--applications',type=Path)
    parser.add_argument('--config',type=Path,default=PROJECT_ROOT/'job_bot/config.china_hk_ic_foreign.json')
    parser.add_argument('--confirm-submissions',action='store_true',help='仅在取得实际回执或候选人明确确认后使用')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    try:
        if not args.jobs and not args.applications: raise ValueError('至少提供--jobs或--applications的私有CSV')
        config=load_config(args.config)
        jobs=read_rows(args.jobs,JOB_COLUMNS) if args.jobs else []
        applications=read_rows(args.applications,APP_COLUMNS) if args.applications else []
        validate_rows(jobs,applications,args.confirm_submissions)
        if not jobs and not applications: raise ValueError('CSV只有标题行，请先填写真实记录')
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        APPLICATION_OUTPUT.mkdir(parents=True,exist_ok=True,mode=0o700)
        plan=APPLICATION_OUTPUT/f'manual_database_{stamp}.json'
        payload={'executed':False,'jobs':jobs,'applications':applications,'input_sha256':{
            name:hashlib.sha256(file.read_bytes()).hexdigest() for name,file in (('jobs',args.jobs),('applications',args.applications)) if file},
            'job_scores':[score_job(JobPosting(source_name='manual_csv',company=row['company'],title=row['title'],url=row['url'],description=row['description']),config) for row in jobs],
            'preview_scope':'仅校验 CSV 并预览拟议评分；执行时会在事务中检查现有记录'}
        plan.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');plan.chmod(0o600)
        if args.execute:
            payload['counts']=import_rows(config,jobs,applications,args.confirm_submissions);payload['executed']=True
            plan.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(payload['counts']))
        print('私有预览/导入记录：',plan)
        return 0
    except (ValueError,OSError) as exc:
        print('无法导入：'+str(exc))
        return 2


if __name__=='__main__': raise SystemExit(main())
