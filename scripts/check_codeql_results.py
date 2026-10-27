"""Keep uploaded HIGH/CRITICAL CodeQL security findings as a failing gate."""
import json
from pathlib import Path
import sys

count, files = 0, list(Path(sys.argv[1]).glob('*.sarif'))
if not files:
    raise SystemExit('CodeQL SARIF missing; cannot claim source scan passed')
for path in files:
    for run in json.loads(path.read_text(encoding='utf-8')).get('runs', []):
        rules = run.get('tool', {}).get('driver', {}).get('rules', [])
        by_id = {rule['id']: rule for rule in rules}
        for result in run.get('results', []):
            rule = by_id.get(result.get('ruleId'), {})
            value = rule.get('properties', {}).get('security-severity')
            if value is not None and float(value) >= 7:
                count += 1
print(f'CodeQL HIGH/CRITICAL security findings: {count}')
raise SystemExit(1 if count else 0)
