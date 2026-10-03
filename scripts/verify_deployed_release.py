"""Verify public Pages bytes against the exact tree uploaded by this run."""
import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REMOVED = ('USA-SOURCE-IPTV-EPG.xml.gz', 'USA-SOURCE-EPGSHARE-US2.xml.gz',
           'USA-SOURCE-EPGSHARE-LOCALS.xml.gz', 'USA-SOURCE-EPGSHARE-SPORTS.xml.gz',
           'USA-SOURCES.txt')


def download(url):
    request=urllib.request.Request(url,headers={'Accept-Encoding':'identity','Cache-Control':'no-cache'})
    with urllib.request.urlopen(request,timeout=120) as response:
        return response.read()


def verify_public(source, base_url, fetch=download):
    if urllib.parse.urlparse(base_url).scheme!='https':
        raise ValueError('public release requires HTTPS')
    release=(source/'release.json').read_bytes()
    expected=json.loads(release)
    version=hashlib.sha256(release).hexdigest()
    def url(name):
        return base_url.rstrip('/')+'/'+name+'?release='+version
    if fetch(url('release.json'))!=release:
        raise ValueError('public release manifest differs from uploaded tree')
    checked={}
    for path in sorted(source.iterdir()):
        if not path.is_file() or path.name in ('release.json','.nojekyll'):
            continue
        local=hashlib.sha256(path.read_bytes()).hexdigest()
        if hashlib.sha256(fetch(url(path.name))).hexdigest()!=local:
            raise ValueError('public bytes differ: '+path.name)
        checked[path.name]=local
    removed=list(REMOVED)
    if not (source/'DE-AMAZON.xml.gz').exists():
        removed.append('DE-AMAZON.xml.gz')
    for name in removed:
        try:
            fetch(url(name))
        except urllib.error.HTTPError as exc:
            if exc.code==404:
                continue
            raise
        raise ValueError('retired product still publicly available: '+name)
    return {'status':'passed','release_sha256':version,'files':checked,'removed':removed,
            'producer_runs':{r:p['run_id'] for r,p in expected['producers'].items()}}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--url',required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    for attempt in range(5):
        try:
            result=verify_public(args.source,args.url)
            args.report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
            print('Public Pages bytes match the validated producer artifacts.')
            return
        except (OSError,ValueError) as exc:
            if attempt==4:
                raise
            print(f'Waiting for Pages propagation: {exc}',flush=True)
            time.sleep(20)


if __name__=='__main__':
    main()
