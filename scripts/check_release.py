"""Bounded source/package checks. Findings never print matching secret values."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'GitHub token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'OpenAI key': re.compile(rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{35,}\b'),
    'AWS access ID': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'Slack token': re.compile(rb'\bxox[baprs]-[A-Za-z0-9-]{20,}\b'),
}


def main():
    try:
        names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    except subprocess.CalledProcessError:
        names = [str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file() and '.git' not in p.parts]
    findings = []
    for name in filter(None, names):
        path = ROOT / name
        if not path.is_file():
            continue
        if path.name.startswith('.env') or path.suffix in {'.db', '.sqlite3', '.pem', '.key', '.dump'}:
            findings.append((name, 'runtime or secret file'))
        if name.startswith(('.codex/', '.agents/', '.entire/')):
            findings.append((name, 'private tooling metadata'))
        data = path.read_bytes()
        for label, pattern in PATTERNS.items():
            if pattern.search(data):
                findings.append((name, label))
    if findings:
        for name, label in findings:
            print(f'BLOCKED: {name}: {label}', file=sys.stderr)
        return 1
    print(f'Checked {len(list(filter(None, names)))} source files for known credential formats and excluded runtime files. This is not a comprehensive security audit.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
