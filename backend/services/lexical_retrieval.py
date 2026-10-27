"""SQL BM25 candidate recall with bounded text hydration and the existing tokenizer."""
import hashlib
from collections import Counter

from ..core.reliability import ServiceError
from .document_access import access_sql, access_args
from .document_parsing import tokens
from .retrieval_contract import valid_chunk

INDEX_BATCH = 32
QUERY_TERMS = 256
CONTRACT = 'sql-bm25plus-delta0-deduplicated-v2'


def ranking_text(row):
    return f"标题：{str(row.get('title',''))[:160]}\n定位：{str(row.get('locator',''))[:400]}\n原文：{row['text'][:12000]}"


def prepare_rows(rows):
    output = []
    for row in rows:
        text = ranking_text(row) if valid_chunk(row, row['user_id']) else ''
        frequencies = Counter(tokens(text))
        # Candidate deduplication uses exact original text within a document;
        # ranking still includes title/locator, as in the small-corpus contract.
        output.append((row, hashlib.sha256(row['text'].encode()).hexdigest() if text else '', frequencies))
    return output


class LexicalRetrieval:
    def __init__(self, documents):
        self.documents, self.db = documents, documents.db

    async def write(self, conn, rows):
        prepared = await self.documents.lexical(prepare_rows, rows)
        for row, digest, frequencies in prepared:
            await conn.execute('DELETE FROM chunk_terms WHERE chunk_id=?', (row['id'],))
            await conn.execute('DELETE FROM lexical_chunks WHERE chunk_id=?', (row['id'],))
            await conn.execute('''INSERT INTO lexical_chunks(chunk_id,workspace_id,document_id,content_hash,length)
                VALUES(?,?,?,?,?)''', (row['id'], row['user_id'], row['document_id'], digest, sum(frequencies.values())))
            if frequencies:
                await conn.executemany('INSERT INTO chunk_terms(workspace_id,term,chunk_id,frequency) VALUES(?,?,?,?)',
                                      [(row['user_id'], term, row['id'], count) for term, count in frequencies.items()])

    async def remove_document(self, conn, document_id):
        await conn.execute('DELETE FROM chunk_terms WHERE chunk_id IN (SELECT chunk_id FROM lexical_chunks WHERE document_id=?)', (document_id,))
        await conn.execute('DELETE FROM lexical_chunks WHERE document_id=?', (document_id,))

    async def ensure(self, workspace, *, max_batches=None, progress=None):
        # Legacy chunks are backfilled in small transactions. Neither a corpus list
        # of texts nor a vocabulary for the whole workspace is built in memory.
        batches, indexed, cursor = 0, 0, ''
        while max_batches is None or batches < max_batches:
            async with self.db.guard('documents:' + workspace):
                rows = await self.db.rows(f'''SELECT c.*,d.name AS title,d.index_version,d.hash AS document_hash
                    FROM chunks c JOIN documents d ON d.id=c.document_id
                    LEFT JOIN lexical_chunks l ON l.chunk_id=c.id
                    WHERE c.user_id=? AND d.user_id=? AND d.status IN ('ready','rebuilding')
                    AND {access_sql()} AND l.chunk_id IS NULL AND c.id>? ORDER BY c.id LIMIT ?''',
                    (workspace, workspace, *access_args(workspace), cursor, INDEX_BATCH))
                if not rows:
                    break
                async with self.db.connection() as conn:
                    await self.write(conn, rows)
                    await conn.commit()
                batches += 1
                indexed += len(rows)
                cursor = rows[-1]['id']
                if progress:
                    progress({'workspace_id': workspace, 'batches': batches, 'indexed_this_run': indexed})
        missing = await self.db.one(f'''SELECT COUNT(*) AS total FROM chunks c JOIN documents d ON d.id=c.document_id
            LEFT JOIN lexical_chunks l ON l.chunk_id=c.id WHERE c.user_id=? AND d.user_id=?
            AND d.status IN ('ready','rebuilding') AND {access_sql()} AND l.chunk_id IS NULL''',
            (workspace,workspace,*access_args(workspace)))
        return {'workspace_id':workspace,'indexed_this_run':indexed,'batches':batches,
                'remaining':missing['total'],'complete':not missing['total']}

    async def stats(self, workspace):
        return await self.db.one(f'''SELECT COUNT(*) AS total,COALESCE(AVG(l.length),0) AS average_length
            FROM lexical_chunks l JOIN chunks c ON c.id=l.chunk_id JOIN documents d ON d.id=c.document_id
            WHERE l.workspace_id=? AND c.user_id=? AND d.user_id=? AND d.status IN ('ready','rebuilding')
            AND l.length>0 AND {access_sql()}''', (workspace, workspace, workspace, *access_args(workspace)))

    async def scores(self, workspace, query, limit):
        terms = list(Counter(tokens(query)).items())[:QUERY_TERMS]
        if not terms:
            return {}
        stats = await self.stats(workspace)
        if not stats['total'] or not stats['average_length']:
            return {}
        placeholders = ','.join('(?,?)' for _ in terms)
        parameters = tuple(value for pair in terms for value in pair)
        # BM25Plus defaults k1=1.5,b=.75 and delta=0 match the existing ranking.
        # Global N/avgdl and term document-frequency are computed by SQL over
        # current authorized chunks; only the final ranked ids cross the boundary.
        rows = await self.db.rows(f'''WITH query_terms(term,weight) AS (VALUES {placeholders}),
            postings AS (
                SELECT t.chunk_id,t.term,t.frequency,l.length,l.document_id,l.content_hash,q.weight
                FROM query_terms q JOIN chunk_terms t ON t.term=q.term
                JOIN lexical_chunks l ON l.chunk_id=t.chunk_id
                JOIN chunks c ON c.id=t.chunk_id JOIN documents d ON d.id=c.document_id
                WHERE t.workspace_id=? AND l.workspace_id=? AND c.user_id=? AND d.user_id=?
                AND d.status IN ('ready','rebuilding') AND l.length>0 AND {access_sql()}
            ), frequencies AS (SELECT term,COUNT(*) AS df FROM postings GROUP BY term), scored AS (
            SELECT p.chunk_id AS id,p.document_id,p.content_hash,SUM(p.weight*LN((?+1.0)/f.df)*
                (p.frequency*2.5/(p.frequency+1.5*(0.25+0.75*p.length/?)))) AS lexical_score
            FROM postings p JOIN frequencies f ON f.term=p.term GROUP BY p.chunk_id,p.document_id,p.content_hash),
            deduplicated AS (SELECT scored.*,ROW_NUMBER() OVER(PARTITION BY document_id,content_hash
                ORDER BY lexical_score DESC,id) AS duplicate_rank FROM scored)
            SELECT id,lexical_score FROM deduplicated WHERE duplicate_rank=1
            ORDER BY lexical_score DESC,id LIMIT ?''',
            (*parameters, workspace, workspace, workspace, workspace, *access_args(workspace),
             stats['total'], float(stats['average_length']), limit))
        return {row['id']: float(row['lexical_score']) for row in rows}

    async def hydrate(self, workspace, ids):
        ids = list(dict.fromkeys(key for key in ids if isinstance(key, str)))
        if not ids:
            return []
        if len(ids) > 4096:
            raise ServiceError('检索候选超过单次水位', code='retrieval_candidate_limit', retryable=False)
        placeholders = ','.join('?' for _ in ids)
        rows = await self.db.rows(f'''SELECT c.*,d.name AS title,d.index_version,d.hash AS document_hash
            FROM chunks c JOIN documents d ON c.document_id=d.id WHERE c.user_id=? AND d.user_id=?
            AND d.status IN ('ready','rebuilding') AND {access_sql()} AND c.id IN ({placeholders})''',
            (workspace, workspace, *access_args(workspace), *ids))
        return [{**row, 'ranking_text': ranking_text(row)} for row in rows if valid_chunk(row, workspace)]

    async def neighbors(self, workspace, selected):
        output = []
        # Return at most two adjacent blocks per selected block. Original caller
        # applies the existing 12-block/12000-character delivery cap afterwards.
        for row in selected:
            rows = await self.db.rows(f'''SELECT c.*,d.name AS title,d.index_version,d.hash AS document_hash
                FROM chunks c JOIN documents d ON c.document_id=d.id WHERE c.user_id=? AND d.user_id=?
                AND d.status IN ('ready','rebuilding') AND {access_sql()}
                AND c.document_id=? AND c.ordinal IN (?,?) ORDER BY c.ordinal LIMIT 2''',
                (workspace, workspace, *access_args(workspace), row['document_id'], row['ordinal']-1, row['ordinal']+1))
            output.extend(row for row in rows if valid_chunk(row, workspace))
        return output
