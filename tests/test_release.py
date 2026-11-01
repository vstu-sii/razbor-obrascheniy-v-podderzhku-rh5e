"""Release control negative tests; no deploy or production HTTP request is sent."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('release_control',Path(__file__).resolve().parents[1]/'ops/release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_commit_is_exact_and_hook_cannot_target_another_host(self):
        sha='a'*40
        result=release.hook_url('https://api.render.com/deploy/srv-synthetic?key=synthetic&ref=old',sha)
        self.assertIn('ref='+sha,result)
        with self.assertRaises(ValueError):release.hook_url('https://example.test/deploy/srv-synthetic?key=synthetic',sha)
        with self.assertRaises(ValueError):release.revision('main')

    def test_successful_health_of_old_revision_does_not_complete_release(self):
        seen=[]
        def fetch(url,timeout):
            seen.append(url)
            return io.StringIO(json.dumps({'status':'ok','revision':'b'*40,'infrastructure':'lab04'}))
        with patch.object(release.time,'monotonic',side_effect=[0,1,3]):
            with self.assertRaises(RuntimeError):
                release.wait_for_revision('https://synthetic.onrender.com','a'*40,timeout=2,fetch=fetch,sleep=lambda _:None)
        self.assertEqual(len(seen),1)

    def test_health_must_expose_requested_revision(self):
        sha='a'*40
        fetch=lambda *args,**kwargs:io.StringIO(json.dumps({'status':'ok','revision':sha,'infrastructure':'lab04'}))
        self.assertEqual(release.wait_for_revision('https://synthetic.onrender.com',sha,fetch=fetch)['revision'],sha)

    def test_compose_rollback_retains_volume_and_never_builds_current_source(self):
        with patch.object(release.subprocess,'run') as called:
            release.compose_rollback('support-triage:previous-immutable','synthetic-audit')
        command=called.call_args.args[0]
        self.assertIn('--no-build',command)
        self.assertNotIn('down',command)
        self.assertNotIn('--volumes',command)
        self.assertEqual(called.call_args.kwargs['env']['BASELINE_MODE'],'extractive')
