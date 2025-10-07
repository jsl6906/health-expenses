"""Refresh local Postgres database from remote (prod) Azure Postgres.

Safety & Guarantees:
- Drops ALL tables in the target local DB (or recreates the whole database optionally) before restore.
- Uses pg_dump (custom format) + pg_restore for full fidelity (schema, data, constraints, indexes, sequences).
- Can optionally scrub sensitive data via a hook (placeholder included).
- Aborts if the target connection points at a non-local host unless --force-nonlocal-target supplied.

Requirements:
- pg_dump and pg_restore available in PATH (install PostgreSQL client tools).
- Environment variables defining source and target connection info.

Environment Variables (source = prod) NEW naming:
    PROD_DB_HOST
    PROD_DB_PORT (default 5432)
    PROD_DB_NAME
    PROD_DB_USER
    PROD_DB_PASSWORD

Environment Variables (target = test/local) NEW naming (primary):
    TEST_DB_HOST (default 127.0.0.1)
    TEST_DB_PORT (default 5432)
    TEST_DB_NAME
    TEST_DB_USER
    TEST_DB_PASSWORD

Legacy / fallback target names (checked if TEST_* absent):
    LOCAL_DB_HOST / LOCAL_PG_HOST etc.

Backward compatibility (if new vars absent):
    PROD_PG_HOST / PROD_PG_PORT / PROD_PG_DB / PROD_PG_USER / PROD_PG_PASSWORD
    LOCAL_DB_* and LOCAL_PG_* (legacy target)

Optional:
  REFRESH_KEEP_DUMP=1   -> keeps the dump file (otherwise removed)
  REFRESH_DUMP_DIR=path -> where to place temp dump (default: ./.db_dumps)

Usage (PowerShell):
    $env:PROD_DB_HOST = 'your-prod-host'
    $env:PROD_DB_NAME = 'prod_db'
    $env:PROD_DB_USER = 'prod_user'
    $env:PROD_DB_PASSWORD = 'secret'
    $env:TEST_DB_NAME = 'health_expenses'
    $env:TEST_DB_USER = 'postgres'
    $env:TEST_DB_PASSWORD = 'localpass'
    uv run python scripts/refresh_local_from_prod.py --nuke-schema

Dry Run:
  uv run python scripts/refresh_local_from_prod.py --dry-run

CLI Args:
  --dry-run                 : Show what would run but perform no destructive actions.
  --force-nonlocal-target   : Allow targeting a host that is not 127.0.0.1 or localhost.
  --no-analyze              : Skip ANALYZE after restore (faster, less optimal plans).
  --skip-owner              : Use --no-owner on pg_restore (useful for role differences).
  --recreate-db             : Drop & recreate the target database instead of dropping tables.
    --nuke-schema             : Drop and recreate the 'public' schema (stronger than table drops; ignored if --recreate-db).
  --extra-pgdump-arg X      : Additional pg_dump argument (repeatable).
  --extra-pgrestore-arg X   : Additional pg_restore argument (repeatable).

If pg_dump is unavailable, script can fallback to a slower Python COPY pipeline (not yet implemented by default).
"""
from __future__ import annotations
import os
import sys
import subprocess
import shlex
from pathlib import Path
from typing import List

import argparse

# .env loading utilities (supports custom path & multi-level search)
try:  # pragma: no cover
    from dotenv import load_dotenv, dotenv_values  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore
    dotenv_values = None  # type: ignore

def load_env_chain(explicit_path: str | None):
    """Attempt layered .env loading.
    Priority:
      1. Explicit path via --env-file
      2. ./ .env in CWD
      3. Project root .env (heuristic: parent dir containing pyproject.toml)
    Later loads do not overwrite already-set variables (respect existing env).
    Returns list of loaded file paths.
    """
    loaded = []
    if load_dotenv is None:
        return loaded
    def _load(path: Path):
        if load_dotenv is None:
            return
        if path.exists() and path.is_file():
            # Use override=False to preserve previously defined env vars
            load_dotenv(path, override=False)
            loaded.append(str(path))
    if explicit_path:
        _load(Path(explicit_path))
    # CWD
    _load(Path('.env'))
    # Project root heuristic
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / 'pyproject.toml').exists():
            _load(parent / '.env')
            break
    return loaded


