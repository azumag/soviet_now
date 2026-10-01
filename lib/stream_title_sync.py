"""Update only existing live titles; never create broadcasts or restart video."""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import urllib.request

from youtube_broadcast_guard import YouTubeAPI


def normalize_title(title: str, limit: int = 100) -> str:
    text = ' '.join(title.split())
    if not text or '<' in text or '>' in text:
        raise ValueError('invalid_title')
    return text if len(text) <= limit else text[:limit-1].rstrip() + '…'


def youtube_title(api, title: str, stream_id: str = '') -> str:
    """Resolve a unique currently live owner broadcast, preserve its snippet."""
    if not stream_id:
        return 'stream_not_configured'
    rows = [r for r in api.broadcasts() if r.get('status', {}).get('lifeCycleStatus') == 'live']
    if stream_id:
        rows = [r for r in rows if r.get('contentDetails', {}).get('boundStreamId') == stream_id]
    if len(rows) != 1:
        return 'no_unique_live_broadcast'
    video_id = rows[0].get('id')
    if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):
        return 'invalid_video_id'
    data = api._api('videos',params={'part':'snippet','id':video_id})
    items = data.get('items')
    if not isinstance(items,list) or len(items) != 1 or items[0].get('id') != video_id:
        return 'video_not_found'
    snippet = items[0].get('snippet')
    if not isinstance(snippet,dict) or not isinstance(snippet.get('categoryId'),str):
        return 'invalid_video_snippet'
    wanted = normalize_title(title)
    if snippet.get('title') == wanted:
        return 'unchanged'
    # videos.update replaces mutable fields within snippet: preserve all of
    # them, but do not echo output-only channel/thumbnails/localized fields.
    payload = {k:copy.deepcopy(snippet[k]) for k in
               ('title','description','tags','categoryId','defaultLanguage','defaultAudioLanguage') if k in snippet}
    payload['title'] = wanted
    api._api('videos',params={'part':'snippet'},method='PUT',payload={'id':video_id,'snippet':payload})
    verified = api._api('videos',params={'part':'snippet','id':video_id}).get('items',[])
    return 'updated' if len(verified)==1 and verified[0].get('id')==video_id and verified[0].get('snippet',{}).get('title')==wanted else 'unconfirmed'



def kick_title(request, title: str, broadcaster_id: str) -> str:
    if not re.fullmatch(r'[1-9][0-9]{0,18}',broadcaster_id):
        return 'invalid_broadcaster'
    # No query returns the authenticated principal. A public lookup of the
    # configured ID would not establish that PATCH targets that owner.
    rows=request('GET').get('data',[])
    if len(rows)!=1 or type(rows[0].get('broadcaster_user_id')) is not int or str(rows[0]['broadcaster_user_id'])!=broadcaster_id:
        return 'wrong_broadcaster'
    if rows[0].get('stream',{}).get('is_live') is not True:
        return 'not_live'
    wanted=normalize_title(title)
    if rows[0].get('stream_title')==wanted:
        return 'unchanged'
    request('PATCH',{'stream_title':wanted})
    rows=request('GET').get('data',[])
    return 'updated' if len(rows)==1 and str(rows[0].get('broadcaster_user_id'))==broadcaster_id and rows[0].get('stream_title')==wanted else 'unconfirmed'


def kick_request(token: str):
    def request(method, payload=None):
        req=urllib.request.Request('https://api.kick.com/public/v1/channels',
            method=method, data=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=10) as response:
            raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:
            raise ValueError('oversized_response')
        if not raw:
            return {}
        value=json.loads(raw)
        if not isinstance(value,dict):
            raise ValueError('invalid_response')
        return value
    return request

def main() -> int:
    title = sys.stdin.read(4097)
    if len(title)>4096:
        return 2
    try:
        title=normalize_title(title)
    except ValueError:
        return 2
    results={}
    required=('YOUTUBE_OAUTH_CLIENT_ID','YOUTUBE_OAUTH_CLIENT_SECRET','YOUTUBE_OAUTH_REFRESH_TOKEN')
    if all(os.environ.get(k) for k in required):
        try:
            api=YouTubeAPI(timeout=10);api.refresh()
            results['youtube']=youtube_title(api,title,os.environ.get('YOUTUBE_BROADCAST_STREAM_ID',''))
        except Exception:
            # Never serialize OAuth responses, request objects or API bodies.
            results['youtube']='update_failed'
    else:
        results['youtube']='not_configured'
    if os.environ.get('KICK_ACCESS_TOKEN') and os.environ.get('KICK_BROADCASTER_USER_ID'):
        try:
            results['kick']=kick_title(kick_request(os.environ['KICK_ACCESS_TOKEN']),title,os.environ['KICK_BROADCASTER_USER_ID'])
        except Exception:
            results['kick']='update_failed'
    else:
        results['kick']='not_configured'
    print(json.dumps(results))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
