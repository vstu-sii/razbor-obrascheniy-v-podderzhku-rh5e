"""Deploy/rollback an exact Git SHA and wait for that revision, never just HTTP 200."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.parse
import urllib.request


def revision(value):
    if not re.fullmatch('[0-9a-f]{40}',value):
        raise ValueError('Expected a full lowercase Git commit SHA')
    return value


def hook_url(value, sha):
    parts=urllib.parse.urlsplit(value)
    if parts.scheme!='https' or parts.hostname!='api.render.com' or parts.username or parts.password or not parts.path.startswith('/deploy/srv-'):
        raise ValueError('RENDER_DEPLOY_HOOK must be a Render HTTPS deploy hook')
    query=dict(urllib.parse.parse_qsl(parts.query));query['ref']=revision(sha)
    if not query.get('key'): raise ValueError('Deploy hook key is missing')
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def wait_for_revision(public_url, sha, timeout=600, fetch=urllib.request.urlopen, sleep=time.sleep):
    parts=urllib.parse.urlsplit(public_url)
    if parts.scheme!='https' or parts.username or parts.password:
        raise ValueError('PUBLIC_URL must be an HTTPS URL without credentials')
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            with fetch(public_url.rstrip('/')+'/health',timeout=15) as response:
                value=json.load(response)
                if value.get('status')=='ok' and value.get('revision')==sha and value.get('infrastructure')=='lab04':
                    return value
        except Exception:
            pass
        sleep(5)
    raise RuntimeError('Requested revision did not become healthy before deadline')


def deploy(sha):
    url=hook_url(os.environ.get('RENDER_DEPLOY_HOOK',''),sha)
    public=os.environ.get('PUBLIC_URL','')
    if not public: raise ValueError('PUBLIC_URL is missing')
    # Sanitize every network failure: hook URLs must never enter exception logs.
    try:
        with urllib.request.urlopen(urllib.request.Request(url,method='POST'),timeout=30) as response:
            if response.status not in (200,202): raise RuntimeError()
    except Exception:
        raise RuntimeError('Render deployment request failed; inspect hosting privately') from None
    wait_for_revision(public,sha)
    print(json.dumps({'result':'healthy','revision':sha}))


def compose_rollback(image, project):
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:/@-]+',image): raise ValueError('Invalid image reference')
    if not re.fullmatch('[a-z0-9][a-z0-9_-]+',project): raise ValueError('Invalid compose project')
    root=Path(__file__).resolve().parents[1]
    env=dict(os.environ,TRIAGE_IMAGE=image,BASELINE_MODE='extractive')
    subprocess.run(['docker','compose','-p',project,'-f',str(root/'compose.yml'),'-f',str(root/'compose.staging.yml'),
        'up','-d','--wait','--no-build','app','ai','worker','ingress'],env=env,check=True)
    print('Compatible image selected; database and volumes retained, inference is extractive')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('deploy','rollback','verify','compose-rollback'))
    parser.add_argument('--sha');parser.add_argument('--image');parser.add_argument('--project',default='lab04-staging')
    args=parser.parse_args()
    try:
        if args.action=='compose-rollback': compose_rollback(args.image or '',args.project)
        elif args.action=='verify': wait_for_revision(os.environ.get('PUBLIC_URL',''),revision(args.sha or ''))
        else: deploy(revision(args.sha or ''))
    except Exception:
        raise SystemExit('Release failed: verify configuration, compatibility and hosting; secret values omitted') from None


if __name__=='__main__':main()
