"""Build one inventory-selected image with structured arguments, never shell interpolation."""
import os
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
context = (root / os.environ['SCAN_BUILD_CONTEXT']).resolve()
dockerfile = (context / os.environ['SCAN_DOCKERFILE']).resolve()
name = os.environ['SCAN_BUILD_NAME']
if (not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name) or not context.is_relative_to(root)
        or not dockerfile.is_relative_to(root) or not dockerfile.is_file()):
    raise SystemExit('Invalid inventory build target')
raise SystemExit(subprocess.run(['docker', 'build', '-f', str(dockerfile), '-t', 'deepresearch-scan-' + name + ':ci', str(context)], cwd=root, check=False).returncode)
