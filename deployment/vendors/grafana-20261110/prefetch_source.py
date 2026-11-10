"""Fetch fixed Grafana source as verified Git objects for the grafanasource build context.

Large Git/HTTP packs were reproducibly truncated on this network. Native Git
partial-clone requests preserve object validation while bounding each transfer.
Only failed EOF batches are split; no TLS, checksum, or source check is disabled.
No source files are generated or replaced, and existing source objects are reused.
"""
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import argparse
import hashlib
import json
import os
import subprocess
import time

REPO: Path
OUT: Path
UPSTREAM='https://github.com/grafana/grafana.git'
TAG='refs/tags/v13.2.5'
EXPECTED='ce471d869a4810cd83c987dd3521c4b67689dbb6'
LOCK=Lock()
COUNTER=0
EVENTS=[]


def local_blobs():
    result=subprocess.run(['git','--git-dir',str(REPO),'cat-file','--batch-all-objects','--batch-check=%(objectname) %(objecttype)'],capture_output=True,check=True)
    return {line.split()[0] for line in result.stdout.decode().splitlines() if line.endswith(' blob')}


def get(ids,depth=0):
    global COUNTER
    with LOCK:
        COUNTER+=1
        number=COUNTER
    log=OUT/'blob-fetch-logs'/('batch-'+str(number).zfill(4)+'.log')
    command=['git','-c','credential.helper=','-c','http.version=HTTP/1.1','-c','fetch.negotiationAlgorithm=noop',
        '--git-dir',str(REPO),'fetch','--no-tags','--no-write-fetch-head','--recurse-submodules=no',
        '--filter=blob:none','--no-auto-maintenance','origin','--stdin']
    started=time.monotonic()
    result=subprocess.run(command,input=('\n'.join(ids)+'\n').encode(),env={**os.environ,'GIT_TERMINAL_PROMPT':'0'},capture_output=True,timeout=90)
    log.write_bytes(result.stdout+result.stderr)
    event={'number':number,'requested':len(ids),'depth':depth,'exit_code':result.returncode,
           'elapsed_seconds':round(time.monotonic()-started,3),'log':str(log.relative_to(OUT))}
    with LOCK:EVENTS.append(event)
    if result.returncode==0:return len(ids)
    text=(result.stdout+result.stderr).decode(errors='replace')
    if len(ids)>1 and any(value in text for value in ['early EOF','unexpected eof','RPC failed','invalid index-pack','did not send all necessary objects']):
        split=len(ids)//2
        return get(ids[:split],depth+1)+get(ids[split:],depth+1)
    raise RuntimeError('Exact blob fetch failed; '+str(log))


def save(document):
    (OUT/'blob-prefetch-progress.json').write_text(json.dumps(document,indent=2)+'\n')


def main():
    global REPO, OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Bare Git source context directory')
    parser.add_argument('--evidence', type=Path, required=True, help='New evidence directory; existing evidence is preserved')
    args=parser.parse_args()
    REPO=args.output.resolve()
    OUT=args.evidence.resolve()
    OUT.mkdir(parents=True, exist_ok=False)
    env={**os.environ,'GIT_TERMINAL_PROMPT':'0'}
    def git(*command):
        return subprocess.run(['git','-c','credential.helper=','-c','http.version=HTTP/1.1',
            '--git-dir',str(REPO),*command], env=env, capture_output=True, check=True)
    if not REPO.exists():
        subprocess.run(['git','init','--bare',str(REPO)], env=env, check=True, capture_output=True)
        git('remote','add','origin',UPSTREAM)
        git('config','remote.origin.promisor','true')
        git('config','remote.origin.partialclonefilter','blob:none')
    if git('rev-parse','--is-bare-repository').stdout.strip()!=b'true':
        raise ValueError('Output must be a bare Git repository')
    if git('remote','get-url','origin').stdout.decode().strip()!=UPSTREAM:
        raise ValueError('Existing source repository is not the fixed official upstream')
    tag=subprocess.run(['git','--git-dir',str(REPO),'rev-parse',TAG+'^{commit}'],capture_output=True)
    if tag.returncode:
        fetched=git('fetch','--filter=blob:none','--depth=1','origin',TAG+':'+TAG)
        (OUT/'initial-fetch.log').write_bytes(fetched.stdout+fetched.stderr)
    tree=git('ls-tree','-r','-z','--full-tree',TAG).stdout
    (OUT/'git-ls-tree.z').write_bytes(tree)
    if hashlib.sha256(tree).hexdigest()!='1829f0ac8d263e1c9fb5eabf1a2b1a89b35be9bf752ee82796c9fd146e0bfac2':
        raise ValueError('Fixed upstream tree identity changed')
    commit=subprocess.check_output(['git','--git-dir',str(REPO),'rev-parse','refs/tags/v13.2.5^{commit}']).decode().strip()
    assert commit==EXPECTED
    raw=(OUT/'git-ls-tree.z').read_bytes()
    wanted=list(dict.fromkeys(row.split(b'\t',1)[0].decode().split()[2] for row in raw.split(b'\0') if row))
    before=local_blobs()
    pending=[key for key in wanted if key not in before]
    (OUT/'blob-fetch-logs').mkdir(exist_ok=True)
    document={'started_at_utc':datetime.now(timezone.utc).isoformat(),'upstream_url':'https://github.com/grafana/grafana.git',
      'upstream_commit':commit,'file_tree_sha256':hashlib.sha256(raw).hexdigest(),'required_unique_blobs':len(wanted),
      'already_present_required_blobs':len(set(wanted)&before),'batch_size':128,'workers':3,'events':EVENTS,
      'completed':False,'boundary':'Git native lazy fetch of exact pinned tree blob IDs; only failed EOF batches are recursively split, no skipped or fabricated object'}
    completed=0
    chunks=[pending[n:n+128] for n in range(0,len(pending),128)]
    save(document)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(get,chunk) for chunk in chunks]
        for future in as_completed(futures):
            completed+=future.result()
            document['received_required_blobs_from_this_run']=completed
            document['events']=sorted(EVENTS,key=lambda event:event['number'])
            save(document)
            if completed%1280==0 or completed==len(pending):print(json.dumps({'received':completed,'pending_total':len(pending),'events':len(EVENTS)}),flush=True)
    actual=local_blobs()
    assert set(wanted)<=actual,'Not every expected source blob arrived'
    check=subprocess.run(['git','--git-dir',str(REPO),'fsck','--full'],capture_output=True)
    (OUT/'git-fsck.log').write_bytes(check.stdout+check.stderr)
    assert check.returncode==0,'Git object graph integrity failed'
    document['required_blobs_present_after']=len(set(wanted)&actual)
    document['git_fsck_exit_code']=check.returncode
    document['pack_bytes']=sum(p.stat().st_size for p in (REPO/'objects/pack').glob('*.pack'))
    document['completed_at_utc']=datetime.now(timezone.utc).isoformat()
    document['completed']=True
    save(document)
    print(json.dumps({key:document[key] for key in ['required_unique_blobs','required_blobs_present_after','pack_bytes','git_fsck_exit_code','completed']}),flush=True)


if __name__=='__main__':main()
