"""Run the unit tests and turn failures into GitHub Actions annotations.

Annotations show on the run page and through the API without opening the raw log.
Usage (CI): python tools/ci_report.py
"""
import re
import subprocess
import sys

result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
output = result.stdout
print(output)
if result.returncode:
    blocks = re.split(r'\n={50,}\n', output)
    for block in blocks[1:11]:
        title, _, body = block.partition('\n')
        lines = [line for line in body.splitlines() if line.strip() and not line.startswith('-' * 20)]
        message = '\n'.join(lines[-14:])[:3500]
        escaped = message.replace('%', '%25').replace('\r', '').replace('\n', '%0A')
        print(f'::error title={title.strip()[:200]}::{escaped}')
sys.exit(result.returncode)