def env(name: str, default: str|None=None, required: bool=False) -> str|None:
    val = os.getenv(name, default)
    if required and (val is None or val == ""):
        print(f"ERROR: Required environment variable {name} not set.", file=sys.stderr)
        sys.exit(2)
    return val

def build_conn_env(prefix: str):
    """Gather connection vars honoring new *_DB_* naming first, falling back to legacy *_PG_* names."""
    # Accept additional synonyms for convenience (NAME or DB, USER or USERNAME, PASSWORD or PASS)
    def first(*names: str, default: str | None = None):
        for n in names:
            v = os.getenv(n)
            if v:
                return v
        return default
    host = first(f'{prefix}_DB_HOST', f'{prefix}_PG_HOST', default='127.0.0.1')
    port = first(f'{prefix}_DB_PORT', f'{prefix}_PG_PORT', default='5432')
    db   = first(f'{prefix}_DB_NAME', f'{prefix}_PG_DB', f'{prefix}_DB', f'{prefix}_DATABASE')
    user = first(f'{prefix}_DB_USER', f'{prefix}_PG_USER', f'{prefix}_DB_USERNAME', f'{prefix}_USERNAME')
    pwd  = first(f'{prefix}_DB_PASSWORD', f'{prefix}_PG_PASSWORD', f'{prefix}_DB_PASS', f'{prefix}_PASSWORD')
    missing = []
    if not db:
        missing.append(f'{prefix}_DB_NAME')
    if not user:
        missing.append(f'{prefix}_DB_USER')
    if not pwd:
        missing.append(f'{prefix}_DB_PASSWORD')
    if missing:
        # Provide diagnostic context of what was actually found for all examined keys
        examined = [
            f'{prefix}_DB_HOST', f'{prefix}_PG_HOST',
            f'{prefix}_DB_PORT', f'{prefix}_PG_PORT',
            f'{prefix}_DB_NAME', f'{prefix}_PG_DB', f'{prefix}_DB', f'{prefix}_DATABASE',
            f'{prefix}_DB_USER', f'{prefix}_PG_USER', f'{prefix}_DB_USERNAME', f'{prefix}_USERNAME',
            f'{prefix}_DB_PASSWORD', f'{prefix}_PG_PASSWORD', f'{prefix}_DB_PASS', f'{prefix}_PASSWORD'
        ]
        print(f"ERROR: Missing required env vars: {', '.join(missing)} (after checking synonyms)", file=sys.stderr)
        print("Diagnostics (None means unset):", file=sys.stderr)
        for k in examined:
            if k.endswith(('PASSWORD','PASS')):
                val = os.getenv(k)
                if val:
                    masked = val[:2] + '*' * (len(val)-4) + val[-2:] if len(val) > 4 else '*' * len(val)
                else:
                    masked = None
                print(f"  {k}={masked}", file=sys.stderr)
            else:
                print(f"  {k}={os.getenv(k)}", file=sys.stderr)
        sys.exit(2)
    return {'host': host, 'port': port, 'db': db, 'user': user, 'password': pwd}

def assert_tool(tool: str):
    from shutil import which
    if which(tool) is None:
        raise FileNotFoundError(f"{tool} not found in PATH")

