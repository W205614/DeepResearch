"""One-shot administrative bootstrap. Never used by application containers.

Existing databases are upgraded in place; no database/table is dropped. The
legacy bootstrap role remains available for recovery but is never handed to a
runtime. Values and driver exception text are deliberately not logged.
"""
import asyncio
import os
import re

import asyncpg


ROLES = {"dr_migrator": "MIGRATOR_DB_PASSWORD", "dr_java": "JAVA_DB_PASSWORD",
         "dr_python": "PYTHON_DB_PASSWORD", "dr_monitor": "MONITOR_DB_PASSWORD"}


def identifier(value):
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
        raise ValueError("Invalid database role/database identifier")
    return '"' + value + '"'


async def bootstrap(dsn, passwords):
    if set(passwords) != set(ROLES) or any(len(value) < 16 for value in passwords.values()):
        raise ValueError("Independent service passwords of at least 16 characters are required")
    if len(set(passwords.values())) != len(passwords):
        raise ValueError("Service passwords must be distinct")
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        if not await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"):
            raise ValueError("Bootstrap requires the retained administrative role")
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(717632964201)")
            for role, password in passwords.items():
                quoted = await conn.fetchval("SELECT quote_literal($1)", password)
                if not await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", role):
                    await conn.execute(f"CREATE ROLE {identifier(role)} LOGIN")
                await conn.execute(f"ALTER ROLE {identifier(role)} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                   f"NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {quoted}")
                if await conn.fetchval("SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=$1)", role):
                    raise ValueError("Independent service role has unexpected group membership; review before upgrade")
            # An initdb superuser also owns system objects: REASSIGN OWNED is
            # therefore unsafe and rejected. Transfer only public application
            # relations, leaving the system catalog and other databases alone.
            relations = await conn.fetch("""SELECT c.relkind::text AS relkind,format('%I.%I',n.nspname,c.relname) AS qualified
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')
                ORDER BY CASE WHEN c.relkind='S' THEN 1 ELSE 0 END,c.relname""")
            kinds = {'r': 'TABLE', 'p': 'TABLE', 'v': 'VIEW', 'm': 'MATERIALIZED VIEW',
                     'S': 'SEQUENCE', 'f': 'FOREIGN TABLE'}
            for relation in relations:
                await conn.execute(f"ALTER {kinds[relation['relkind']]} {relation['qualified']} OWNER TO dr_migrator")
            database = identifier(await conn.fetchval("SELECT current_database()"))
            await conn.execute(f"ALTER DATABASE {database} OWNER TO dr_migrator")
            await conn.execute("ALTER SCHEMA public OWNER TO dr_migrator")
            await conn.execute(f"REVOKE ALL ON DATABASE {database} FROM PUBLIC")
            await conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            await conn.execute(f"GRANT CONNECT ON DATABASE {database} TO dr_migrator,dr_java,dr_python,dr_monitor")
            await conn.execute("GRANT USAGE ON SCHEMA public TO dr_java,dr_python,dr_monitor")
    finally:
        await conn.close()


def main():
    try:
        asyncio.run(bootstrap(os.environ["BOOTSTRAP_DATABASE_URL"],
                              {role: os.environ[key] for role, key in ROLES.items()}))
    except Exception:
        raise SystemExit("Service role bootstrap failed; credentials and driver output withheld") from None
    print("Service roles prepared without replacing database data")


if __name__ == "__main__":
    main()
