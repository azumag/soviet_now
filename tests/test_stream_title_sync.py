import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from stream_title_sync import youtube_title,kick_title,normalize_title

class FakeYouTube:
    def __init__(self):
        self.rows=[{'id':'abcdefghijk','status':{'lifeCycleStatus':'live'},'contentDetails':{'boundStreamId':'ours'}}]
        self.snippet={'title':'old','categoryId':'20','description':'keep description','tags':['game'],'defaultLanguage':'ja','channelId':'owner','thumbnails':{'default':{'url':'public'}}}
        self.writes=[]
    def broadcasts(self):return self.rows
    def _api(self,path,*,params,method='GET',payload=None):
        assert path=='videos' and params['part']=='snippet'
        if method=='PUT':
            self.writes.append(copy.deepcopy(payload));self.snippet=payload['snippet']
        return {'items':[{'id':'abcdefghijk','snippet':self.snippet}]}

class SyncTests(unittest.TestCase):
    def test_youtube_preserves_mutable_fields_and_readback(self):
        api=FakeYouTube();self.assertEqual(youtube_title(api,'new','ours'),'updated')
        p=api.writes[0];self.assertEqual(p['snippet']['description'],'keep description')
        self.assertEqual(p['snippet']['tags'],['game']);self.assertEqual(p['snippet']['categoryId'],'20')
        self.assertNotIn('channelId',p['snippet']);self.assertNotIn('status',p)
        self.assertEqual(youtube_title(api,'new','ours'),'unchanged');self.assertEqual(len(api.writes),1)
    def test_wrong_stream_or_multiple_broadcasts_never_write(self):
        api=FakeYouTube();self.assertEqual(youtube_title(api,'new','other'),'no_unique_live_broadcast')
        api.rows*=2;self.assertEqual(youtube_title(api,'new','ours'),'no_unique_live_broadcast');self.assertEqual(api.writes,[])
    def test_missing_expected_stream_never_queries_or_writes(self):
        api=FakeYouTube();self.assertEqual(youtube_title(api,'new'),'stream_not_configured');self.assertFalse(api.writes)
    def test_ended_broadcast_never_updated(self):
        api=FakeYouTube();api.rows[0]['status']['lifeCycleStatus']='complete'
        self.assertEqual(youtube_title(api,'new','ours'),'no_unique_live_broadcast');self.assertEqual(api.writes,[])
    def test_missing_category_never_writes(self):
        api=FakeYouTube();del api.snippet['categoryId'];self.assertEqual(youtube_title(api,'new','ours'),'invalid_video_snippet');self.assertFalse(api.writes)
    def test_kick_checks_principal_live_and_only_changes_title(self):
        row={'broadcaster_user_id':123,'stream_title':'old','stream':{'is_live':True,'key':'must-not-log'}};writes=[]
        def request(method,payload=None):
            if method=='PATCH':writes.append(payload);row['stream_title']=payload['stream_title']
            return {'data':[row]}
        self.assertEqual(kick_title(request,'new','999'),'wrong_broadcaster');self.assertFalse(writes)
        self.assertEqual(kick_title(request,'new','123'),'updated');self.assertEqual(writes,[{'stream_title':'new'}])
        self.assertEqual(kick_title(request,'new','123'),'unchanged');row['stream']['is_live']=False
        self.assertEqual(kick_title(request,'next','123'),'not_live')
    def test_title_limit_and_markup_rejected(self):
        self.assertEqual(len(normalize_title('あ'*150)),100)
        for text in ('',' ','<unsafe>'):
            with self.assertRaises(ValueError):normalize_title(text)
