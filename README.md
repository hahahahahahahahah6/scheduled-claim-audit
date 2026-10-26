# scheduled-claim-audit

Version 0.1.0. A static auditor for documentation about time triggered execution.
It compares root README and Markdown under any docs directory with AST scheduler
wiring and repository manifests. It never executes audited source. Runtime uses
only the standard library; the supported interpreter is Python3 3.9 or later.

Install and use:

```sh
python3 -m pip install .
scheduled-claim-audit /path/to/repository
python3 -m scheduled_claim_audit /path/to/repository --json
scheduled-claim-audit /path/to/repository --verbose --ignore 'vendor/*' --fail-on MEDIUM
scheduled-claim-audit --version
```

Claims use an exact phrase vocabulary, word boundaries, case folding, hyphen
normalization, contraction expansion, and paragraph joining. Fenced examples are
excluded; inline code counts. Negative spans are removed before positive matching.
Bare timing nouns do not trigger findings.

Evidence includes schedule every/do plus run_pending inside a loop, APScheduler
construction plus add_job and start, or sched construction plus enter/enterabs and
run, all within one module with matching imports. Aliases are resolved. Strings,
comments, one-shot timers, cron parsers, and sleep loops supply no evidence.

A GitHub Actions cron trigger, crontab entry, or paired systemd timer/service must
invoke repository code through an interpreter command. Recognized targets are an
existing repository script, a top-level module directory, or a declared console
script. Test, lint, build, and dependency-install commands are rejected. Workflow
block commands are inspected line by line. This intentionally narrow text scan
is not a YAML parser or shell interpreter. AST checks establish wiring presence,
not runtime reachability, receiver identity, or successful execution. Invalid
source syntax supplies no evidence. Unreadable selected files cause a usage error.
Symlink files are skipped. Environment files are never opened.

Production evidence excludes tests directories and test_ source files. Test-only
evidence produces MEDIUM for a positive claim; contradictory negative claims with
production evidence remain HIGH even in test documentation. At most one finding
per family is emitted. No claims means PASS regardless of evidence.

Rule identifiers:

```text
documented-scheduled-claim-false
documented-no-scheduled-claim-false
```

The default threshold is HIGH. MEDIUM findings are reported but require
`--fail-on MEDIUM` to fail. Exit codes: 0 for PASS, 2 for findings meeting the
threshold, 1 for usage or filesystem errors. JSON includes verdict, findings,
claim paths, and evidence; verbose human output lists evidence paths and kinds.
Repeat `--ignore` to exclude root-relative path globs.

Development:

```sh
python3 -m unittest discover -s tests -v
python3 -m pip install build
python3 -m build
python3 -m scheduled_claim_audit . --json
```

Tests and marker-only fixtures ship in the source distribution. MIT licensed.
