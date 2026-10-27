"""Offline representative lexical-memory probe. Keeps its fixture for review."""
import argparse
import asyncio
import json
import hashlib
import os
from pathlib import Path
import sys
import time
import tracemalloc
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.config import Settings  # noqa: E402
from backend.core.db import now  # noqa: E402
from backend.services.runtime import Runtime  # noqa: E402


def peak_rss():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = wintypes.HANDLE
        read_counters = ctypes.windll.psapi.GetProcessMemoryInfo
        read_counters.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        read_counters.restype = wintypes.BOOL
        if read_counters(process(), ctypes.byref(counters), counters.cb):
            return counters.PeakWorkingSetSize
        return None
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)


async def main(args):
    directory = Path(args.data_dir).resolve()
    existing = (directory/'research.sqlite3').exists()
    if existing and not args.reuse_fixture:
        raise ValueError('Use a new fixture directory; existing data is never overwritten')
    runtime = await Runtime(Settings(_env_file=None, demo_mode=True, auth_mode='development', queue_backend='local',
        document_scan_mode='disabled', data_dir=directory, reranker_enabled=False)).start()
    try:
        body = ('普通资料背景说明，研究制度需要完整证据和安全处理。'*500)[:10000]
        stamp = now()
        if existing:
            count = await runtime.db.one("SELECT COUNT(*) AS total FROM chunks")
            invalid = await runtime.db.one("SELECT COUNT(*) AS total FROM chunks WHERE text!=? OR user_id!='memory'", (body,))
            if count['total'] != args.chunks or invalid['total']:
                raise ValueError('Existing fixture does not match requested frozen content')
        else:
            async with runtime.db.connection() as conn:
                for start in range(0, args.chunks, 400):
                    doc_id = f'memory-{start//400:03d}'
                    await conn.execute("""INSERT INTO documents(id,user_id,name,hash,status,created_at,updated_at,index_version)
                        VALUES(?,'memory','固定中文资料',?,'ready',?,?,1)""", (doc_id,doc_id,stamp,stamp))
                    await conn.executemany("""INSERT INTO chunks(id,document_id,user_id,text,locator,vector,ordinal)
                        VALUES(?,?,'memory',?,'正文','[]',?)""", [(f'chunk-{index:06d}',doc_id,body,index-start)
                        for index in range(start,min(args.chunks,start+400))])
                await conn.commit()
        runtime.providers.embed=AsyncMock(return_value=[[0.0,0.0,1.0]])
        runtime.vectors.search=AsyncMock(return_value=[])
        projection=await runtime.db.one('SELECT COUNT(*) AS total FROM lexical_chunks')
        baseline_rss=peak_rss()
        tracemalloc.start()
        started=time.monotonic()
        result=await runtime.documents.search('memory',['制度安全证据'],limit=6)
        elapsed=time.monotonic()-started
        current,peak=tracemalloc.get_traced_memory()
        tracemalloc.stop()
        output={'fixture_chunks':args.chunks,'fixture_documents':(args.chunks+399)//400,
                'characters_per_chunk':len(body),'source_text_utf8_bytes':len(body.encode())*args.chunks,
                'heap_current_bytes':current,'heap_peak_bytes':peak,'process_peak_rss_before':baseline_rss,
                'process_peak_rss_after':peak_rss(),'elapsed_seconds':round(elapsed,3),'returned':len(result),
                'candidate_limit_per_query':getattr(runtime.settings,'rag_db_candidate_limit',128),
                'index_mode':'warm' if projection['total']==args.chunks else 'cold_or_partial',
                'implementation_sha256':hashlib.sha256(b''.join((Path(__file__).parents[1]/file).read_bytes()
                    for file in ('backend/services/lexical_retrieval.py','backend/services/documents.py'))).hexdigest(),
                'backfill_batch':32,'supplier_calls':0,'heap_limit_bytes':32*1024*1024,
                'boundary':'One offline SQLite fixture; index_mode distinguishes cold/partial backfill from warm query. Python heap and process RSS are distinct; not production throughput or SLA.'}
        Path(args.output).parent.mkdir(parents=True,exist_ok=True)
        Path(args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(output,ensure_ascii=False))
        if not result or peak>output['heap_limit_bytes']:
            raise AssertionError('Bounded lexical probe failed; retain fixture and output')
    finally:
        await runtime.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--chunks',type=int,default=4201)
    parser.add_argument('--reuse-fixture',action='store_true')
    asyncio.run(main(parser.parse_args()))
