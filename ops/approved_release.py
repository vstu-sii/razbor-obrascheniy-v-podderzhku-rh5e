"""A rollback must be an ancestor with the daily guards and successful quality gates."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
from release import revision


def approve(sha):
    revision(sha)
    subprocess.run(['git','merge-base','--is-ancestor',sha,'HEAD'],check=True,capture_output=True)
    prefix='project/' if Path('project/backend/limits/daily.py').exists() else ''
    for relative in ('backend/limits/daily.py','backend/guards/inference.py','migrations/005_daily_calls.sql'):
        path=prefix+relative
        subprocess.run(['git','cat-file','-e',sha+':'+path],check=True,capture_output=True)
    if sha==os.environ.get('GITHUB_SHA'):
        return  # This run tests/builds the same exact revision before deployment.
    repository=os.environ['GITHUB_REPOSITORY']
    request=urllib.request.Request('https://api.github.com/repos/'+repository+'/commits/'+sha+'/check-runs?per_page=100',
        headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request,timeout=15) as response: runs=json.load(response)['check_runs']
    for required in ('full-evals-and-security','postgres-event-scenarios'):
        matching=[run for run in runs if run['name'].endswith(required)]
        if not matching or matching[0]['conclusion']!='success':
            raise RuntimeError('Rollback revision lacks successful quality gates')


if __name__=='__main__':
    try:approve(sys.argv[1])
    except Exception:raise SystemExit('Release not approved: require compatible tested ancestor; credentials omitted') from None
