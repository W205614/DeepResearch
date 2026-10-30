"""Durable change processing; semantic uncertainty never becomes an unchanged claim."""
import hashlib
import json
from difflib import SequenceMatcher
from ..core.db import now, uid
from ..infrastructure.providers import ServiceError


def structural_diff(old, new):
    changes = []
    a = [(x.get('locator', ''), x.get('text', '')) for x in old]
    b = [(x.get('locator', ''), x.get('text', '')) for x in new]
    for tag, i, j, k, end in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        for n in range(max(j-i, end-k)):
            before = old[i+n] if i+n < j else None
            after = new[k+n] if k+n < end else None
            changes.append({'kind': 'modified' if before and after else 'deleted' if before else 'added',
                            'old': before, 'new': after})
    return changes


class EvidenceLifecycle:
    def __init__(self, db):
        self.db = db

    async def archive_current_versions(self):
        """Backfill only existing current text; never invent deleted historical versions."""
        documents = await self.db.rows("""SELECT d.id,d.user_id FROM documents d WHERE d.status IN ('ready','rebuilding')
            AND NOT EXISTS(SELECT 1 FROM document_versions v WHERE v.document_id=d.id AND v.version=d.index_version)""")
        for item in documents:
            async with self.db.guard('documents:' + item['user_id']):
                document = await self.db.one("SELECT * FROM documents WHERE id=? AND status IN ('ready','rebuilding')", (item['id'],))
                if not document:
                    continue
                chunks = await self.db.rows('SELECT id,text,locator,ordinal FROM chunks WHERE document_id=? ORDER BY ordinal', (item['id'],))
                await self.db.execute("""INSERT OR IGNORE INTO document_versions(document_id,user_id,version,hash,name,object_key,status,blocks_json,chunks_json,parser_config,created_at)
                    VALUES(?,?,?,?,?,?,'ready',?,?,?,?)""", (document['id'],document['user_id'],document['index_version'],document['hash'],document['name'],document['object_key'] or document['id'],
                    json.dumps([{k:c[k] for k in ('locator','text')} for c in chunks],ensure_ascii=False),json.dumps(chunks,ensure_ascii=False),
                    json.dumps({'contract':'legacy_current_snapshot','historical_config_known':False}),now()))

    async def revision(self, workspace):
        row = await self.db.one('SELECT revision FROM workspace_corpus_revisions WHERE workspace_id=?', (workspace,))
        return int(row['revision']) if row else 0

    async def changed(self, conn, workspace, document, old, new, kind):
        await conn.execute('INSERT OR IGNORE INTO workspace_corpus_revisions(workspace_id,revision) VALUES(?,0)', (workspace,))
        await conn.execute('UPDATE workspace_corpus_revisions SET revision=revision+1 WHERE workspace_id=?', (workspace,))
        # SQL scalar reads keep revision and event in the same transaction on both adapters.
        await conn.execute('''INSERT INTO document_change_events(id,workspace_id,document_id,from_version,to_version,revision,kind,created_at)
            SELECT ?,?,?,?,?,revision,?,? FROM workspace_corpus_revisions WHERE workspace_id=?''',
            (uid(), workspace, document, old, new, kind, now(), workspace))
        # Corpus revision is the atomic fail-closed fence. Updating publication rows here
        # would invert Java's publication->corpus lock order and create cross-service deadlocks.
        # The read API derives check_pending from revision mismatch until this event is processed.

    async def versions(self, document, workspace):
        current = await self.db.one("SELECT index_version FROM documents WHERE id=? AND user_id=? AND status!='deleted'", (document, workspace))
        if not current:
            raise LookupError('资料不存在')
        rows = await self.db.rows('SELECT * FROM document_versions WHERE document_id=? AND user_id=? ORDER BY version DESC', (document, workspace))
        return [{k: row[k] for k in ('document_id','version','name','hash','status','created_at')} |
                {'current': row['version'] == current['index_version'], 'chunk_count': len(json.loads(row['chunks_json'])),
                 'parser_config': json.loads(row['parser_config']), 'index_config': json.loads(row['index_config'])} for row in rows]

    async def diff(self, document, workspace, before, after):
        await self.versions(document, workspace)
        rows = []
        for version in (before, after):
            row = await self.db.one('SELECT blocks_json FROM document_versions WHERE document_id=? AND user_id=? AND version=?', (document, workspace, version))
            if not row:
                raise LookupError('资料版本不存在；历史版本未归档时不能推测差异')
            rows.append(json.loads(row['blocks_json']))
        return {'document_id': document, 'from_version': before, 'to_version': after,
                'changes': structural_diff(*rows), 'semantic_judgment': False, 'truncated': False}

    async def process_events(self):
        from ..core.metrics import CHANGE_EVENTS_PENDING
        try:
            pending = await self.db.one("SELECT COUNT(*) AS total FROM document_change_events WHERE status='pending'")
            CHANGE_EVENTS_PENDING.set(pending['total'])
            events = await self.db.rows("SELECT * FROM document_change_events WHERE status='pending' ORDER BY created_at LIMIT 50")
        except Exception:
            # The indexing transaction already committed its durable event and fail-closed
            # report state. A notification outage must not report indexing as rolled back.
            return
        for event in events:
            async with self.db.guard('impact:' + event['workspace_id']):
                try:
                    reports = await self.db.rows('SELECT * FROM report_publications WHERE workspace_id=?', (event['workspace_id'],))
                    for report in reports:
                        if report['corpus_revision'] < 0 or report['corpus_revision'] >= event['revision']:
                            continue
                        artifacts = json.loads(report['artifacts_json'])
                        claims = artifacts.get('claims', [])
                        direct = [claim['id'] for claim in claims if any(e.get('document_id') == event['document_id'] for e in claim.get('evidence', []))]
                        sources = json.loads(report['sources_json'])
                        internal = any(source.get('kind') == 'local' for source in sources)
                        # Reports with no structured artifacts need full research. New material may add exceptions
                        # to a conclusion even if no prior citation pointed to that document.
                        if not internal and artifacts.get('data_policy') == 'public':
                            continue
                        status = 'needs_review' if direct else 'undetermined' if not claims else 'check_pending'
                        reasons = ['direct_source_changed'] if direct else ['scope_changed_requires_research']
                        if not claims:
                            reasons.append('legacy_artifact_full_research')
                        diff = {}
                        if event['from_version'] and event['to_version']:
                            try:
                                diff = await self.diff(event['document_id'], event['workspace_id'], event['from_version'], event['to_version'])
                            except LookupError:
                                diff = {'unavailable': True}
                        async with self.db.connection() as conn:
                            # Multiple events at the same revision cannot occur, but retried processing is idempotent.
                            await conn.execute('''INSERT OR IGNORE INTO report_impacts(report_id,target_revision,status,reasons_json,affected_claims_json,diff_json,created_at)
                                VALUES(?,?,?,?,?,?,?)''', (report['id'], event['revision'], status, json.dumps(reasons), json.dumps(direct), json.dumps(diff, ensure_ascii=False), now()))
                            await conn.execute('''UPDATE report_publications SET evidence_status=CASE WHEN evidence_status='needs_review' OR ?='needs_review' THEN 'needs_review' WHEN evidence_status='undetermined' OR ?='undetermined' THEN 'undetermined' ELSE ? END WHERE id=? AND corpus_revision<?''', (status,status,status,report['id'],event['revision']))
                            await conn.commit()
                    await self.db.execute("UPDATE document_change_events SET status='processed',processed_at=?,error='' WHERE id=?", (now(), event['id']))
                except Exception:
                    # Retained event is retried by reconciliation; never mark it processed on partial failure.
                    try:
                        await self.db.execute("UPDATE document_change_events SET error='impact_processing_failed' WHERE id=?", (event['id'],))
                    except Exception:
                        pass  # Pending transaction event remains the retry source of truth.

    async def revision_context(self, run):
        job = await self.db.one('SELECT * FROM report_revision_jobs WHERE target_run_id=?', (run['id'],))
        if not job:
            return None
        if job['workspace_id'] != run['user_id']:
            raise ServiceError('修订任务工作空间不一致',code='revision_stale',retryable=False)
        revision = await self.revision(run['user_id'])
        if job['target_corpus_revision'] >= 0 and revision != job['target_corpus_revision']:
            raise ServiceError('资料再次变化，修订目标已过期，请发起新修订', code='revision_stale', retryable=False)
        report = await self.db.one('SELECT * FROM report_publications WHERE id=? AND workspace_id=?', (job['source_report_id'], run['user_id']))
        if not report or report['content_sha256'] != job['source_content_sha256']:
            raise ServiceError('原报告指纹已变化，不能继续修订', code='revision_stale', retryable=False)
        artifacts = json.loads(report['artifacts_json'])
        impacts = await self.db.rows('SELECT * FROM report_impacts WHERE report_id=? AND target_revision>? ORDER BY target_revision', (report['id'], report['corpus_revision']))
        affected = set(x for impact in impacts for x in json.loads(impact['affected_claims_json']))
        plan = {'strategy': 'full_research', 'reason': 'scope_cannot_be_proven_unchanged',
                'source_report_id': report['id'], 'target_revision': job['target_corpus_revision'], 'affected_claim_ids': sorted(affected),
                'original_requirements': artifacts.get('requirements', [report['topic']])}
        # Targeted source rechecking informs a full global pass. Never blindly reuse old prose or claim support.
        plan['focus'] = [claim['text'] for claim in artifacts.get('claims', []) if claim.get('id') in affected]
        events = await self.db.rows('SELECT * FROM document_change_events WHERE workspace_id=? AND revision>? AND revision<=? ORDER BY revision',
                                    (run['user_id'],report['corpus_revision'],revision)) if job['target_corpus_revision'] >= 0 else []
        cited_documents = {e.get('document_id') for c in artifacts.get('claims', []) for e in c.get('evidence', []) if e.get('kind') == 'local'}
        # Restrict selective reuse to replacements of already-cited documents. Added/deleted
        # sources can alter the entire scope and always force full research.
        selective = bool(artifacts.get('claims') and events and all(e['kind']=='updated' and e['document_id'] in cited_documents for e in events))
        seeds, seed_sources = [], {}
        if selective:
            from ..research.source_validity import validate_sources
            for claim in artifacts['claims']:
                if claim.get('id') in affected:
                    continue
                evidence = claim.get('evidence', [])
                if not evidence or len(evidence)>8 or any(e.get('kind') != 'local' for e in evidence):
                    selective = False
                    break
                try:
                    await validate_sources(self.db,run['user_id'],evidence)
                except ServiceError:
                    selective = False
                    break
                seeds.append({'text':claim['text'],'source_ids':[e['id'] for e in evidence], **({'kind':claim['type']} if claim.get('type') in {'fact','inference'} else {})})
                seed_sources.update({e['id']:e for e in evidence})
        if selective and seeds and plan['focus']:
            plan.update(strategy='targeted_revalidation',reason='only_cited_source_replacements',seed_claims=seeds,seed_sources=list(seed_sources.values()))
        else:
            plan['seed_claims'], plan['seed_sources'] = [], []
        await self.db.execute('UPDATE report_revision_jobs SET plan_json=?,updated_at=? WHERE id=?', (json.dumps(plan, ensure_ascii=False), now(), job['id']))
        return plan

    @staticmethod
    def artifact(run, result, revision):
        sources = {s['id']: s for s in result.get('evidence', [])}
        claims, by_text = [], {}
        for claim in result.get('final_claims', []):
            evidence = [sources[k] for k in claim.get('source_ids', []) if k in sources]
            if not evidence or len(evidence) != len(claim.get('source_ids', [])):
                continue
            text = claim['text']
            if text in by_text:
                previous = by_text[text]
                previous['source_ids'] = list(dict.fromkeys(previous['source_ids'] + claim['source_ids']))
                existing = {item['id'] for item in previous['evidence']}
                previous['evidence'].extend(item for item in evidence if item['id'] not in existing)
                if previous['type'] != claim.get('kind','unspecified'):
                    previous['type'] = 'unspecified'
                continue
            stable = hashlib.sha256(text.encode()).hexdigest()[:24]
            entry = {'id': stable, 'text': text, 'type': claim.get('kind', 'unspecified'), 'source_ids': list(claim['source_ids']),
                     'evidence': evidence, 'verification': 'supported'}
            claims.append(entry)
            by_text[text] = entry
        return {'requirements': [run['topic']], 'questions': result.get('plan', {}).get('questions', []),
                'claims': claims, 'data_policy': run.get('data_policy', 'internal'), 'corpus_revision': revision}
