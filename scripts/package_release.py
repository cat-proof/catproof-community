"""Export tracked release source, excluding Git history and runtime state."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--ref', default='HEAD')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', args.version):
        parser.error('Use a version such as 0.1.0 or 0.1.0-rc.1')
    if not (ROOT / 'LICENSE').is_file():
        parser.error('Select and add the distribution license before packaging a release.')
    subprocess.run(['python3', 'scripts/check_release.py'], cwd=ROOT, check=True)
    tracked_license = subprocess.run(['git', 'cat-file', '-e', args.ref + ':LICENSE'], cwd=ROOT, capture_output=True)
    if tracked_license.returncode:
        parser.error('The selected Git ref does not contain a license.')
    directory = ROOT / 'release'
    directory.mkdir(exist_ok=True)
    name = f'catproof-community-{args.version}'
    path = directory / (name + '.tar.gz')
    with path.open('xb') as output:
        subprocess.run(['git', 'archive', '--format=tar.gz', '--prefix=' + name + '/', args.ref], cwd=ROOT, stdout=output, check=True)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / 'SHA256SUMS.txt').write_text(checksum + '  ' + path.name + '\n')
    print(path)


if __name__ == '__main__':
    main()
