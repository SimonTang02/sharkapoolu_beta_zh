from __future__ import annotations

import urllib.error
import urllib.parse
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from job_bot.bot import (
    JobPosting,
    classify_role_kind,
    connect_db,
    expanded_workday_location,
    fetch_apple_jobs,
    fetch_ashby,
    fetch_attrax,
    fetch_eightfold,
    fetch_icims,
    fetch_hotjob,
    fetch_jibe,
    fetch_jobsyn,
    fetch_mediatek,
    fetch_oracle_candidate_experience,
    fetch_rss,
    fetch_smartrecruiters,
    fetch_source_with_retry,
    fetch_source,
    fetch_xiaomi,
    fetch_zhiye,
    fetch_zhiye_html,
    load_config,
    render_digest,
    rescore_jobs,
    scan,
    score_job,
    source_parallel_key,
    transient_source_error,
    upsert_job,
    url_with_query,
    validate_sync_snapshot,
)


class StructuredCareerSourceTests(unittest.TestCase):
    def test_lifecycle_guard_rejects_empty_snapshot_without_deactivating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "database": {"path": str(Path(directory) / "jobs.sqlite3")},
                "scan": {"lifecycle_guard": {"minimum_items": 1, "allow_empty": False}},
            }
            conn = connect_db(config)
            conn.execute(
                "INSERT INTO jobs (title,url,source_name,is_active) VALUES ('RTL','https://example.com','Fixture',1)"
            )
            conn.commit()
            with self.assertRaisesRegex(RuntimeError, "现有在招状态已保留"):
                validate_sync_snapshot(
                    conn,
                    {"name": "Fixture", "sync_active": True},
                    [],
                    config,
                )
            self.assertEqual(conn.execute("SELECT is_active FROM jobs").fetchone()[0], 1)
            conn.close()

    def test_rescore_does_not_rewrite_lifecycle_timestamp_or_role_kind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.sqlite3"
            config = {
                "database": {"path": str(database)},
                "sources": [{"name": "Fixture", "force_role_kind": "internship"}],
                "scoring": {"target_keywords": ["rtl"]},
            }
            conn = connect_db(config)
            conn.execute(
                """
                INSERT INTO jobs (
                  company,title,url,role_kind,description,source_name,
                  first_seen,last_seen,is_active,created_at,updated_at
                ) VALUES ('Example','RTL Engineer','https://example.com/rtl',
                  'full_time','RTL design','Fixture',
                  '2026-08-01T00:00:00+00:00','2026-08-01T00:00:00+00:00',1,
                  '2026-08-01T00:00:00+00:00','2026-08-01T00:00:00+00:00')
                """
            )
            conn.commit()
            conn.close()
            self.assertEqual(rescore_jobs(config), 1)
            conn = connect_db(config)
            row = conn.execute(
                "SELECT role_kind, updated_at, fit_score FROM jobs"
            ).fetchone()
            conn.close()
            self.assertEqual(row["role_kind"], "full_time")
            self.assertEqual(row["updated_at"], "2026-08-01T00:00:00+00:00")
            self.assertGreater(row["fit_score"], 0)

    @patch("job_bot.bot.http_get")
    def test_ashby_maps_public_job_board(self, http_get) -> None:
        http_get.return_value = json.dumps({
            "jobs": [{
                "id": "etched-rtl-2027",
                "title": "RTL Intern - Summer 2027",
                "location": "San Jose, California, United States",
                "jobUrl": "https://jobs.ashbyhq.com/etched/rtl-2027",
                "descriptionHtml": "<p>Design microarchitecture and Verilog RTL.</p>",
                "publishedAt": "2026-08-01T00:00:00Z",
            }],
        }).encode()
        jobs = fetch_ashby({
            "name": "Etched fixture",
            "type": "ashby",
            "board_name": "etched",
            "company": "Etched",
            "role_kinds": ["internship"],
        })
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].role_kind, "internship")
        self.assertEqual(jobs[0].external_id, "etched-rtl-2027")
        self.assertIn("microarchitecture", jobs[0].description)

    @patch("job_bot.bot.http_get")
    def test_xiaomi_maps_publication_date_and_role_type(self, http_get) -> None:
        http_get.return_value = json.dumps(
            {
                "code": 0,
                "data": {
                    "total": 1,
                    "list": [
                        {
                            "id": 380527,
                            "title": "芯片设计工程师",
                            "cityZhNames": ["深圳"],
                            "type": 2,
                            "url": "https://xiaomi.jobs.example/position/380527/detail",
                            "publishTime": "2026-08-26",
                            "description": "负责数字电路 RTL 设计",
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ).encode()
        jobs = fetch_xiaomi(
            {
                "name": "Xiaomi fixture",
                "company": "Xiaomi",
                "search_texts": ["芯片"],
                "page_size": 100,
                "max_pages": 1,
                "include_patterns": ["深圳", "芯片|RTL"],
            }
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].role_kind, "full_time")
        self.assertEqual(jobs[0].published_at, "2026-08-26T00:00:00+08:00")

    def test_foundation_scoring_prefers_architecture_and_digital_design(self) -> None:
        config = {
            "scoring": {
                "foundation_secondary_bonus": 4,
                "foundation_groups": [
                    {
                        "name": "architecture",
                        "base_score": 74,
                        "min_body_hits": 2,
                        "keywords": ["CPU architecture", "microarchitecture", "processor"],
                    },
                    {
                        "name": "digital RTL",
                        "base_score": 72,
                        "min_body_hits": 2,
                        "keywords": ["RTL", "SystemVerilog", "Verilog"],
                    },
                ],
                "modifiers": [
                    {
                        "name": "resume evidence",
                        "points": 8,
                        "keywords": ["SystemVerilog", "RISC-V"],
                    }
                ],
            }
        }
        job = JobPosting(
            source_name="fixture",
            company="Example",
            title="CPU Architecture Intern",
            url="https://example.com/architecture",
            description="Develop RISC-V microarchitecture and SystemVerilog RTL.",
        )
        score, reason = score_job(job, config)
        self.assertGreaterEqual(score, 82)
        self.assertIn("基础方向：architecture", reason)
        self.assertIn("次要方向：digital RTL", reason)

    def test_foundation_gate_blocks_incidental_validation_word(self) -> None:
        config = {
            "scoring": {
                "foundation_groups": [
                    {
                        "name": "verification",
                        "base_score": 60,
                        "min_body_hits": 2,
                        "keywords": ["verification", "validation", "testbench"],
                    }
                ]
            }
        }
        job = JobPosting(
            source_name="fixture",
            company="Example",
            title="Trade Sanctions Systems Analyst",
            url="https://example.com/sanctions",
            description="Own validation for a semiconductor compliance system.",
        )
        score, reason = score_job(job, config)
        self.assertEqual(score, 0)
        self.assertIn("基础方向：无", reason)

    def test_specific_physical_design_title_beats_generic_asic_word(self) -> None:
        config = {
            "scoring": {
                "foundation_secondary_bonus": 4,
                "foundation_groups": [
                    {
                        "name": "digital RTL",
                        "base_score": 72,
                        "keywords": ["ASIC", "RTL"],
                    },
                    {
                        "name": "physical design",
                        "base_score": 44,
                        "keywords": ["physical design", "layout"],
                    },
                ],
            }
        }
        job = JobPosting(
            source_name="fixture",
            company="Example",
            title="ASIC Physical Design Intern",
            url="https://example.com/pd",
        )
        score, reason = score_job(job, config)
        self.assertEqual(score, 48)
        self.assertIn("基础方向：physical design", reason)
        self.assertIn("次要方向：digital RTL", reason)

    def test_campaign_profile_demotes_generic_soc_dft_and_software_titles(self) -> None:
        config = load_config(Path(__file__).with_name("config.china_hk_ic_foreign.json"))
        dft_score, dft_reason = score_job(
            JobPosting(
                source_name="fixture",
                company="Example",
                title="SoC DFT Engineer",
                url="https://example.com/dft",
                description="ASIC scan insertion and DFT verification.",
            ),
            config,
        )
        self.assertIn("基础方向：physical design and DFT", dft_reason)
        self.assertLess(dft_score, 75)

        software_score, _ = score_job(
            JobPosting(
                source_name="fixture",
                company="Example",
                title="Embedded SW Engineer (SoC)",
                url="https://example.com/software",
                description="Embedded software for a SoC platform.",
            ),
            config,
        )
        self.assertLess(software_score, 60)

        sales_score, _ = score_job(
            JobPosting(
                source_name="fixture",
                company="Example",
                title="Sr Account Executive I (EDA Software)",
                url="https://example.com/sales",
                description="Own customer accounts for EDA products.",
            ),
            config,
        )
        self.assertLess(sales_score, 45)

    @patch("job_bot.bot.http_get")
    def test_zhiye_html_maps_server_rendered_cards(self, http_get) -> None:
        http_get.return_value = b"""
          <div class="item"><a href="/xiangqing?jobId=42">
            <h3>AI\xe8\x8a\xaf\xe7\x89\x87\xe6\x9e\xb6\xe6\x9e\x84\xe5\xb7\xa5\xe7\xa8\x8b\xe5\xb8\x88</h3>
            <span class="info"><span class="sx"> | \xe5\x8c\x97\xe4\xba\xac\xe5\xb8\x82,\xe4\xb8\x8a\xe6\xb5\xb7\xe5\xb8\x82</span></span>
          </a></div>
        """
        jobs = fetch_zhiye_html({
            "name": "Zhiye HTML fixture",
            "type": "zhiye_html",
            "host": "https://jobs.example.com",
            "company": "Example",
            "sections": ["Intern"],
            "search_texts": ["\xe8\x8a\xaf\xe7\x89\x87"],
        })
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "42")
        self.assertEqual(jobs[0].location, "北京市,上海市")
        self.assertEqual(jobs[0].role_kind, "internship")

    @patch("job_bot.bot.http_get")
    def test_smartrecruiters_maps_public_postings(self, http_get) -> None:
        http_get.return_value = json.dumps({
            "totalFound": 1,
            "content": [{
                "id": "7440001",
                "name": "SSD Firmware Engineer",
                "location": {"fullLocation": "San Jose, CA, United States"},
                "department": {"label": "Engineering"},
                "typeOfEmployment": {"label": "Full-time"},
            }],
        }).encode()
        jobs = fetch_smartrecruiters({
            "name": "SmartRecruiters fixture",
            "type": "smartrecruiters",
            "company_code": "Example",
            "company": "Example",
        })
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "7440001")
        self.assertEqual(jobs[0].location, "San Jose, CA, United States")
        self.assertEqual(
            jobs[0].url,
            "https://jobs.smartrecruiters.com/Example/7440001",
        )

    @patch("job_bot.bot.urllib.request.urlopen")
    def test_hotjob_maps_position_pages(self, urlopen) -> None:
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "data": {"pageForm": {"totalPage": 1, "pageData": [{
                "postId": "abc",
                "postName": "数字芯片验证工程师",
                "workPlaceStr": "上海市",
                "postTypeName": "芯片序列",
                "company": "Example",
            }]}},
        }).encode()
        urlopen.return_value = response
        jobs = fetch_hotjob({
            "name": "Hotjob fixture",
            "type": "hotjob",
            "suite_key": "SUfixture",
            "public_url": "https://jobs.example.com/social.html",
            "company": "Example",
            "recruit_types": [2],
        })
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "上海市")
        self.assertIn("postId=abc", jobs[0].url)

    @patch("job_bot.bot.post_json")
    def test_zhiye_maps_job_ad_list(self, post_json) -> None:
        post_json.return_value = {
            "Count": 1,
            "Data": [{
                "JobAdId": 42,
                "JobAdName": "芯片架构设计工程师",
                "LocNames": ["北京市"],
                "Duty": "CPU SoC RTL design",
                "Kind": "全职",
            }],
        }
        jobs = fetch_zhiye({
            "name": "Zhiye fixture",
            "type": "zhiye",
            "api_url": "https://jobs.example.com/api/jobs",
            "public_url": "https://jobs.example.com/campus/jobs",
            "company": "Example",
            "categories": ["2"],
        })
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "42")
        self.assertEqual(jobs[0].role_kind, "full_time")

    @patch("job_bot.bot.http_get")
    def test_jobsyn_maps_public_search_results(self, http_get) -> None:
        http_get.return_value = json.dumps({
            "jobs": [{
                "guid": "ABC123",
                "reqid": "42",
                "title_exact": "Veloce Verification Engineer",
                "title_slug": "veloce-verification-engineer",
                "location_exact": "Shanghai, CHN",
                "description": "SoC emulation and UVM",
            }],
            "pagination": {"has_more_pages": False},
        }).encode()
        jobs = fetch_jobsyn({
            "name": "Jobsyn fixture",
            "type": "jobsyn",
            "api_url": "https://search.example.com/api/search",
            "public_host": "https://jobs.example.com",
            "company": "Example",
        })
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "Shanghai, CHN")
        self.assertEqual(
            jobs[0].url,
            "https://jobs.example.com/shanghai-chn/veloce-verification-engineer/ABC123/job/",
        )

    @patch("job_bot.bot.http_get")
    def test_oracle_candidate_experience_maps_requisitions(self, http_get) -> None:
        payload = {
            "items": [{
                "TotalJobsCount": 1,
                "requisitionList": [{
                    "Id": "240001HW",
                    "Title": "Analog IC Design Engineering Intern",
                    "PrimaryLocation": "Shanghai, Shanghai, China",
                    "ShortDescriptionStr": "Design mixed-signal circuits",
                }],
            }],
        }
        http_get.return_value = json.dumps(payload).encode()
        jobs = fetch_oracle_candidate_experience({
            "name": "Oracle fixture",
            "type": "oracle_ce",
            "api_host": "https://tenant.fa.oraclecloud.com",
            "public_host": "https://careers.example.com",
            "site": "CX",
            "company": "Example",
            "search_texts": ["analog"],
            "locations": ["China"],
        })
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "240001HW")
        self.assertEqual(jobs[0].role_kind, "internship")
        self.assertEqual(
            jobs[0].url,
            "https://careers.example.com/en/sites/CX/job/240001HW/",
        )

    @patch("job_bot.bot.http_get")
    def test_apple_jobs_reads_hydration_state(self, http_get) -> None:
        state = {
            "loaderData": {"search": {"searchResults": [{
                "reqId": "200000001-3715",
                "postingTitle": "SoC Design Engineer",
                "transformedPostingTitle": "soc-design-engineer",
                "jobSummary": "RTL architecture and silicon verification",
                "locations": [{"name": "Shanghai", "countryName": "China"}],
                "localeInfo": {"defaultLocaleCode": "en-us"},
            }]}},
            "actionData": None,
            "errors": None,
        }
        encoded = json.dumps(json.dumps(state))
        http_get.return_value = (
            f'<script>window.__staticRouterHydrationData = JSON.parse({encoded});</script>'
        ).encode()
        jobs = fetch_apple_jobs({
            "name": "Apple fixture",
            "type": "apple_jobs",
            "url": "https://jobs.apple.com/en-us/search",
            "company": "Apple",
        })
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "Shanghai, China")
        self.assertEqual(
            jobs[0].url,
            "https://jobs.apple.com/en-us/details/200000001-3715/soc-design-engineer",
        )

    @patch("job_bot.bot.http_get")
    def test_eightfold_paginates_and_deduplicates(self, http_get) -> None:
        def response(url, *_args, **_kwargs):
            start = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["start"][0])
            item_id = "100" if start == 0 else "101"
            payload = {
                "status": 200,
                "data": {
                    "count": 2,
                    "positions": [{
                        "id": item_id,
                        "name": "ASIC Verification Intern" if start == 0 else "CPU Architect",
                        "locations": ["Shanghai, China"],
                        "department": "Hardware Engineering",
                        "positionUrl": f"/careers/job/{item_id}",
                    }],
                },
            }
            return json.dumps(payload).encode()

        http_get.side_effect = response
        jobs = fetch_eightfold({
            "name": "Fixture Eightfold",
            "type": "eightfold",
            "host": "https://jobs.example.com",
            "domain": "example.com",
            "company": "Example",
            "page_size": 1,
        })
        self.assertEqual([job.external_id for job in jobs], ["100", "101"])
        self.assertEqual(jobs[0].location, "Shanghai, China")
        self.assertEqual(jobs[0].role_kind, "internship")

    @patch("job_bot.bot.http_get")
    def test_jibe_unwraps_jobs_and_paginates(self, http_get) -> None:
        def response(url, *_args, **_kwargs):
            page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["page"][0])
            if page > 2:
                return json.dumps({"jobs": [], "totalCount": 2}).encode()
            item = {
                "data": {
                    "title": "Digital IC Design Intern" if page == 1 else "CPU Architect",
                    "req_id": str(40 + page),
                    "slug": str(40 + page),
                    "full_location": "Shanghai, China",
                    "description": "RTL and silicon architecture",
                }
            }
            return json.dumps({"jobs": [item], "totalCount": 2}).encode()

        http_get.side_effect = response
        jobs = fetch_jibe(
            {
                "name": "Fixture Jibe",
                "type": "jibe",
                "api_url": "https://careers.example.com/api/jobs",
                "company": "Example",
                "page_size": 1,
                "job_url_template": "https://careers.example.com/jobs/{slug}",
            }
        )
        self.assertEqual([job.external_id for job in jobs], ["41", "42"])
        self.assertEqual(jobs[0].role_kind, "internship")
        self.assertEqual(jobs[1].url, "https://careers.example.com/jobs/42")

    def test_url_with_query_replaces_values(self) -> None:
        result = url_with_query("https://example.com/jobs?ss=1&pr=9", pr=0, in_iframe=1)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(result).query)
        self.assertEqual(query, {"ss": ["1"], "pr": ["0"], "in_iframe": ["1"]})

    @patch("job_bot.bot.http_get")
    def test_icims_cards_include_location_and_summary(self, http_get) -> None:
        http_get.return_value = b"""
          <a>Page 1 of 1</a>
          <li class="iCIMS_JobCardItem"><div class="row">
            <div class="col-xs-6 header left"><span>Job Locations US-CA-Irvine</span></div>
            <div class="col-xs-12 title"><a href="/jobs/42/analog-designer/job?in_iframe=1"><h3>Analog Designer</h3></a></div>
            <div class="col-xs-12 description">Design mixed-signal circuits.</div>
          </div></li>
        """
        jobs = fetch_icims(
            {
                "name": "Fixture iCIMS",
                "type": "icims",
                "url": "https://careers.example.com/jobs/search?ss=1",
                "company": "Example",
            }
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Analog Designer")
        self.assertEqual(jobs[0].location, "US-CA-Irvine")
        self.assertEqual(jobs[0].external_id, "42")
        self.assertEqual(jobs[0].role_kind, "full_time")

    @patch("job_bot.bot.http_get")
    def test_attrax_uses_official_filters_and_paginates(self, http_get) -> None:
        def response(url, *_args, **_kwargs):
            page = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["page"][0]
            job_id = "101" if page == "1" else "102"
            title = "RTL Engineer" if page == "1" else "CPU Architect"
            return f"""
              <span class="attrax-pagination__total-results">2 result(s)</span>
              <div class="attrax-vacancy-tile test" data-jobid="{job_id}">
                <a class="attrax-vacancy-tile__title" href="/job/{job_id}">{title}</a>
                <div class="attrax-vacancy-tile__location-freetext">
                  <p class="attrax-vacancy-tile__item-value">Shanghai, China</p>
                </div>
              </div>
            """.encode()

        http_get.side_effect = response
        jobs = fetch_attrax(
            {
                "name": "Fixture Attrax",
                "type": "attrax",
                "url": "https://jobs.example.com/jobs",
                "company": "Example",
                "option_ids": [620],
                "page_size": 1,
                "max_pages": 2,
            }
        )
        self.assertEqual([job.external_id for job in jobs], ["101", "102"])
        first_url = http_get.call_args_list[0].args[0]
        self.assertIn("options=620", first_url)

    @patch("job_bot.bot.fetch_html")
    def test_jobsdb_cloudflare_error_is_actionable(self, fetch_html) -> None:
        fetch_html.side_effect = urllib.error.HTTPError(
            "https://hk.jobsdb.com/jobs", 403, "Forbidden", {}, None
        )
        with self.assertRaisesRegex(RuntimeError, "Cloudflare 403"):
            fetch_source(
                {
                    "name": "JobsDB fixture",
                    "type": "jobsdb_hk",
                    "url": "https://hk.jobsdb.com/jobs",
                    "company": "JobsDB HK",
                }
            )

    @patch("job_bot.bot.http_get")
    def test_rss_can_extract_location_from_title(self, http_get) -> None:
        http_get.return_value = b"""
          <rss><channel><item>
            <title>Analog Design Intern (Austin, TX)</title>
            <link>https://careers.example.com/job/42</link>
            <description>Design analog and mixed-signal circuits.</description>
          </item></channel></rss>
        """
        jobs = fetch_rss(
            {
                "name": "Fixture RSS",
                "type": "rss",
                "url": "https://careers.example.com/sitemap.xml",
                "company": "Example",
                "location_from_title": True,
            }
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Analog Design Intern")
        self.assertEqual(jobs[0].location, "Austin, TX")
        self.assertEqual(jobs[0].role_kind, "internship")

    def test_workday_expands_primary_and_additional_locations(self) -> None:
        self.assertEqual(
            expanded_workday_location(
                {
                    "location": "China, Shanghai",
                    "additionalLocations": ["China, Beijing", "China, Shenzhen"],
                },
                "3 Locations",
            ),
            "China, Shanghai; China, Beijing; China, Shenzhen",
        )

    def test_role_classification_prefers_new_grad_title_over_body_intern_word(self) -> None:
        job = JobPosting(
            source_name="fixture",
            company="Example",
            title="2027 New College Graduate: GPU Architecture Engineer",
            url="https://example.com/job",
            description="Applicants may have previous internship experience.",
        )
        self.assertEqual(classify_role_kind(job, {}), "full_time")

    @patch("job_bot.bot.http_get")
    def test_mediatek_uses_structured_location_and_role(self, http_get) -> None:
        http_get.return_value = json.dumps(
            [
                {
                    "result": {
                        "data": {
                            "json": {
                                "jobs": [
                                    {
                                        "id": "MTK42",
                                        "title": "[2027 Internship] Digital IC Design",
                                        "description": "RTL and verification",
                                        "properties": {
                                            "location": {"code": "深圳"},
                                            "category": {"label": "Chip Design"},
                                            "workExperience": {"code": "无工作经验"},
                                        },
                                    }
                                ],
                                "pagination": {"total_pages": 1},
                            }
                        }
                    }
                }
            ]
        ).encode()
        jobs = fetch_mediatek(
            {
                "name": "MediaTek fixture",
                "type": "mediatek",
                "company": "MediaTek",
                "max_pages": 1,
            }
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "深圳")
        self.assertEqual(jobs[0].role_kind, "internship")

    def test_all_active_digest_groups_internships_before_full_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config = {
                "database": {"path": str(Path(temporary_dir) / "jobs.sqlite3")},
                "digest": {"min_score": 0, "deduplicate_similar": False},
                "scoring": {"target_keywords": ["rtl"]},
            }
            conn = connect_db(config)
            upsert_job(
                conn,
                JobPosting(
                    source_name="fixture",
                    company="A",
                    title="RTL Intern",
                    url="https://example.com/intern",
                    role_kind="internship",
                ),
                config,
            )
            upsert_job(
                conn,
                JobPosting(
                    source_name="fixture",
                    company="B",
                    title="RTL Engineer",
                    url="https://example.com/full-time",
                    role_kind="full_time",
                ),
                config,
            )
            conn.commit()
            conn.close()
            subject, body = render_digest(config, 24, all_active=True, edition=1)
            self.assertIn("每日职位摘要 #1：2 个在招职位", subject)
            self.assertLess(body.index("RTL Intern"), body.index("RTL Engineer"))

    @patch("job_bot.bot.fetch_source")
    def test_explicit_empty_source_selection_scans_nothing(self, fetch_source) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config = {
                "database": {"path": str(Path(temporary_dir) / "jobs.sqlite3")},
                "sources": [{"name": "should-not-run", "type": "rss"}],
            }
            self.assertEqual(scan(config, selected_sources=[]), {"seen": 0, "new": 0})
            fetch_source.assert_not_called()

    def test_parallel_key_serializes_same_company_sources(self) -> None:
        china = {"name": "Qualcomm China", "company": "Qualcomm"}
        united_states = {"name": "Qualcomm US", "company": "qualcomm"}
        self.assertEqual(source_parallel_key(china), source_parallel_key(united_states))

    @patch("job_bot.bot.time.sleep")
    @patch("job_bot.bot.fetch_source")
    def test_transient_source_failure_is_retried(self, fetch_source, sleep) -> None:
        temporary_error = urllib.error.HTTPError(
            "https://example.com", 502, "Bad Gateway", {}, None
        )
        fetch_source.side_effect = [temporary_error, []]
        config = {"scan": {"retry_attempts": 2, "retry_backoff_seconds": 1}}
        self.assertEqual(
            fetch_source_with_retry({"name": "fixture"}, config), []
        )
        self.assertEqual(fetch_source.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_authentication_failure_is_not_transient(self) -> None:
        forbidden = urllib.error.HTTPError(
            "https://example.com", 403, "Forbidden", {}, None
        )
        self.assertFalse(transient_source_error(forbidden))


if __name__ == "__main__":
    unittest.main()
