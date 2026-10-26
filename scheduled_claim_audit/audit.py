"""Static checks for documented time-triggered execution."""
import argparse
import ast
import fnmatch
import json
import re
from pathlib import Path

SCHEDULED = (
    'runs on a schedule', 'runs on a cron schedule', 'runs on a timer',
    'runs periodically', 'runs automatically on a schedule',
    'runs automatically every day', 'runs automatically every hour',
    'runs automatically every week', 'runs every day', 'runs every hour',
    'runs every week', 'runs daily', 'runs nightly', 'runs weekly',
    'runs hourly', 'runs at regular intervals', 'runs at scheduled times',
    'runs at scheduled intervals', 'is scheduled to run', 'scheduled execution',
    'scheduled to run automatically', 'cron job', 'scheduled job',
    'scheduled task', 'periodic execution',
)
NOSCHEDULED = (
    'does not run on a schedule', 'does not run periodically',
    'does not run automatically', 'no scheduled execution', 'no scheduling',
    'not scheduled', 'no cron job', 'runs manually only', 'manual only',
    'must be run manually', 'must be started manually', 'no automatic scheduling',
)


def pattern(phrases):
    return re.compile(r'\b(?:' + '|'.join(re.escape(p) for p in sorted(phrases, key=len, reverse=True)) + r')\b')


POSITIVE = pattern(SCHEDULED)
NEGATIVE = pattern(NOSCHEDULED)


def claims(text):
    lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not line[match.end():].strip():
                fence = None
            continue
        if match:
            fence = match[1]
            continue
        lines.append(line)
    found = set()
    for paragraph in re.split(r'\n\s*\n', '\n'.join(lines)):
        normalized = re.sub(r'\s+', ' ', paragraph.lower().replace("n't", ' not').replace('-', ' '))
        if NEGATIVE.search(normalized):
            found.add('negative')
        if POSITIVE.search(NEGATIVE.sub(' ', normalized)):
            found.add('positive')
    return found


