from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]


def run_isolated(source: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="jianzhou-channels-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp, "JIANZHOU_DEMO": "1", "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=90,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        return json.loads(result.stdout.strip())


class ChannelOperationsTests(unittest.TestCase):
    def test_campaign_moments_official_article_plan_and_assets_are_one_chain(self):
        result = run_isolated("""
            import json
            from app.api import set_current_user
            from app.api.channels import (
                ArticlePatch, CampaignIn, GenerateIn, ManualPublishIn, MetricsIn,
                MomentPatch, bind_moment_visual_cover, create_campaign, generate_moments, generate_official,
                list_campaigns, list_moments, official_detail, publish_moment_manual,
                publish_official_manual, review_official, save_channel_metrics,
                update_moment, update_official, channel_performance, generate_channel_retro,
            )
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.planning import board, week
            from app.api.visuals import CoverCreateIn, create_cover
            from app.asset_center import sync_asset_index
            from app.db import init_db, jloads, query, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            set_current_user('u-zhou')

            campaign = create_campaign(CampaignIn(
                title='个人知识库到底要不要放本地',
                core_topic='本地与在线应按协作边界分工',
                objective='验证读者最关心的判断条件',
                audience='正在搭建个人知识库的创作者',
            ))['data']
            public_first = create_project(ProjectIn(
                title='个人知识库到底要不要放本地', goal='验证读者最关心的判断条件',
                campaign_id=campaign['id'],
            ))['data']
            public_second = create_project(ProjectIn(
                title='个人知识库到底要不要放本地', goal='验证读者最关心的判断条件',
                campaign_id=campaign['id'],
            ))['data']
            initial_plan_task = query_one(
                "SELECT * FROM content_tasks WHERE project_id=? AND delivery_kind='public_content'",
                (public_first['id'],),
            )
            draft = generate_draft(
                public_first['id'], DraftGenerateIn(style_mode='structure', platform='抖音口播')
            )['data']
            visual = create_cover(CoverCreateIn(
                creation_project_id=public_first['id'], draft_version_id=draft['id'],
                generation_mode='local_template', quantity=1,
            ))['data']
            moments = generate_moments(GenerateIn(campaign_id=campaign['id']))['data']
            posts = list_moments()['data']
            visual_option_id = posts[0]['visual_options'][0]['id']
            bind_moment_visual_cover(posts[0]['id'], visual_option_id)
            posts = list_moments()['data']
            update_moment(posts[0]['id'], MomentPatch(scheduled_at='2026-08-11T09:30', status='SCHEDULED'))
            scheduled_task = query_one(
                "SELECT * FROM content_tasks WHERE deliverable_id=?",
                (moments['deliverable_id'],),
            )
            for post in posts:
                publish_moment_manual(post['id'], ManualPublishIn(note='人工发布验收'))
            moment_task = query_one(
                "SELECT * FROM content_tasks WHERE deliverable_id=?",
                (moments['deliverable_id'],),
            )

            generated = generate_official(GenerateIn(campaign_id=campaign['id']))['data']
            article = official_detail(generated['id'])['data']
            long_body = '# ' + article['title'] + '\\n\\n## 先说结论\\n\\n' + ('这是来源明确、边界清晰且可以执行的正文段落。' * 90)
            update_official(generated['id'], ArticlePatch(
                title=article['title'], digest='说明判断、方法和适用边界。',
                markdown=long_body, author='见舟AI', change_note='验收补充深度',
            ))
            review = review_official(generated['id'])['data']
            ready_task = query_one(
                "SELECT * FROM content_tasks WHERE deliverable_id=?",
                (generated['deliverable_id'],),
            )
            publish_official_manual(
                generated['id'],
                ManualPublishIn(url='https://mp.weixin.qq.com/s/test-channel-chain'),
            )
            published_task = query_one(
                "SELECT * FROM content_tasks WHERE deliverable_id=?",
                (generated['deliverable_id'],),
            )
            save_channel_metrics(
                generated['deliverable_id'], MetricsIn(
                    tn='T+1', metrics={'阅读': 128, '点赞': 9},
                    subject_type='official_article', subject_id=generated['id'],
                ),
            )
            first_retro = generate_channel_retro(generated['deliverable_id'])['data']
            second_retro = generate_channel_retro(generated['deliverable_id'])['data']
            performance = channel_performance(generated['deliverable_id'])['data']
            official_record = query_one(
                "SELECT * FROM channel_publish_records WHERE deliverable_id=? AND mode='manual'",
                (generated['deliverable_id'],),
            )
            sync_asset_index()
            plan_board = board()['data']
            plan_cards = [card for column in plan_board['columns'] for card in column['cards']]
            official_plan_card = next(card for card in plan_cards
                                      if card.get('deliverable_id') == generated['deliverable_id'])
            moment_plan_card = next(card for card in plan_cards
                                    if card.get('deliverable_id') == moments['deliverable_id'])
            calendar_events = [event for day in week()['data']['days'] for event in day['events']]
            official_calendar = next(event for event in calendar_events
                                     if event.get('deliverable_id') == generated['deliverable_id'])
            campaign_view = next(item for item in list_campaigns('all')['data']
                                 if item['id'] == campaign['id'])
            asset_types = {row['asset_type'] for row in query(
                "SELECT asset_type FROM asset_records WHERE user_id='u-zhou'"
            )}
            lineage = {row['relation_type'] for row in query(
                "SELECT r.relation_type FROM asset_relations r "
                "JOIN asset_records f ON f.id=r.from_asset_id "
                "JOIN asset_records t ON t.id=r.to_asset_id "
                "WHERE r.user_id='u-zhou' AND ("
                "(f.entity_type='content_campaign' AND f.entity_id=? AND t.entity_type='creation_project') OR "
                "(f.entity_type='creation_project' AND f.entity_id=? AND t.entity_type='visual_batch') OR "
                "(f.entity_type='channel_deliverable' AND f.entity_id=? AND t.entity_type='channel_retro_report'))",
                (campaign['id'], public_first['id'], generated['deliverable_id']),
            )}
            moment_visual_lineage = {row['relation_type'] for row in query(
                "SELECT r.relation_type FROM asset_relations r "
                "JOIN asset_records f ON f.id=r.from_asset_id "
                "JOIN asset_records t ON t.id=r.to_asset_id "
                "WHERE r.user_id='u-zhou' AND f.entity_type='cover_project' AND f.entity_id=? "
                "AND t.entity_type='moment_post' AND t.entity_id=?",
                (visual_option_id, posts[0]['id']),
            )}
            official_asset = query_one(
                "SELECT metadata_json FROM asset_records WHERE user_id='u-zhou' "
                "AND entity_type='official_article' AND entity_id=?",
                (generated['id'],),
            )
            moment_asset = query_one(
                "SELECT metadata_json FROM asset_records WHERE user_id='u-zhou' "
                "AND entity_type='moment_post' AND entity_id=?",
                (posts[0]['id'],),
            )
            print(json.dumps({
                'schema': query_one('PRAGMA user_version')['user_version'],
                'campaign_id': campaign['id'],
                'campaigns': query_one('SELECT COUNT(*) n FROM content_campaigns')['n'],
                'linked_project_count': query_one(
                    'SELECT COUNT(*) n FROM creation_projects WHERE campaign_id=?', (campaign['id'],)
                )['n'],
                'public_project_reused': public_second['reused'],
                'same_public_project': public_first['id'] == public_second['id'],
                'public_project_id': public_first['id'],
                'same_plan_task': public_first['plan_task_id'] == public_second['plan_task_id'],
                'initial_plan_status': initial_plan_task['status'],
                'visual_batch_id': visual['batch_id'],
                'campaign_progress': campaign_view['progress'],
                'lineage': sorted(lineage),
                'moment_visual_lineage': sorted(moment_visual_lineage),
                'moment_visual_format': posts[0]['visual_suggestion']['format'],
                'moment_bound_visuals': len(posts[0]['bound_visuals']),
                'scheduled_status': scheduled_task['status'],
                'moment_status': moment_task['status'],
                'article_review': review['status'],
                'article_ready_status': ready_task['status'],
                'article_published_status': published_task['status'],
                'metric_snapshots': query_one('SELECT COUNT(*) n FROM channel_metric_snapshots')['n'],
                'retro_count': query_one('SELECT COUNT(*) n FROM channel_retro_reports')['n'],
                'retro_reused': second_retro['reused'],
                'performance_snapshots': len(performance['snapshots']),
                'performance_retros': len(performance['retro_reports']),
                'publish_subject_type': official_record['subject_type'],
                'publish_subject_id': official_record['subject_id'],
                'official_plan_target': official_plan_card['channel_target_id'],
                'moment_plan_target': moment_plan_card['channel_target_id'],
                'moment_post_ids': [post['id'] for post in posts],
                'official_calendar_target': official_calendar['channel_target_id'],
                'official_asset_metadata': jloads(official_asset['metadata_json'], {}),
                'moment_asset_metadata': jloads(moment_asset['metadata_json'], {}),
                'asset_types': sorted(asset_types),
                'fk_errors': len(query('PRAGMA foreign_key_check')),
            }, ensure_ascii=False))
        """)
        self.assertGreaterEqual(result["schema"], 30)
        self.assertGreaterEqual(result["campaigns"], 1)
        self.assertEqual(result["linked_project_count"], 1)
        self.assertTrue(result["public_project_reused"])
        self.assertTrue(result["same_public_project"])
        self.assertTrue(result["same_plan_task"])
        self.assertEqual(result["initial_plan_status"], "CANDIDATE")
        self.assertTrue(result["visual_batch_id"].startswith("VB-"))
        self.assertEqual(result["campaign_progress"]["project_id"], result["public_project_id"])
        self.assertEqual(result["campaign_progress"]["plan_task_status"], "DRAFTING")
        self.assertEqual(result["campaign_progress"]["visual_count"], 1)
        self.assertEqual(result["campaign_progress"]["channel_count"], 2)
        self.assertEqual(result["campaign_progress"]["retro_count"], 1)
        self.assertTrue({"has_public_content", "has_visual_batch", "reviewed_as"} <= set(result["lineage"]))
        self.assertIn("used_as_moment_visual", result["moment_visual_lineage"])
        self.assertIn(result["moment_visual_format"], {"real_photo", "screenshot", "knowledge_card", "minimal_card", "process_collage", "no_image"})
        self.assertEqual(result["moment_bound_visuals"], 1)
        self.assertEqual(result["scheduled_status"], "READY_TO_PUBLISH")
        self.assertEqual(result["moment_status"], "PUBLISHED")
        self.assertEqual(result["article_review"], "PASS")
        self.assertEqual(result["article_ready_status"], "READY_TO_PUBLISH")
        self.assertEqual(result["article_published_status"], "PUBLISHED")
        self.assertEqual(result["metric_snapshots"], 1)
        self.assertEqual(result["retro_count"], 1)
        self.assertTrue(result["retro_reused"])
        self.assertEqual(result["performance_snapshots"], 1)
        self.assertEqual(result["performance_retros"], 1)
        self.assertEqual(result["publish_subject_type"], "official_article")
        self.assertTrue(result["publish_subject_id"].startswith("OA-"))
        self.assertEqual(result["official_plan_target"], result["publish_subject_id"])
        self.assertEqual(result["official_calendar_target"], result["publish_subject_id"])
        self.assertIn(result["moment_plan_target"], result["moment_post_ids"])
        self.assertEqual(result["official_asset_metadata"]["campaign_id"], result["campaign_id"])
        self.assertEqual(result["moment_asset_metadata"]["campaign_id"], result["campaign_id"])
        self.assertTrue({"campaign", "moment", "official_article", "channel_retro"} <= set(result["asset_types"]))
        self.assertEqual(result["fk_errors"], 0)

    def test_draft_box_is_not_publish_evidence_and_cannot_enter_metrics_or_retro(self):
        result = run_isolated("""
            import json
            from app.api import now, set_current_user
            from app.api.channels import (
                CampaignIn, GenerateIn, MetricsIn, create_campaign, generate_channel_retro,
                generate_official, save_channel_metrics,
            )
            from app.db import execute, init_db, jdumps, transaction
            from app.errors import AppError
            from app.ids import new_id
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            set_current_user('u-zhou')
            campaign = create_campaign(CampaignIn(title='草稿不等于发布'))['data']
            article = generate_official(GenerateIn(campaign_id=campaign['id']))['data']
            stamp = now()
            execute(
                "INSERT INTO channel_publish_records(id,user_id,deliverable_id,channel,mode,status,"
                "remote_id,url,request_json,response_json,error_json,published_at,created_at,updated_at,"
                "subject_type,subject_id) VALUES(?,?,?,?,?,'IN_DRAFT_BOX',?,NULL,?,?, '{}',NULL,?,?,?,?)",
                (new_id('CPR'), 'u-zhou', article['deliverable_id'], 'wechat_official', 'api_draft',
                 'draft-media', jdumps({'article_id': article['id']}), jdumps({'media_id': 'draft-media'}),
                 stamp, stamp, 'official_article', article['id']),
            )
            metric_rejected = retro_rejected = False
            try:
                save_channel_metrics(article['deliverable_id'], MetricsIn(
                    metrics={'阅读': 100}, subject_type='official_article', subject_id=article['id']))
            except AppError:
                metric_rejected = True
            execute(
                "INSERT INTO channel_metric_snapshots(id,user_id,deliverable_id,channel,subject_type,"
                "subject_id,tn,metrics_json,source,taken_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (new_id('CMS'), 'u-zhou', article['deliverable_id'], 'wechat_official',
                 'official_article', article['id'], 'T+1', jdumps({'阅读': 100}), 'manual', stamp),
            )
            try:
                generate_channel_retro(article['deliverable_id'])
            except AppError:
                retro_rejected = True
            print(json.dumps({'metric_rejected': metric_rejected, 'retro_rejected': retro_rejected}))
        """)
        self.assertTrue(result["metric_rejected"])
        self.assertTrue(result["retro_rejected"])

    def test_official_api_publish_has_separate_traceable_final_evidence(self):
        result = run_isolated("""
            import json
            from unittest.mock import patch
            from app.api import set_current_user
            from app.api.channels import (
                CampaignIn, GenerateIn, OfficialPublishIn, create_campaign, generate_official,
                publish_official, sync_official_status,
            )
            from app.db import execute, init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            class FakeWechat:
                def publish(self, media_id):
                    assert media_id == 'draft-media-id'
                    return {'publish_id': 'publish-task-1'}
                def publish_status(self, publish_id):
                    assert publish_id == 'publish-task-1'
                    return {'publish_status': 0, 'article_detail': {'item': [
                        {'article_url': 'https://mp.weixin.qq.com/s/final-evidence'}
                    ]}}

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            set_current_user('u-zhou')
            campaign = create_campaign(CampaignIn(title='公众号API证据链'))['data']
            article = generate_official(GenerateIn(campaign_id=campaign['id']))['data']
            execute("UPDATE official_articles SET status='IN_DRAFT_BOX',remote_media_id='draft-media-id' WHERE id=?",
                    (article['id'],))
            execute("UPDATE channel_deliverables SET status='IN_DRAFT_BOX' WHERE id=?",
                    (article['deliverable_id'],))
            with patch('app.api.channels.WeChatOfficialClient', FakeWechat):
                started = publish_official(article['id'], OfficialPublishIn(confirm=True))['data']
                synced = sync_official_status(article['id'])['data']
            record = query_one("SELECT * FROM channel_publish_records WHERE id=?", (started['record_id'],))
            saved = query_one("SELECT * FROM official_articles WHERE id=?", (article['id'],))
            print(json.dumps({
                'subject_type': record['subject_type'], 'subject_id': record['subject_id'],
                'status': record['status'], 'url': record['url'],
                'article_status': saved['status'], 'sync_status': synced['status'],
            }))
        """)
        self.assertEqual(result["subject_type"], "official_article")
        self.assertTrue(result["subject_id"].startswith("OA-"))
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(result["article_status"], "PUBLISHED")
        self.assertEqual(result["sync_status"], "PUBLISHED")
        self.assertEqual(result["url"], "https://mp.weixin.qq.com/s/final-evidence")

    def test_official_article_can_use_same_campaign_visual_cover_for_real_draft_upload(self):
        result = run_isolated("""
            import json
            from pathlib import Path
            from unittest.mock import patch
            from app.api import set_current_user
            from app.api.channels import (
                ArticlePatch, CampaignIn, DraftPushIn, GenerateIn,
                bind_official_visual_cover, create_campaign, generate_official,
                official_detail, push_official_draft, review_official,
                update_official,
            )
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.visuals import CoverCreateIn, create_cover
            from app.asset_center import sync_asset_index
            from app.db import init_db, jloads, query, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            calls = {'upload': 0, 'draft': 0}
            class FakeWechat:
                def __init__(self): pass
                def upload_permanent_image(self, path):
                    assert isinstance(path, Path) and path.exists()
                    calls['upload'] += 1
                    return {'media_id': 'cover-media-1', 'url': 'https://mmbiz.qpic.cn/cover'}
                def add_draft(self, article):
                    assert article['thumb_media_id'] == 'cover-media-1'
                    assert article['content']
                    calls['draft'] += 1
                    return {'media_id': 'draft-media-1'}

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            set_current_user('u-zhou')
            campaign = create_campaign(CampaignIn(title='Visual cover to Official Account'))['data']
            project = create_project(ProjectIn(
                title='Visual cover to Official Account', campaign_id=campaign['id']
            ))['data']
            draft = generate_draft(project['id'], DraftGenerateIn(
                style_mode='structure', platform='WeChat Official Account'
            ))['data']
            cover = create_cover(CoverCreateIn(
                creation_project_id=project['id'], draft_version_id=draft['id'],
                generation_mode='local_template', quantity=1, ratio='16:9',
            ))['data']
            article_ref = generate_official(GenerateIn(campaign_id=campaign['id']))['data']
            article = official_detail(article_ref['id'])['data']
            long_body = '# ' + article['title'] + '\\n\\n## Conclusion\\n\\n' + (
                'This paragraph has a clear source boundary, conclusion and action. ' * 130
            )
            update_official(article_ref['id'], ArticlePatch(
                title=article['title'], digest='A traceable long article.', markdown=long_body,
                author='Jianzhou AI', change_note='Complete the acceptance body',
            ))
            review = review_official(article_ref['id'])['data']
            bound = bind_official_visual_cover(article_ref['id'], cover['id'])['data']
            detail = official_detail(article_ref['id'])['data']
            with patch('app.api.channels.WeChatOfficialClient', FakeWechat):
                pushed = push_official_draft(article_ref['id'], DraftPushIn())['data']
            sync_asset_index()
            saved = query_one('SELECT * FROM official_articles WHERE id=?', (article_ref['id'],))
            record = query_one(
                "SELECT * FROM channel_publish_records WHERE subject_type='official_article' "
                "AND subject_id=? ORDER BY created_at DESC LIMIT 1", (article_ref['id'],)
            )
            relations = {row['relation_type'] for row in query(
                "SELECT r.relation_type FROM asset_relations r "
                "JOIN asset_records f ON f.id=r.from_asset_id "
                "JOIN asset_records t ON t.id=r.to_asset_id "
                "WHERE r.user_id='u-zhou' AND f.entity_type='cover_project' "
                "AND f.entity_id=? AND t.entity_type='official_article' AND t.entity_id=?",
                (cover['id'], article_ref['id']),
            )}
            print(json.dumps({
                'schema': query_one('PRAGMA user_version')['user_version'],
                'review': review['status'], 'bound_id': bound['cover_project_id'],
                'candidate_bound': any(item['bound'] for item in detail['visual_covers']),
                'cover_preview_url': detail['cover_preview_url'],
                'saved_cover': saved['cover_project_id'], 'saved_media': saved['cover_media_id'],
                'draft_media': saved['remote_media_id'], 'pushed_media': pushed['media_id'],
                'request': jloads(record['request_json'], {}),
                'upload_calls': calls['upload'], 'draft_calls': calls['draft'],
                'relations': sorted(relations),
            }))
        """)
        self.assertGreaterEqual(result["schema"], 30)
        self.assertEqual(result["review"], "PASS")
        self.assertEqual(result["bound_id"], result["saved_cover"])
        self.assertTrue(result["candidate_bound"])
        self.assertIn("/api/visuals/covers/", result["cover_preview_url"])
        self.assertEqual(result["saved_media"], "cover-media-1")
        self.assertEqual(result["draft_media"], "draft-media-1")
        self.assertEqual(result["pushed_media"], "draft-media-1")
        self.assertEqual(result["upload_calls"], 1)
        self.assertEqual(result["draft_calls"], 1)
        self.assertTrue(result["request"]["cover_uploaded_now"])
        self.assertEqual(result["request"]["thumb_media_id"], "cover-media-1")
        self.assertIn("used_as_official_cover", result["relations"])

    def test_wechat_secret_is_hidden_and_draft_boundary_is_explicit(self):
        result = run_isolated("""
            import json
            from app.api.channels import wechat_status
            from app.api.settings import SettingsIn, get_settings, put_settings
            from app.db import init_db
            from app.main import _ensure_settings
            from app.secret_store import get_secret

            init_db(); _ensure_settings()
            put_settings(SettingsIn(section='wechat_official', values={
                'enabled': True, 'app_id': 'wx-test-app-id',
                'app_secret': 'wechat-secret-value', 'account_name': '测试公众号',
            }))
            public = get_settings()['data']['wechat_official']
            status = wechat_status()['data']
            print(json.dumps({
                'secret_saved': get_secret('wechat_app_secret') == 'wechat-secret-value',
                'secret_leaked': 'wechat-secret-value' in json.dumps(public),
                'configured': status['configured'],
                'fields': [item['field'] for item in status['requirements']],
                'boundary': status['boundary'],
            }, ensure_ascii=False))
        """)
        self.assertTrue(result["secret_saved"])
        self.assertFalse(result["secret_leaked"])
        self.assertTrue(result["configured"])
        self.assertIn("AppID", result["fields"])
        self.assertIn("AppSecret", result["fields"])
        self.assertIn("草稿箱", result["boundary"])
        self.assertIn("朋友圈", result["boundary"])

    def test_wechat_official_contract_uses_exact_token_and_draft_boundaries(self):
        from app.errors import AppError
        from app import wechat_official as module

        settings = {
            "enabled": True, "account_name": "测试公众号", "account_type": "subscription",
            "verified": True, "app_id": "wx-app-id", "app_secret": "wx-secret",
            "default_author": "见舟AI", "default_thumb_media_id": "thumb-default",
            "draft_push_enabled": True, "publish_enabled": False,
            "auto_publish_enabled": False, "ip_whitelist_note": "",
        }

        class FakeResponse:
            def __init__(self, payload, status_code=200):
                self.payload = payload; self.status_code = status_code
            def json(self): return self.payload

        module._token_cache.update({"token": "", "expires_at": 0.0, "app_id": ""})
        with patch.object(module, "connector_settings", return_value=settings), \
             patch.object(module.httpx, "get", return_value=FakeResponse({"access_token": "token-1", "expires_in": 7200})) as get_mock, \
             patch.object(module.httpx, "post", return_value=FakeResponse({"media_id": "draft-media"})) as post_mock:
            client = module.WeChatOfficialClient()
            result = client.add_draft({
                "title": "真实标题", "digest": "真实摘要", "content": "<p>正文</p>",
                "content_source_url": "https://example.com/source",
            })
            self.assertEqual(result["media_id"], "draft-media")
            token_call = get_mock.call_args
            self.assertEqual(token_call.args[0], module.API_BASE + "/cgi-bin/token")
            self.assertEqual(token_call.kwargs["params"], {
                "grant_type": "client_credential", "appid": "wx-app-id", "secret": "wx-secret",
            })
            draft_call = post_mock.call_args
            self.assertEqual(draft_call.args[0], module.API_BASE + "/cgi-bin/draft/add")
            self.assertEqual(draft_call.kwargs["params"], {"access_token": "token-1"})
            article = draft_call.kwargs["json"]["articles"][0]
            self.assertEqual(article["thumb_media_id"], "thumb-default")
            self.assertEqual(article["content"], "<p>正文</p>")
            self.assertFalse(settings["publish_enabled"])

        with self.assertRaises(AppError) as raised:
            module.WeChatOfficialClient._translate({"errcode": 40164, "errmsg": "invalid ip"}, "wechat_token")
        self.assertIn("IP", raised.exception.message)
        self.assertEqual(raised.exception.details["errcode"], 40164)

    def test_wechat_probe_records_blocked_ip_without_claiming_publish_permission(self):
        result = run_isolated("""
            import json
            from unittest.mock import patch
            from app.api import set_current_user
            from app.api.channels import channels_overview, test_wechat, wechat_status
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.api.settings import SettingsIn, put_settings
            from app.seed import seed_if_empty
            from app import wechat_official as module

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            set_current_user('u-zhou')
            put_settings(SettingsIn(section='wechat_official', values={
                'enabled': True, 'app_id': 'wx-probe-app', 'app_secret': 'probe-secret',
                'account_name': '测试公众号', 'draft_push_enabled': True,
                'publish_enabled': True,
            }))

            class FakeResponse:
                status_code = 200
                def json(self):
                    return {'errcode': 40164, 'errmsg': 'invalid ip 104.28.152.117 ipv6 ::ffff:104.28.152.117'}

            module._token_cache.update({'token': '', 'expires_at': 0.0, 'app_id': ''})
            with patch.object(module.httpx, 'get', return_value=FakeResponse()):
                probe = test_wechat()['data']
            status = wechat_status()['data']
            overview = channels_overview()['data']
            row = query_one('SELECT * FROM wechat_permission_probes WHERE user_id=?', ('u-zhou',))
            print(json.dumps({
                'status': probe['status'], 'reachable': probe['reachable'],
                'token_state': probe['capability_states']['token']['status'],
                'publish_verified': probe['capability_states']['publish']['verified'],
                'publish_boolean': probe['capabilities']['publish'],
                'ips': probe['egress_ips'], 'saved': bool(row),
                'last_status': status['last_probe']['status'],
                'overview_last_status': overview['wechat']['last_probe']['status'],
                'overview_ip_note': overview['wechat']['ip_whitelist_note'],
                'secret_leaked': 'probe-secret' in json.dumps(dict(row)),
            }, ensure_ascii=False))
        """)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["reachable"])
        self.assertEqual(result["token_state"], "BLOCKED")
        self.assertFalse(result["publish_verified"])
        self.assertFalse(result["publish_boolean"])
        self.assertEqual(result["ips"], ["104.28.152.117"])
        self.assertTrue(result["saved"])
        self.assertEqual(result["last_status"], "BLOCKED")
        self.assertEqual(result["overview_last_status"], "BLOCKED")
        self.assertIn("104.28.152.117", result["overview_ip_note"])
        self.assertFalse(result["secret_leaked"])

    def test_wechat_read_only_probe_verifies_draft_but_never_claims_publish(self):
        from app import wechat_official as module

        settings = {
            "enabled": True, "account_name": "测试公众号", "account_type": "service",
            "verified": True, "app_id": "wx-probe-ok", "app_secret": "secret",
            "default_author": "见舟AI", "default_thumb_media_id": "",
            "draft_push_enabled": True, "publish_enabled": True,
            "auto_publish_enabled": False, "ip_whitelist_note": "",
        }

        class FakeResponse:
            status_code = 200
            def __init__(self, payload): self.payload = payload
            def json(self): return self.payload

        module._token_cache.update({"token": "", "expires_at": 0.0, "app_id": ""})
        with patch.object(module, "connector_settings", return_value=settings), \
             patch.object(module.httpx, "get", return_value=FakeResponse({"access_token": "token-ok", "expires_in": 7200})), \
             patch.object(module.httpx, "post", return_value=FakeResponse({"total_count": 1, "item_count": 1})):
            result = module.WeChatOfficialClient().probe()
        self.assertEqual(result["status"], "READY_FOR_DRAFT")
        self.assertTrue(result["capabilities"]["token"])
        self.assertTrue(result["capabilities"]["draft"])
        self.assertFalse(result["capabilities"]["publish"])
        self.assertEqual(result["capability_states"]["publish"]["status"], "NOT_VERIFIED")
        self.assertFalse(result["capability_states"]["publish"]["verified"])


if __name__ == "__main__":
    unittest.main()
