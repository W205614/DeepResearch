"""Read actual ELF build metadata with the pinned official Go toolchain, without starting services."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import uuid

from scripts.go_stdlib_rebuild_20261112 import TOOLCHAIN, require


def capture(image, expected, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    inspected, = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))
    name = 'dr-go-native-facts-' + uuid.uuid4().hex[:12]
    cid = subprocess.check_output(['docker', 'create', '--name', name, '--network', 'none',
        '--label', 'com.deepresearch.purpose=go-native-facts', '--entrypoint', '/bin/true',
        inspected['Id']]).decode().strip()
    try:
        paths = {}
        for index, (target, expected_hash) in enumerate(expected.items()):
            require(not Path(target).is_absolute() and '..' not in Path(target).parts and '\\' not in target,
                    'Only normalized image targets may be read')
            filename = f'{index:02d}.elf'
            destination = output / filename
            subprocess.run(['docker', 'cp', cid + ':/' + target, str(destination)], check=True)
            with destination.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            require(actual == expected_hash, 'Actual ELF differs from the exact expected target')
            paths[target] = filename
        command = ['docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
                   '--label', 'com.deepresearch.purpose=go-native-parser',
                   '--mount', 'type=bind,source=' + str(output) + ',target=/facts,readonly',
                   '--entrypoint', 'go', TOOLCHAIN, 'version', '-m', '-json',
                   *('/facts/' + filename for filename in paths.values())]
        raw = subprocess.check_output(command)
        (output / 'go-version-raw.json').write_bytes(raw)
        decoder, cursor, values = json.JSONDecoder(), 0, []
        text = raw.decode('utf-8')
        while cursor < len(text):
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor == len(text):
                break
            value, cursor = decoder.raw_decode(text, cursor)
            values.append(value)
        require(len(values) == len(paths), 'Native parser output is missing or duplicated')
        result = {}
        for (target, filename), native in zip(paths.items(), values, strict=True):
            destination = output / (filename + '.native.json')
            destination.write_bytes((json.dumps(native, indent=2) + '\n').encode())
            result[target] = {'elf_sha256': expected[target], 'native_file': str(destination), 'native': native}
        (output / 'receipt.json').write_bytes((json.dumps({'image_id': inspected['Id'],
            'toolchain': TOOLCHAIN, 'command': command, 'service_started': False,
            'targets': {key: {k: v for k, v in value.items() if k != 'native'} for key, value in result.items()}}, indent=2) + '\n').encode())
        return result
    finally:
        actual, = json.loads(subprocess.check_output(['docker', 'inspect', cid]))
        require(actual['Name'] == '/' + name and actual['Image'] == inspected['Id']
                and actual['State']['Status'] == 'created' and actual['HostConfig']['NetworkMode'] == 'none'
                and actual['Config']['Labels'].get('com.deepresearch.purpose') == 'go-native-facts',
                'Only the owned never-started fact container may be removed')
        subprocess.run(['docker', 'rm', '-v', cid], check=True, stdout=subprocess.DEVNULL)