def code_evidence(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split('.')[0]] = item.name if item.asname else item.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = node.module + '.' + item.name

    def name(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return name(node.value) + '.' + node.attr
        if isinstance(node, ast.Call):
            return name(node.func) + '()'
        return ''

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    names = [name(node.func) for node in calls]
    imported = {value.split('.')[0] for value in aliases.values()}
    result = []
    if 'schedule' in imported:
        chain = any(n.startswith('schedule.every()') and n.endswith('.do') for n in names)
        loop = any(name(call.func) == 'schedule.run_pending' for node in ast.walk(tree)
                   if isinstance(node, (ast.For, ast.While, ast.AsyncFor))
                   for stmt in node.body for call in ast.walk(stmt) if isinstance(call, ast.Call))
        if chain and loop:
            result.append('E1 schedule')
    if 'apscheduler' in imported:
        constructor = any(n.startswith('apscheduler.') and n.split('.')[-1].endswith('Scheduler') for n in names)
        if constructor and any(n.endswith('.add_job') for n in names) and any(n.endswith('.start') for n in names):
            result.append('E2 APScheduler')
    if 'sched' in imported and 'sched.scheduler' in names:
        if any(n.endswith(('.enter', '.enterabs')) for n in names) and any(n.endswith('.run') for n in names):
            result.append('E3 sched')
    return result


def invocation(command, root, scripts):
    # Match interpreter names in audited command data, including legacy spellings.
    if re.search(r'\b(?:pytest|unittest|tox|coverage|ruff|flake8|mypy|black)\b|\bpip\s+install\b|\bpython3?\s+-m\s+build\b', command):
        return False
    interpreter = re.search(r'(?<![\w-])(?:[^\s;|&]*/)?python3?\s+(.+)', command)
    if not interpreter:
        return False
    args = interpreter[1]
    module = re.search(r'(?:^|\s)-m\s+([\w.]+)', args)
    if module and (root / module[1].split('.')[0]).is_dir():
        return True
    for token in re.findall(r'''[^\s;|&"']+''', args):
        if token.endswith('.py'):
            target = (root / token).resolve()
            if root in target.parents and target.is_file():
                return True
        if token in scripts:
            return True
    return False


def manifest_kind(path):
    parts = path.parts
    if '.github' in parts and 'workflows' in parts and path.suffix in ('.yml', '.yaml'):
        return 'workflow'
    if path.name in ('crontab', 'Cronfile') or path.suffix == '.crontab' or 'cron.d' in parts:
        return 'crontab'
    if path.suffix in ('.timer', '.service'):
        return path.suffix[1:]
    return None


def manifest_evidence(path, text, texts, root, scripts):
    kind = manifest_kind(path)
    if kind == 'workflow':
        schedule = False
        cron = False
        commands = []
        block_indent = None
        for line in text.splitlines():
            if line and not line[0].isspace() and not line.startswith('#'):
                schedule = False
            if re.match(r'^\s*schedule:\s*(?:#.*)?$', line):
                schedule = True
            if schedule and re.match(r'''^\s*-?\s*cron:\s*(['"]).+\1\s*(?:#.*)?$''', line):
                cron = True
            match = re.match(r'^\s*-?\s*run:\s*(.*)', line)
            indent = len(line) - len(line.lstrip())
            if match:
                block_indent = indent if match[1].strip() in ('|', '>', '|-', '>-') else None
                commands.append(match[1].strip().strip('\'"'))
            elif block_indent is not None:
                if line.strip() and indent <= block_indent:
                    block_indent = None
                else:
                    commands.append(line.strip())
        return cron and any(invocation(c, root, scripts) for c in commands)
    if kind == 'crontab':
        for line in text.splitlines():
            if line.lstrip().startswith('#'):
                continue
            match = re.match(r'^\s*(?:@(?:daily|hourly|weekly|monthly)\s+|(?:[\d*/?,\-A-Za-z]+\s+){5})(.+)', line)
            if match and invocation(match[1], root, scripts):
                return True
    if kind == 'timer':
        for service, content in texts.items():
            if service.suffix == '.service' and service.stem == path.stem:
                if any(invocation(m[1], root, scripts) for m in re.finditer(r'^\s*ExecStart=(.*)$', content, re.M)):
                    return True
    return False


def audit(root, ignore=(), fail_on='HIGH'):
    root = Path(root).resolve()
    if not root.is_dir() or root.stat().st_mode & 0o444 == 0:
        raise ValueError('root must be a readable directory')
    paths = sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink()
                   and p.name != '.env' and not p.name.startswith('.env.')
                   and not any(fnmatch.fnmatch(p.relative_to(root).as_posix(), glob) for glob in ignore))
    texts = {}
    for path in paths:
        rel = path.relative_to(root)
        if path.suffix == '.py' or manifest_kind(rel) or rel.as_posix() == 'pyproject.toml' or (path.suffix.lower() == '.md' and ('docs' in rel.parts or rel.as_posix() == 'README.md')):
            texts[rel] = path.read_text(encoding='utf-8', errors='replace')
    scripts = set()
    config = texts.get(Path('pyproject.toml'), '')
    section = re.search(r'^\[project\.scripts\]\s*\n(.*?)(?=^\[|\Z)', config, re.M | re.S)
    if section:
        scripts = set(re.findall(r'^\s*[\'"]?([\w.-]+)[\'"]?\s*=', section[1], re.M))
    locations = {'positive': [], 'negative': []}
    evidence = []
    for rel, content in texts.items():
        if rel.suffix.lower() == '.md':
            for family in claims(content):
                locations[family].append(rel.as_posix())
        test = 'tests' in rel.parts or (rel.suffix == '.py' and rel.name.startswith('test_'))
        for label in code_evidence(content) if rel.suffix == '.py' else []:
            evidence.append({'path': rel.as_posix(), 'kind': label, 'test_only': test})
        # A production timer cannot borrow a test service as production evidence.
        services = {p: t for p, t in texts.items() if test or 'tests' not in p.parts}
        if manifest_evidence(rel, content, services, root, scripts):
            evidence.append({'path': rel.as_posix(), 'kind': manifest_kind(rel), 'test_only': test})
    prod = any(not e['test_only'] for e in evidence)
    findings = []
    if locations['positive'] and not prod:
        severity = 'MEDIUM' if evidence or all('tests' in Path(p).parts for p in locations['positive']) else 'HIGH'
        findings.append({'rule': 'documented-scheduled-claim-false', 'severity': severity,
                         'message': 'Time-triggered execution claim lacks production scheduler evidence.', 'paths': locations['positive']})
    if locations['negative'] and prod:
        findings.append({'rule': 'documented-no-scheduled-claim-false', 'severity': 'HIGH',
                         'message': 'Manual-execution claim conflicts with production scheduler evidence.', 'paths': locations['negative']})
    failed = any(f['severity'] == 'HIGH' or fail_on == 'MEDIUM' for f in findings)
    return {'verdict': 'FAIL' if failed else 'PASS', 'findings': findings, 'claims': locations, 'evidence': evidence}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(1, 'usage error: ' + message + '\n')


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', action='append', default=[])
    parser.add_argument('--fail-on', choices=('HIGH', 'MEDIUM'), default='HIGH')
    parser.add_argument('--version', action='version', version='0.1.0')
    args = parser.parse_args(argv)
    try:
        result = audit(args.root, args.ignore, args.fail_on)
    except (OSError, ValueError) as exc:
        parser.exit(1, 'usage error: ' + str(exc) + '\n')
    if args.json:
        print(json.dumps(result))
    else:
        print(result['verdict'])
        for finding in result['findings']:
            print('{severity} {rule}: {message}'.format(**finding))
        if args.verbose:
            for item in result['evidence']:
                print('Evidence: {path} ({kind}, test_only={test_only})'.format(**item))
    return 2 if result['verdict'] == 'FAIL' else 0
