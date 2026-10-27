"""Host-only synthetic processes, no Docker, backup, providers or task registration."""
import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import host_task, recovery

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('module',[host_task,recovery])
def test_non_windows_subprocess_options_are_unchanged(module,monkeypatch):
    monkeypatch.setattr(module,'os',SimpleNamespace(name='posix'))
    assert module.hidden_process_options()=={}


@pytest.mark.skipif(os.name!='nt',reason='Windows startup structures are platform-specific')
def test_windows_children_use_no_window_and_explicit_hidden_startup():
    for module in (host_task,recovery):
        options=module.hidden_process_options()
        assert options['creationflags']==subprocess.CREATE_NO_WINDOW
        assert options['startupinfo'].dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert options['startupinfo'].wShowWindow==subprocess.SW_HIDE


@pytest.mark.skipif(os.name!='nt',reason='Requires actual windowless Windows Python')
@pytest.mark.parametrize('exit_code',[0,23])
def test_pythonw_bridge_logs_target_and_descendant_streams_preserving_exit(tmp_path,exit_code):
    pythonw=Path(sys.executable).with_name('pythonw.exe')
    assert pythonw.is_file()
    target=tmp_path/'synthetic_target.py'
    target.write_text("""import subprocess,sys
from scripts.recovery import hidden_process_options
print('entry stdout',flush=True)
print('entry stderr',file=sys.stderr,flush=True)
child=subprocess.run([sys.executable,'-c',"import sys; print('child stdout'); print('child stderr',file=sys.stderr); sys.exit(17)"],stdout=sys.stdout,stderr=sys.stderr,**hidden_process_options())
assert child.returncode==17
raise SystemExit(int(sys.argv[1]))
""",encoding='utf-8')
    log=tmp_path/'task.log'
    result=subprocess.run([str(pythonw),str(ROOT/'scripts/host_task.py'),'--log-file',str(log),str(target),str(exit_code),
                           'private-fixture-argument-must-not-be-logged'],
                          capture_output=True,env={**os.environ,'PYTHONPATH':str(ROOT)},
                          **host_task.hidden_process_options())
    text=log.read_text(encoding='utf-8')
    assert result.returncode==exit_code and not result.stdout and not result.stderr
    assert all(marker in text for marker in ['entry stdout','entry stderr','child stdout','child stderr'])
    assert 'private-fixture-argument-must-not-be-logged' not in text
    events=[json.loads(line) for line in text.splitlines() if line.startswith('{')]
    assert events[0]['event']=='host_task_started'
    assert events[-1]['event']=='host_task_finished' and events[-1]['returncode']==exit_code
    # Opt-in local evidence capture; normal CI tests only use their temporary folder.
    if directory:=os.environ.get('DR_HOST_TASK_EVIDENCE_DIR'):
        folder=Path(directory)
        folder.mkdir(parents=True,exist_ok=True)
        raw=folder/f'pythonw-exit-{exit_code}.log'
        raw.write_bytes(log.read_bytes())
        (folder/f'pythonw-exit-{exit_code}.json').write_text(json.dumps({
            'actual_interpreter':'pythonw.exe','exit_code':result.returncode,'descendant_exit_code':17,
            'stdout_and_stderr_saved':True,'outer_console_output_bytes':len(result.stdout)+len(result.stderr),
            'creationflags':host_task.hidden_process_options()['creationflags'],
            'startup_hidden':host_task.hidden_process_options()['startupinfo'].wShowWindow==0,
            'raw_log_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'supplier_http':0},indent=2)+'\n',encoding='utf-8')


def test_recovery_child_failure_is_redacted_and_keeps_native_exit(monkeypatch,capsys):
    captured={}
    def child(args,**kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args,17,stdout=b'',stderr=b'private-fixture-secret')
    monkeypatch.setattr(recovery.subprocess,'run',child)
    with pytest.raises(RuntimeError,match='exit 17') as error:
        recovery.run(['synthetic-child'])
    assert 'private-fixture-secret' not in str(error.value)
    if os.name=='nt':
        assert captured['creationflags']==subprocess.CREATE_NO_WINDOW
        error_log=capsys.readouterr().err
        assert json.loads(error_log)['exit_code']==17 and 'private-fixture-secret' not in error_log


def test_every_recovery_process_site_has_hidden_options():
    tree=ast.parse(inspect.getsource(recovery))
    calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
           and isinstance(node.func.value,ast.Name) and node.func.value.id=='subprocess' and node.func.attr in {'run','Popen'}]
    assert len(calls)==3
    for call in calls:
        assert any(keyword.arg is None and isinstance(keyword.value,ast.Call)
                   and isinstance(keyword.value.func,ast.Name) and keyword.value.func.id=='hidden_process_options'
                   for keyword in call.keywords)


def test_installer_requires_gui_python_and_current_interactive_limited_principal():
    text=(ROOT/'scripts/install_backup_task.ps1').read_text(encoding='utf-8')
    assert "$pythonExecutable = Join-Path $workspacePath '.venv\\Scripts\\pythonw.exe'" in text
    assert 'no console fallback is permitted' in text
    assert '-LogonType Interactive -RunLevel Limited' in text
    assert text.count('-Principal $principal')==2
    assert '-Password' not in text and '-RunLevel Highest' not in text and 'Start-ScheduledTask' not in text
    for entry in ['scheduled_backup.py','ops_probe.py']:
        source=(ROOT/'scripts'/entry).read_text(encoding='utf-8')
        assert "if __name__ == '__main__':" in source and 'if needs_windowless_bridge():' in source
        assert 'run_logged_target(__file__, sys.argv[1:]' in source