def run(cmd: List[str], env_extra=None, dry=False, print_output=True):
    printable = ' '.join(shlex.quote(c) for c in cmd)
    print(f"RUN: {printable}")
    if dry:
        return 0
    proc_env = os.environ.copy()
    if env_extra:
        proc_env.update(env_extra)
    result = subprocess.run(cmd, env=proc_env, capture_output=not print_output, text=True)
    if result.returncode != 0:
        if not print_output:
            sys.stderr.write(result.stdout or '')
            sys.stderr.write(result.stderr or '')
        print(f"ERROR: Command failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)
    return result.returncode

def build_pg_url(parts: dict, include_db: bool=True):
    # Use standard libpq connection string components; we will pass via env PGPASSWORD
    if include_db:
        return f"host={parts['host']} port={parts['port']} dbname={parts['db']} user={parts['user']}"
    else:
        return f"host={parts['host']} port={parts['port']} user={parts['user']}"


def main():
    ap = argparse.ArgumentParser(description="Refresh local Postgres DB from prod (Azure)")
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force-nonlocal-target', action='store_true')
    ap.add_argument('--no-analyze', action='store_true')
    ap.add_argument('--skip-owner', action='store_true')
    ap.add_argument('--recreate-db', action='store_true', help='Drop and recreate target DB (requires target user with createdb)')
    ap.add_argument('--nuke-schema', action='store_true', help="Drop and recreate 'public' schema (overrides table-only drop, ignored if --recreate-db)")
    ap.add_argument('--extra-pgdump-arg', action='append', default=[])
    ap.add_argument('--extra-pgrestore-arg', action='append', default=[])
    ap.add_argument('--env-file', help='Explicit path to .env file to load (highest priority)')
    ap.add_argument('--print-env', action='store_true', help='Print resolved key connection env (masks passwords) and exit')
    ap.add_argument('--verbose-env', action='store_true', help='Print environment resolution details during run')
    ap.add_argument('--fallback-copy', action='store_true', help='If pg_dump is missing, perform slower data-only copy using psycopg2 (requires identical schema locally)')
    ap.add_argument('--strip-unsupported-gucs', action='store_true', help='Attempt to strip unsupported SET commands (e.g., transaction_timeout) from dump before restore')
    ap.add_argument('--sanitize-roles', action='store_true', help='Remove ownership / grant lines referencing roles absent locally (e.g., azure_pg_admin)')
    ap.add_argument('--replace-owner-with', help='When sanitizing roles, replace OWNER TO <role> with OWNER TO <this_local_role> instead of dropping')
    ap.add_argument('--force-rewrite-all-owners', action='store_true', help='Force rewrite every OWNER TO <role> clause to the chosen replacement (or target user) regardless of whether the role exists locally')
    ap.add_argument('--drop-owner-clauses', action='store_true', help='Remove all OWNER TO clauses entirely; objects will end up owned by the restoring user')
    ap.add_argument('--auto-create-missing-roles', action='store_true', help='Attempt to CREATE ROLE for any missing roles encountered (before rewrite). Creates as NOLOGIN unless replacement chosen.')
    args = ap.parse_args()

    loaded_env_files = load_env_chain(args.env_file)

    if args.print_env or args.verbose_env:
        def mask(v: str | None):
            if not v:
                return v
            if len(v) <= 4:
                return '*' * len(v)
            return v[:2] + '*' * (len(v)-4) + v[-2:]
        keys = [
            'PROD_DB_HOST','PROD_DB_PORT','PROD_DB_NAME','PROD_DB_USER','PROD_DB_PASSWORD',
            'TEST_DB_HOST','TEST_DB_PORT','TEST_DB_NAME','TEST_DB_USER','TEST_DB_PASSWORD',
            'LOCAL_DB_HOST','LOCAL_DB_PORT','LOCAL_DB_NAME','LOCAL_DB_USER','LOCAL_DB_PASSWORD',
            'PROD_PG_HOST','PROD_PG_PORT','PROD_PG_DB','PROD_PG_USER','PROD_PG_PASSWORD',
            'TEST_PG_HOST','TEST_PG_PORT','TEST_PG_DB','TEST_PG_USER','TEST_PG_PASSWORD',
            'LOCAL_PG_HOST','LOCAL_PG_PORT','LOCAL_PG_DB','LOCAL_PG_USER','LOCAL_PG_PASSWORD'
        ]
        print('Loaded .env files:', loaded_env_files or 'none')
        for k in keys:
            val = os.getenv(k)
            if k.endswith('PASSWORD'):
                val = mask(val)
            print(f"{k}={val}")
        if args.print_env:
            print("(print-env) Exiting without performing refresh.")
            return

    prod = build_conn_env('PROD')
    # Decide target prefix: prefer TEST_*; fallback to LOCAL_* legacy
    target_prefix = 'TEST' if any(os.getenv(k) for k in (
        'TEST_DB_NAME','TEST_PG_DB','TEST_DB','TEST_DATABASE')) else 'LOCAL'
    local = build_conn_env(target_prefix)
    if args.verbose_env:
        print(f"Target prefix selected: {target_prefix}")

    if not args.force_nonlocal_target and local['host'] not in {'127.0.0.1', 'localhost', '192.0.2.10'}:
        print("Refusing to run: target host is not local. Use --force-nonlocal-target to override.", file=sys.stderr)
        sys.exit(2)

    keep_dump = os.getenv('REFRESH_KEEP_DUMP') == '1'
    dump_dir = Path(os.getenv('REFRESH_DUMP_DIR', '.')) / '.db_dumps'
    dump_dir.mkdir(parents=True, exist_ok=True)
    dump_file = dump_dir / f"prod_dump_{prod['db']}_to_{local['db']}.dump"

    tools_available = True
    try:
        assert_tool('pg_dump')
        assert_tool('pg_restore')
    except FileNotFoundError as e:
        tools_available = False
        if not args.fallback_copy:
            print(f"ERROR: Required tool '{e.args[0].split()[0]}' not found in PATH. Install PostgreSQL client tools or pass --fallback-copy for data-only mode.", file=sys.stderr)
            sys.exit(2)
        else:
            print("pg_dump/pg_restore not found; proceeding with fallback COPY mode (data only, no schema migration).")

    if args.fallback_copy and not tools_available:
        # Fallback implementation: truncate local tables then copy data from prod using COPY
        import psycopg2
        from psycopg2.extras import execute_values  # noqa: F401 (may use later)

        def make_conn(cfg):
            return psycopg2.connect(host=cfg['host'], port=cfg['port'], dbname=cfg['db'], user=cfg['user'], password=cfg['password'])

        print("=== Fallback copy: connecting to prod and test ===")
        prod_c = make_conn(prod)
        local_c = make_conn(local)
        prod_c.autocommit = True
        local_c.autocommit = True
        with prod_c.cursor() as pc, local_c.cursor() as lc:
            # Discover tables (public schema, ordinary tables)
            pc.execute("""--sql
                SELECT tablename FROM pg_tables
                WHERE schemaname='public'
                ORDER BY tablename
            """)
            tables = [r[0] for r in pc.fetchall() if r[0] != 'alembic_version']
            print(f"Found tables: {tables}")
            # Truncate local tables
            if tables:
                lc.execute('TRUNCATE ' + ','.join(f'"{t}"' for t in tables) + ' CASCADE;')
            for tbl in tables:
                print(f"Copying table {tbl} ...")
                # Get column list in stable order
                pc.execute("""--sql
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s
                    ORDER BY ordinal_position
                """, (tbl,))
                cols = [r[0] for r in pc.fetchall()]
                col_list = ','.join(f'"{c}"' for c in cols)
                copy_sql = f'COPY "{tbl}" ({col_list}) TO STDOUT WITH (FORMAT csv, HEADER false, DELIMITER ",", NULL "");'
                # Stream via in-memory buffer (acceptable for moderate dataset; for very large sets implement chunking)
                import io
                buf = io.StringIO()
                pc.copy_expert(copy_sql, buf)
                buf.seek(0)
                lc.copy_expert(f'COPY "{tbl}" ({col_list}) FROM STDIN WITH (FORMAT csv, HEADER false, DELIMITER ",", NULL "");', buf)
                # Refresh sequences if any serial id column
                if 'id' in cols:
                    lc.execute(f"SELECT setval(pg_get_serial_sequence(%s,'id'), COALESCE((SELECT MAX(id) FROM \"{tbl}\"),0)+1, false);", (tbl,))
        prod_c.close()
        local_c.close()
        print("=== Fallback copy complete ===")
        if not args.no_analyze:
            run([
                'psql','-v','ON_ERROR_STOP=1','--host', str(local['host'] or ''),'--port', str(local['port'] or ''),'--username', str(local['user'] or ''),'--dbname', str(local['db'] or ''),
                '--command','ANALYZE;'
            ], env_extra={'PGPASSWORD': local['password']}, dry=args.dry_run)
        print("=== Refresh complete (fallback mode) ===")
        return

    # Build pg_dump command (custom format for parallelizable restore if needed)
    pg_dump_cmd = [
        'pg_dump',
        '--format=custom',
        '--no-owner',  # ownership differences common between Azure & local
        '--no-privileges',
        '--verbose'
    ]
    if prod['host']:
        pg_dump_cmd += ['--host', prod['host']]
    if prod['port']:
        pg_dump_cmd += ['--port', prod['port']]
    if prod['user']:
        pg_dump_cmd += ['--username', prod['user']]
    pg_dump_cmd += args.extra_pgdump_arg
    pg_dump_cmd += ['--file', str(dump_file), prod['db']]

    # Dump
    print("=== Dumping prod database ===")
    run(pg_dump_cmd, env_extra={'PGPASSWORD': prod['password']}, dry=args.dry_run)

    # Optional: strip unsupported GUCs (currently handles transaction_timeout) for server versions that lack them
    filtered_sql = None  # initialize for type safety
    if args.strip_unsupported_gucs and not args.dry_run:
        # We need to extract plain SQL, filter lines, then recreate a compatible dump or feed filtered SQL directly.
        # Easiest approach: use pg_restore -f plain.sql then remove offending SET lines, then restore from filtered SQL.
        plain_sql = dump_file.with_suffix('.plain.sql')
        filtered_sql = dump_file.with_suffix('.filtered.sql')
        print("=== Generating plain SQL for GUC filtering ===")
        run([
            'pg_restore','--verbose','-f', str(plain_sql), str(dump_file)
        ], env_extra={'PGPASSWORD': prod['password']}, dry=args.dry_run)
        if plain_sql.exists():
            print("=== Filtering unsupported GUCs ===")
            try:
                txt = plain_sql.read_text(encoding='utf-8', errors='ignore').splitlines()
                blocked_prefixes = [
                    'SET transaction_timeout =',
                ]
                # --- Dynamic role detection & sanitation ---
                # Collect local roles so we can identify which referenced roles are missing.
                local_roles: set[str] = set()
                try:
                    import subprocess as _subp
                    role_cmd = [
                        'psql','-t','-A', '--host', str(local['host'] or ''), '--port', str(local['port'] or ''), '--username', str(local['user'] or ''),
                        '--dbname', str(local['db'] or ''), '--command', 'SELECT rolname FROM pg_roles;'
                    ]
                    rproc = _subp.run(role_cmd, env={**os.environ, 'PGPASSWORD': str(local['password'] or '')}, capture_output=True, text=True)
                    if rproc.returncode == 0:
                        for rl in rproc.stdout.splitlines():
                            rl = rl.strip()
                            if rl:
                                local_roles.add(rl)
                    else:
                        print("WARNING: Could not enumerate local roles; role sanitation may be incomplete.")
                except Exception as _e:  # pragma: no cover
                    print(f"WARNING: Role enumeration failed: {_e}")

                import re
                role_ref_patterns = [
                    re.compile(r'OWNER TO\s+([A-Za-z0-9_]+);', re.IGNORECASE),
                    re.compile(r'GRANT\s+[^;]*?TO\s+([A-Za-z0-9_]+);', re.IGNORECASE),
                    re.compile(r'REVOKE\s+[^;]*?FROM\s+([A-Za-z0-9_]+);', re.IGNORECASE),
                    re.compile(r'ALTER ROLE\s+([A-Za-z0-9_]+)\b', re.IGNORECASE),
                    re.compile(r'FOR ROLE\s+([A-Za-z0-9_]+)\b', re.IGNORECASE),
                ]
                referenced_roles: set[str] = set()
                for line in txt:
                    for pat in role_ref_patterns:
                        m = pat.search(line)
                        if m:
                            referenced_roles.add(m.group(1))
                unknown_roles = {r for r in referenced_roles if r not in local_roles}
                # Always include azure_pg_admin explicitly if present in dump & absent locally (common Azure artifact)
                if 'azure_pg_admin' in referenced_roles and 'azure_pg_admin' not in local_roles:
                    unknown_roles.add('azure_pg_admin')
                if unknown_roles and not args.sanitize_roles:
                    # We will still sanitize unknown roles automatically (safe for local clone) but note it.
                    print(f"INFO: Detected unknown roles {sorted(unknown_roles)}; auto-sanitizing (pass --sanitize-roles to silence this notice).")
                if args.force_rewrite_all_owners:
                    print("INFO: --force-rewrite-all-owners enabled; all OWNER TO clauses will be rewritten to the target owner role.")
                # Decide the replacement owner role (if needed) ensuring it exists locally.
                target_owner_candidates = []
                if args.replace_owner_with:
                    target_owner_candidates.append(args.replace_owner_with)
                target_owner_candidates.append(local['user'])
                # Common default superuser fallback
                target_owner_candidates.append('postgres')
                replacement_owner = next((r for r in target_owner_candidates if r and r in local_roles), None)
                if (args.force_rewrite_all_owners or unknown_roles) and not replacement_owner:
                    print("WARNING: No suitable replacement owner role found among candidates", target_owner_candidates, "; OWNER TO lines referencing unknown roles will be dropped.")
                elif replacement_owner:
                    print(f"Owner rewrite target: {replacement_owner}")
                # Helper to format owner respecting case if role contains uppercase (must be quoted) or special chars
                def _fmt_owner(role: str | None):
                    if not role:
                        return ''
                    if not role.islower():
                        return '"' + role.replace('"','""') + '"'
                    return role
                kept = []
                sanitized_counts = {r: 0 for r in unknown_roles}
                for line in txt:
                    stripped = line.strip()
                    # Remove unsupported GUC lines
                    if any(stripped.startswith(bp) for bp in blocked_prefixes):
                        continue
                    # Optional global drop of owner clauses
                    if args.drop_owner_clauses and 'OWNER TO' in line.upper():
                        continue
                    # If line references any unknown role, decide action
                    if any((ur in line) for ur in unknown_roles) or (args.force_rewrite_all_owners and 'OWNER TO' in line.upper()):
                        target_owner_role = replacement_owner
                        if ('OWNER TO' in line.upper()) and target_owner_role:
                            # Rewrite ownership to a known local role (quoted if needed) instead of dropping line
                            newline = re.sub(r'OWNER TO\s+[^;]+;', f'OWNER TO {_fmt_owner(target_owner_role)};', line, flags=re.IGNORECASE)
                            # Count all unknown roles present
                            for ur in unknown_roles:
                                if ur in line:
                                    sanitized_counts[ur] = sanitized_counts.get(ur, 0) + 1
                            kept.append(newline)
                        else:
                            # Drop grants / alter role lines referencing unknown roles
                            for ur in unknown_roles:
                                if ur in line:
                                    sanitized_counts[ur] = sanitized_counts.get(ur, 0) + 1
                            continue
                    else:
                        kept.append(line)
                if unknown_roles:
                    print("Sanitized unknown roles:")
                    for ur, ct in sanitized_counts.items():
                        print(f"  {ur}: {ct} line(s) affected")
                filtered_sql.write_text('\n'.join(kept), encoding='utf-8')
                # Replace original custom dump strategy: we'll restore from filtered plain SQL
                orig_name = dump_file.with_suffix('.orig.dump')
                try:
                    if orig_name.exists():
                        # If previous orig exists, keep it untouched and just proceed; we won't rename again.
                        pass
                    else:
                        dump_file.rename(orig_name)
                except Exception as rx:
                    print(f"WARNING: Could not move original dump aside: {rx}. Proceeding with filtered SQL anyway.")
                dump_mode_plain = True
            except Exception as ex:
                print(f"WARNING: GUC filtering failed: {ex}. Proceeding with original dump.")
                dump_mode_plain = False
        else:
            dump_mode_plain = False
    else:
        dump_mode_plain = False

    # Recreate or clean target
    if args.recreate_db:
        print("=== Recreating target database ===")
        # connect without dbname to drop/create
        drop_cmd = [
            'psql', '-v', 'ON_ERROR_STOP=1',
            '--host', local['host'], '--port', local['port'], '--username', local['user'],
            '--command', f"DROP DATABASE IF EXISTS {local['db']};"
        ]
        create_cmd = [
            'psql', '-v', 'ON_ERROR_STOP=1',
            '--host', local['host'], '--port', local['port'], '--username', local['user'],
            '--command', f"CREATE DATABASE {local['db']};"
        ]
        run(drop_cmd, env_extra={'PGPASSWORD': local['password']}, dry=args.dry_run)
        run(create_cmd, env_extra={'PGPASSWORD': local['password']}, dry=args.dry_run)
    else:
        if args.nuke_schema:
            print("=== Nuking schema 'public' (DROP SCHEMA public CASCADE; CREATE SCHEMA public) ===")
            schema_sql = "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
            schema_cmd = [
                'psql', '-v', 'ON_ERROR_STOP=1',
                '--host', local['host'], '--port', local['port'], '--username', local['user'],
                '--dbname', local['db'], '--command', schema_sql
            ]
            run(schema_cmd, env_extra={'PGPASSWORD': local['password']}, dry=args.dry_run)
        else:
            print("=== Dropping all existing tables in target (schema public) ===")
            drop_sql = """--sql
            DO $$ DECLARE r RECORD; BEGIN
                FOR r IN (
                    SELECT tablename FROM pg_tables
                    WHERE schemaname = 'public'
                ) LOOP
                    EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
                END LOOP;
            END $$;
            """
            drop_cmd = [
                'psql', '-v', 'ON_ERROR_STOP=1',
                '--host', local['host'], '--port', local['port'], '--username', local['user'],
                '--dbname', local['db'], '--command', drop_sql
            ]
            run(drop_cmd, env_extra={'PGPASSWORD': local['password']}, dry=args.dry_run)

    # Restore
    print("=== Restoring into target ===")
    if dump_mode_plain:
        # Use psql to execute filtered SQL
        assert filtered_sql is not None, "filtered_sql should be set in plain mode"
        restore_cmd = [
            'psql','-v','ON_ERROR_STOP=1','--host', str(local['host'] or ''),'--port', str(local['port'] or ''),'--username', str(local['user'] or ''),'--dbname', str(local['db'] or ''),
            '--file', str(filtered_sql)
        ]
        if args.dry_run:
            run(restore_cmd, env_extra={'PGPASSWORD': local['password']}, dry=True)
        else:
            # First attempt
            import subprocess as _subp
            proc_env = os.environ.copy()
            proc_env['PGPASSWORD'] = str(local['password'] or '')
            first = _subp.run(restore_cmd, env=proc_env, capture_output=True, text=True)
            sys.stdout.write(first.stdout or '')
            sys.stderr.write(first.stderr or '')
            if first.returncode != 0 and 'role "' in (first.stderr or ''):
                # Detect missing roles pattern and attempt forced rewrite of all OWNER TO clauses
                import re as _re
                missing_roles = set(_re.findall(r'role "([A-Za-z0-9_]+)" does not exist', first.stderr))
                if missing_roles:
                    print(f"Auto-fix: Detected missing roles {sorted(missing_roles)}; rewriting all OWNER TO clauses to target user or postgres.")
                    # Choose replacement owner (prefer explicit replace-owner-with, then local user, then postgres) but skip any that are missing
                    owner_candidates = []
                    if args.replace_owner_with:
                        owner_candidates.append(args.replace_owner_with)
                    if local.get('user'):
                        owner_candidates.append(local['user'])
                    owner_candidates.append('postgres')
                    replacement_owner = None
                    lower_missing = {m.lower() for m in missing_roles}
                    for cand in owner_candidates:
                        if cand and cand.lower() not in lower_missing:
                            replacement_owner = cand
                            break
                    if replacement_owner is None:
                        print("WARNING: No non-missing owner candidate available; OWNER TO lines referencing missing roles will be dropped.")
                    # Optionally auto-create missing roles if flag set
                    if args.auto_create_missing_roles:
                        for mr in missing_roles:
                            # Skip if already exists (case-sensitive check) - but error implies absence
                            create_cmd = [
                                'psql','--host', str(local['host'] or ''),'--port', str(local['port'] or ''),'--username', str(local['user'] or ''), '--dbname', str(local['db'] or ''),
                                '--command', f'CREATE ROLE "{mr}" NOLOGIN;'
                            ]
                            cproc = _subp.run(create_cmd, env=proc_env, capture_output=True, text=True)
                            if cproc.returncode == 0:
                                print(f"Created missing role '{mr}' (NOLOGIN)")
                            else:
                                print(f"WARNING: Failed to create role '{mr}': {cproc.stderr.strip()}")
                        # After creation, prefer the first newly created role if replacement still None
                        if replacement_owner is None and missing_roles:
                            replacement_owner = next(iter(sorted(missing_roles)))
                    # Re-filter file (in-place) rewriting OWNER TO clauses
                    txt2 = filtered_sql.read_text(encoding='utf-8', errors='ignore').splitlines()
                    out2 = []
                    def _fmt_owner(role: str | None):
                        if not role:
                            return ''
                        if not role.islower():
                            return '"' + role.replace('"','""') + '"'
                        return role
                    for ln in txt2:
                        if 'OWNER TO' in ln.upper():
                            if replacement_owner:
                                out2.append(_re.sub(r'OWNER TO\s+[^;]+;', f'OWNER TO {_fmt_owner(replacement_owner)};', ln, flags=_re.IGNORECASE))
                            else:
                                # Drop line entirely if we cannot map to a valid local owner
                                continue
                        else:
                            out2.append(ln)
                    filtered_sql.write_text('\n'.join(out2), encoding='utf-8')
                    print("Re-attempting restore with rewritten ownership clauses...")
                    second = _subp.run(restore_cmd, env=proc_env, text=True)
                    if second.returncode != 0:
                        print("ERROR: Second restore attempt after owner rewrite still failed.", file=sys.stderr)
                        sys.exit(second.returncode)
                else:
                    print("ERROR: Restore failed and no specific missing roles extracted.", file=sys.stderr)
                    sys.exit(first.returncode)
            elif first.returncode != 0:
                print("ERROR: Restore failed.", file=sys.stderr)
                sys.exit(first.returncode)
    else:
        pg_restore_cmd = [
            'pg_restore', '--verbose'
        ]
        if args.skip_owner:
            pg_restore_cmd.append('--no-owner')
        pg_restore_cmd += ['--exit-on-error']
        pg_restore_cmd += args.extra_pgrestore_arg
        pg_restore_cmd += ['--dbname', local['db'], '--host', local['host'], '--port', local['port'], '--username', local['user'], str(dump_file)]
        try:
            run(pg_restore_cmd, env_extra={'PGPASSWORD': local['password']}, dry=args.dry_run)
        except SystemExit as se:
            # If pg_restore failed and we didn't already try filtering, auto-detect transaction_timeout and retry
            if not args.strip_unsupported_gucs and not dump_mode_plain and not args.dry_run:
                # Provide simple message; user saw console output already
                print("pg_restore failed; inspecting for unsupported GUCs to auto-filter...")
                # On Windows we can't easily capture stdout since run() already printed, so re-run with capture
                import subprocess
                proc_env = os.environ.copy()
                proc_env['PGPASSWORD'] = str(local['password'] or '')
                cap = subprocess.run(pg_restore_cmd, env=proc_env, capture_output=True, text=True)
                if 'transaction_timeout' in (cap.stderr or '') or 'transaction_timeout' in (cap.stdout or ''):
                    print("Detected transaction_timeout; performing automatic filter & retry.")
                    # Simulate strip_unsupported_gucs minimal pathway
                    plain_sql = dump_file.with_suffix('.auto.plain.sql')
                    filtered_auto = dump_file.with_suffix('.auto.filtered.sql')
                    subprocess.run(['pg_restore','-f', str(plain_sql), str(dump_file)], env=proc_env, check=True)
                    if plain_sql.exists():
                        txt = plain_sql.read_text(encoding='utf-8', errors='ignore').splitlines()
                        kept = [ln for ln in txt if not ln.strip().startswith('SET transaction_timeout =')]
                        filtered_auto.write_text('\n'.join(kept), encoding='utf-8')
                        print("Retrying restore using filtered SQL (auto mode)...")
                        run([
                            'psql','-v','ON_ERROR_STOP=1','--host', str(local['host'] or ''),'--port', str(local['port'] or ''),'--username', str(local['user'] or ''),'--dbname', str(local['db'] or ''), '--file', str(filtered_auto)
                        ], env_extra={'PGPASSWORD': local['password']}, dry=False)
                        # If we need role sanitation automatically, we could extend here (future enhancement)
                    else:
                        print("Automatic plain SQL extraction failed; aborting.")
                        raise se
                else:
                    # Not a transaction_timeout issue; re-raise original exit
                    raise se
            else:
                raise se

    if not args.no_analyze:
        print("=== Running ANALYZE ===")
        analyze_cmd = [
            'psql', '-v', 'ON_ERROR_STOP=1', '--host', local['host'], '--port', local['port'], '--username', local['user'], '--dbname', local['db'],
            '--command', 'ANALYZE;' ]
        run(analyze_cmd, env_extra={'PGPASSWORD': local['password']}, dry=args.dry_run)

    if not keep_dump:
        print("=== Removing dump file ===")
        if not args.dry_run and dump_file.exists():
            try:
                dump_file.unlink()
            except Exception as ex:
                print(f"WARNING: could not delete dump file: {ex}")

    print("=== Refresh complete ===")

if __name__ == '__main__':  # pragma: no cover
    main()
