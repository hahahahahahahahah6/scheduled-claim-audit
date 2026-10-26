import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scheduled_claim_audit.audit import audit, claims, main, SCHEDULED, NOSCHEDULED

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = 'import schedule\nschedule.every().day.do(job)\nwhile True:\n    schedule.run_pending()\n'
APS = 'from apscheduler.schedulers.background import BackgroundScheduler\ns = BackgroundScheduler()\ns.add_job(job, "interval")\ns.start()\n'
SCHED = 'import sched\ns = sched.scheduler()\ns.enter(2, 1, job)\ns.run()\n'
WORKFLOW = 'on:\n  schedule:\n    - cron: "0 2 * * *"\njobs:\n  run:\n    steps:\n      - run: python3 -m mytool\n'


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('README.md', 'The tool runs on a schedule.')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def check(self, severity=None, rule='documented-scheduled-claim-false', **kwargs):
        result = audit(self.root, **kwargs)
        if severity is None:
            self.assertEqual(result['findings'], [])
            self.assertEqual(result['verdict'], 'PASS')
        else:
            self.assertEqual(len(result['findings']), 1)
            self.assertEqual(result['findings'][0]['severity'], severity)
            self.assertEqual(result['findings'][0]['rule'], rule)
        return result

    def cli(self, *args):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(list(args))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_01_schedule_fixture(self):
        shutil.copytree(ROOT / 'tests/fixtures/good', self.root, dirs_exist_ok=True)
        self.check()
        self.assertEqual(self.cli(str(self.root))[0], 0)

    def test_02_apscheduler(self):
        self.write('job.py', APS)
        self.write('README.md', 'It runs nightly.')
        self.check()

    def test_03_sched(self):
        self.write('job.py', SCHED)
        self.write('README.md', 'It runs every hour.')
        self.check()

    def test_04_workflow(self):
        self.write('mytool/__init__.py', '')
        self.write('.github/workflows/run.yml', WORKFLOW)
        self.check()

    def test_05_crontab(self):
        self.write('job.py', '')
        self.write('README.md', 'It runs daily.')
        self.write('crontab', '0 2 * * * python3 job.py')
        self.check()

    def test_06_systemd(self):
        self.write('README.md', 'It runs weekly.')
        self.write('job.py', '')
        self.write('system/job.timer', '[Timer]\nOnCalendar=weekly')
        self.write('services/job.service', '[Service]\nExecStart=/usr/bin/python3 job.py')
        self.check()

    def test_07_bad_fixture(self):
        shutil.copytree(ROOT / 'tests/fixtures/bad', self.root, dirs_exist_ok=True)
        self.check('HIGH')
        code, output, _ = self.cli(str(self.root), '--json')
        self.assertEqual(code, 2)
        self.assertEqual(len(json.loads(output)['findings']), 1)

    def test_08_scheduled_ci_only(self):
        self.write('.github/workflows/ci.yml', WORKFLOW.replace('python3 -m mytool', 'python3 -m pytest'))
        self.check('HIGH')
        self.assertEqual(self.cli(str(self.root))[0], 2)

    def test_09_timer_only(self):
        self.write('job.timer', '[Timer]\nOnCalendar=weekly')
        self.check('HIGH')

    def test_10_negative_with_evidence(self):
        self.write('README.md', 'It does not run on a schedule.')
        self.write('job.py', APS)
        self.check('HIGH', 'documented-no-scheduled-claim-false')
        self.assertEqual(self.cli(str(self.root))[0], 2)

    def test_11_no_claim(self):
        self.write('README.md', 'A useful command line tool.')
        self.write('job.py', APS)
        self.check()

    def test_12_positive_no_source(self):
        self.check('HIGH')

    def test_12_negative_no_source(self):
        self.write('README.md', 'It does not run on a schedule.')
        self.check()

    def test_13_croniter_only(self):
        self.write('job.py', 'from croniter import croniter\ncroniter("* * * * *")')
        self.check('HIGH')

    def test_13_timer_only(self):
        self.write('job.py', 'import threading\nthreading.Timer(5, job).start()')
        self.check('HIGH')

    def test_13_async_sleep(self):
        self.write('job.py', 'import asyncio\nasync def main():\n    while True:\n        await asyncio.sleep(5)')
        self.check('HIGH')

    def test_13_time_sleep(self):
        self.write('job.py', 'import time\nwhile True:\n    time.sleep(5)')
        self.check('HIGH')

    def test_13_schedule_import_only(self):
        self.write('job.py', 'import schedule')
        self.check('HIGH')

    def test_13_every_without_pending(self):
        self.write('job.py', 'import schedule\nschedule.every().day.do(job)')
        self.check('HIGH')

    def test_13_pending_outside_loop(self):
        self.write('job.py', 'import schedule\nschedule.every().day.do(job)\nschedule.run_pending()')
        self.check('HIGH')

    def test_13_aps_import_only(self):
        self.write('job.py', 'import apscheduler')
        self.check('HIGH')

    def test_13_add_without_start(self):
        self.write('job.py', APS.replace('s.start()', ''))
        self.check('HIGH')

    def test_13_start_without_add(self):
        self.write('job.py', APS.replace('s.add_job(job, "interval")', ''))
        self.check('HIGH')

    def test_13_sched_import_only(self):
        self.write('job.py', 'import sched')
        self.check('HIGH')

    def test_13_sched_construct_only(self):
        self.write('job.py', 'import sched\ns = sched.scheduler()')
        self.check('HIGH')

    def test_13_enter_without_run(self):
        self.write('job.py', SCHED.replace('s.run()', ''))
        self.check('HIGH')

    def test_13_names_only(self):
        self.write('job.py', 'def schedule(): pass\nclass scheduled: pass\nclass BackgroundScheduler: pass\nBackgroundScheduler()\nx.add_job(job)\nx.start()')
        self.check('HIGH')

    def test_13_named_pending(self):
        self.write('job.py', 'import schedule\nschedule.every().day.do(job)\ndef run_pending(): pass\nwhile True:\n    run_pending()')
        self.check('HIGH')

    def test_13_strings_comments(self):
        self.write('job.py', repr(SCHEDULE) + '\n# ' + SCHED.replace('\n', '\n# '))
        self.check('HIGH')

    def test_13_cron_no_step(self):
        self.write('.github/workflows/job.yml', 'on:\n  schedule:\n    - cron: "0 2 * * *"')
        self.check('HIGH')

    def test_13_cron_tests(self):
        self.write('crontab', '0 2 * * * python3 -m pytest')
        self.check('HIGH')

    def test_13_nonexistent_script(self):
        self.write('crontab', '@daily python3 script.py')
        self.check('HIGH')

    def test_13_service_without_timer(self):
        self.write('job.py', '')
        self.write('job.service', 'ExecStart=python3 job.py')
        self.check('HIGH')

    def test_13_push_only(self):
        self.write('mytool/__init__.py', '')
        self.write('.github/workflows/job.yml', WORKFLOW.replace('  schedule:\n    - cron: "0 2 * * *"', '  push:'))
        self.check('HIGH')

    def test_13_crontab_comment(self):
        self.write('job.py', '')
        self.write('crontab', '# @daily python3 job.py')
        self.check('HIGH')

    def test_13_system_command(self):
        self.write('crontab', '@daily curl https://example.org')
        self.check('HIGH')

    def test_13_service_tests(self):
        self.write('job.timer', '[Timer]')
        self.write('job.service', 'ExecStart=python3 -m pytest')
        self.check('HIGH')

    def test_14_test_code(self):
        self.write('tests/job.py', SCHEDULE)
        self.check('MEDIUM')
        self.assertEqual(self.cli(str(self.root))[0], 0)
        self.assertEqual(self.cli(str(self.root), '--fail-on', 'MEDIUM')[0], 2)

    def test_14_test_filename(self):
        self.write('test_job.py', APS)
        self.check('MEDIUM')

    def test_14_test_manifest(self):
        self.write('job.py', '')
        self.write('tests/crontab', '@daily python3 job.py')
        self.check('MEDIUM')

    def test_15_fences(self):
        self.write('README.md', '```text\nruns on a schedule\n```\n\n~~~\nruns daily\n~~~')
        self.check()

    def test_15_inline(self):
        self.write('README.md', 'It `runs daily`.')
        self.check('HIGH')

    def test_16_negation(self):
        self.assertEqual(claims('It does not run on a schedule.'), {'negative'})
        self.assertEqual(claims("It doesn't run on a schedule."), {'negative'})

    def test_17_bare_nouns(self):
        self.write('README.md', 'schedule daily cron scheduled periodic timer scheduled maintenance schedule a meeting')
        self.check()

    def test_18_cli(self):
        self.assertEqual(self.cli('--version')[:2], (0, '0.1.0\n'))
        self.write('job.py', '')
        self.write('crontab', '@daily python3 job.py')
        self.assertEqual(json.loads(self.cli(str(self.root), '--json')[1])['verdict'], 'PASS')
        self.assertIn('Evidence:', self.cli(str(self.root), '--verbose')[1])
        self.assertEqual(self.cli(str(self.root), '--ignore', 'crontab')[0], 2)
        self.assertEqual(self.cli(str(self.root / 'missing'))[0], 1)
        self.assertEqual(self.cli()[0], 1)
        self.assertEqual(self.cli(str(self.root), '--fail-on', 'LOW')[0], 1)

    def test_19_self(self):
        result = audit(ROOT)
        self.assertEqual(result['verdict'], 'PASS')
        self.assertEqual(result['claims'], {'positive': [], 'negative': []})

    def test_20_env_guard(self):
        shutil.copytree(ROOT / 'tests/fixtures/env', self.root, dirs_exist_ok=True)
        original = Path.open
        def guarded(path, *args, **kwargs):
            self.assertNotEqual(path.name, '.env')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            self.check('HIGH')

    def test_21_packaging_conventions(self):
        config = (ROOT / 'pyproject.toml').read_text()
        self.assertIn('setuptools>=77', config)
        self.assertIn('license = "MIT"', config)
        self.assertIn('recursive-include tests', (ROOT / 'MANIFEST.in').read_text())
        for path in [ROOT / 'README.md', ROOT / 'pyproject.toml'] + list((ROOT / 'scheduled_claim_audit').glob('*.py')):
            # The parser may recognize legacy interpreter names in fixture data.
            for line in path.read_text().splitlines():
                if 'python3?' not in line and not line.startswith('requires-python ='):
                    self.assertIsNone(re.search(r'\bpython\b', line), (path, line))

    def test_aliases(self):
        for code in (SCHEDULE.replace('import schedule', 'import schedule as sch').replace('schedule.', 'sch.'),
                     SCHED.replace('import sched', 'from sched import scheduler as clock').replace('sched.scheduler()', 'clock()'),
                     APS.replace('import BackgroundScheduler', 'import BackgroundScheduler as Clock').replace('s = BackgroundScheduler()', 's = Clock()')):
            with self.subTest(code=code):
                self.write('job.py', code)
                self.check()

    def test_all_claim_templates(self):
        for phrase in SCHEDULED:
            with self.subTest(phrase=phrase):
                self.assertEqual(claims(phrase), {'positive'})
        for phrase in NOSCHEDULED:
            with self.subTest(phrase=phrase):
                self.assertEqual(claims(phrase), {'negative'})

    def test_normalization_boundaries_paragraphs(self):
        self.assertEqual(claims('RUNS-ON A\nschedule'), {'positive'})
        self.assertEqual(claims('runs on a\n\nschedule'), set())
        self.assertEqual(claims('overruns dailyish'), set())

    def test_docs_scope_and_negative_asymmetry(self):
        self.write('README.md', '')
        self.write('other.md', 'runs daily')
        self.check()
        self.write('nested/docs/intro.md', 'runs daily')
        self.check('HIGH')
        self.write('nested/docs/intro.md', '')
        self.write('tests/docs/intro.md', 'does not run on a schedule')
        self.write('job.py', APS)
        self.check('HIGH', 'documented-no-scheduled-claim-false')

    def test_two_families_max(self):
        self.write('README.md', 'runs daily runs weekly manual only')
        self.check('HIGH')

    def test_manifest_invocation_variants(self):
        self.write('job.py', '')
        self.write('mytool/__init__.py', '')
        self.write('pyproject.toml', '[project.scripts]\nmytool-cli = "mytool:main"\n')
        for filename, command in [('Cronfile', '@weekly python3 job.py'), ('jobs.crontab', '@monthly python3 -m mytool'), ('cron.d/job', '@hourly python3 mytool-cli')]:
            with self.subTest(filename=filename):
                self.write(filename, command)
                self.check()
                (self.root / filename).unlink()

    def test_mixed_test_or_install_command_rejected(self):
        self.write('mytool/__init__.py', '')
        for command in ('python3 -m pytest && python3 -m mytool', 'pip install x && python3 -m mytool', 'python3 -m build && python3 -m mytool'):
            self.write('crontab', '@daily ' + command)
            self.check('HIGH')

    def test_workflow_block_steps(self):
        self.write('mytool/__init__.py', '')
        self.write('.github/workflows/job.yaml', WORKFLOW.replace('run: python3 -m mytool', 'run: |\n          python3 -m mytool'))
        self.check()

    def test_no_cross_module_combination(self):
        self.write('a.py', 'import schedule\nschedule.every().day.do(job)')
        self.write('b.py', 'import schedule\nwhile True:\n    schedule.run_pending()')
        self.check('HIGH')

    def test_test_service_does_not_promote_timer(self):
        self.write('job.py', '')
        self.write('job.timer', '[Timer]')
        self.write('tests/job.service', 'ExecStart=python3 job.py')
        self.check('HIGH')

    def test_unreadable_root(self):
        self.root.chmod(0)
        try:
            self.assertEqual(self.cli(str(self.root))[0], 1)
        finally:
            self.root.chmod(0o700)


if __name__ == '__main__':
    unittest.main()
