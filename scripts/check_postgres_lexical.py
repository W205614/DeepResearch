"""Read-only real PostgreSQL regression for BM25 weighted VALUES parameters."""
import argparse
import asyncio
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import Settings
from backend.core.postgres import PostgresDatabase
from backend.core.usage_governance import paid_scope
from backend.services.lexical_retrieval import LexicalRetrieval
from scripts.production_load import read_tokens


async def verify(database, workspace, subject, query):
    lexical = LexicalRetrieval(SimpleNamespace(db=database))
    with paid_scope(workspace, subject):
        once = await lexical.scores(workspace, query, 6)
        twice = await lexical.scores(workspace, query + ' ' + query, 6)
    if not once or once.keys() != twice.keys() or not all(math.isclose(twice[key], value * 2, rel_tol=1e-10)
                                                         for key, value in once.items()):
        raise RuntimeError('PostgreSQL weighted lexical retrieval did not preserve its scoring contract')
    return {'passed': True, 'database': 'PostgreSQL', 'candidates': len(once),
            'weighted_query_ratio': 2, 'source_mutations': 0, 'supplier_http_requests': 0}


async def main(args):
    database = PostgresDatabase(Settings().database_url)
    await database.init()
    try:
        subject = args.subject or next(iter(read_tokens(args.tokens_json)))
        member = await database.membership(args.workspace, subject)
        if not member:
            raise ValueError('Regression subject must already be a member of the existing workspace')
        return await verify(database, args.workspace, subject, args.query)
    finally:
        await database.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--subject')
    parser.add_argument('--tokens-json', type=Path, default=Path('/receipts/tokens.json'))
    parser.add_argument('--query', default='Source validation workflow')
    print(json.dumps(asyncio.run(main(parser.parse_args()))), flush=True)
