"""Self-consistent outer SHA cannot conceal missing runtime content or false DiffIDs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.backup_images import verify_image_bundle
from scripts import recovery
from tests.image_bundle_fixture import bundle
from tests.test_recovery_manifest import archive


@pytest.mark.parametrize('aliased_layer',[False,True])
def test_current_platform_tiny_bundle_is_complete_without_docker(tmp_path,aliased_layer):
    data,images=bundle(aliased_layer=aliased_layer)
    path=tmp_path/'images.tar'
    path.write_bytes(data)
    proof=verify_image_bundle(path,images)
    assert proof['image_roots']==2 and proof['selected_manifests']==2
    assert proof['descriptor_visits']==4
    assert proof['verified_unique_layer_diffids']==1 and proof['docker_cache_consulted'] is False


@pytest.mark.parametrize('damage,message',[('missing_layer','blob is missing'),('wrong_diffid','DiffID differs')])
def test_self_consistent_package_rejected_even_with_all_images_would_be_cached(tmp_path,monkeypatch,damage,message):
    manifest=archive(tmp_path)
    data,images=bundle(**{damage:True})
    (tmp_path/'images.tar').write_bytes(data)
    configuration=json.loads((tmp_path/'deployment.private.json').read_bytes())
    configuration['services']['api']['image']=images[0]
    (tmp_path/'deployment.private.json').write_text(json.dumps(configuration))
    manifest['images']['api'],manifest['helper_image']=images
    manifest['files']['images.tar']=recovery.sha(tmp_path/'images.tar')
    manifest['files']['deployment.private.json']=recovery.sha(tmp_path/'deployment.private.json')
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    def forbidden(*args,**kwargs):
        pytest.fail('Bundle failure must precede cache inspection or any Docker resource action')
    monkeypatch.setattr(recovery,'run',forbidden)
    with pytest.raises(ValueError,match=message):
        recovery.restore(tmp_path,'dr-restore-counterexample')


def test_archived_restore_can_import_its_stdlib_verifier_without_repository(tmp_path):
    source=Path(recovery.__file__)
    shutil.copy2(source,tmp_path/'restore.py')
    shutil.copy2(source.with_name('backup_images.py'),tmp_path/'backup_images.py')
    result=subprocess.run([sys.executable,str(tmp_path/'restore.py'),'--help'],cwd=tmp_path,
                          env={**os.environ,'PYTHONPATH':''},capture_output=True)
    assert result.returncode==0 and b'backup,restore' in result.stdout
