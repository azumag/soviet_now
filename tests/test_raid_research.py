import unittest
from lib.raid_research import raiders, research

class RaidResearchTest(unittest.TestCase):
    def test_only_actual_notifications_and_deduplicate(self):
        items=[dict(user='StreamElements',comment='!raided Conro91',category='raid'),dict(user='Nightbot',comment='Conro91さんレイドありがとうございます https://www.twitch.tv/conro91',category='raid'),dict(user='viewer',comment='raid https://twitch.tv/other',category='raid')]
        self.assertEqual(raiders(items),['conro91'])

    def test_channel_details_not_just_game(self):
        def get(endpoint,params):
            return {'users':[dict(id='12',login='conro91',display_name='Conro',description='週末にパズルを遊びます')], 'channels':[dict(title='今日は挑戦回',game_name='Puzzle')], 'videos':[dict(title='視聴者参加回',published_at='2026-09-01')]}[endpoint]
        result=research('conro91',get)
        self.assertEqual(result['description'],'週末にパズルを遊びます')
        self.assertEqual(result['recent_videos'][0]['title'],'視聴者参加回')

    def test_failure_does_not_fabricate_details(self):
        result=research('conro91',lambda *args: [])
        self.assertEqual(result['status'],'unavailable')
        self.assertNotIn('description',result)

    def test_mixed_batch_ignores_regular_chat(self):
        self.assertEqual(raiders([dict(user='Nightbot',comment='https://twitch.tv/example',category='chitchat')]),[])

    def test_no_url_japanese_notification(self):
        self.assertEqual(raiders([dict(user='Nightbot',comment='ngi_eryさんレイドありがとうございます!',category='raid')]),['ngi_ery'])

    def test_api_failure_is_empty_and_does_not_print_secret(self):
        from unittest.mock import patch
        from lib.raid_research import api_get
        with patch.dict('os.environ',{'TWITCH_BOT_TOKEN':'secret','TWITCH_CLIENT_ID':'client'}), patch('urllib.request.urlopen',side_effect=OSError('failed')):
            self.assertEqual(api_get('users',{'login':'example'}),[])

    def test_rotated_token_in_env_file_wins_over_inherited_environment(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import lib.raid_research as rr
        seen = []
        def fake_urlopen(req, timeout):
            seen.append(req.get_header('Authorization'))
            raise OSError('stop')
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env_file.write_text('export TWITCH_BOT_TOKEN="fresh"\nTWITCH_CLIENT_ID=client\n', encoding='utf-8')
            with patch.object(rr, 'ENV_FILE', env_file), \
                    patch.dict('os.environ', {'TWITCH_BOT_TOKEN': 'stale', 'TWITCH_CLIENT_ID': 'client'}), \
                    patch('urllib.request.urlopen', side_effect=fake_urlopen):
                self.assertEqual(rr.api_get('users', {'login': 'example'}), [])
        self.assertEqual(seen, ['Bearer fresh'])

    def test_empty_description_keeps_recent_titles(self):
        def get(endpoint,params):
            return {'users':[dict(id='12',login='example')], 'channels':[], 'videos':[dict(title='配信者の企画')]}[endpoint]
        result=research('example',get)
        self.assertEqual(result['description'],'')
        self.assertEqual(result['recent_videos'][0]['title'],'配信者の企画')
