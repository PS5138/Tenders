"""Start a local Postgres 16 with pgvector for development, on a fixed TCP port, using the
binaries bundled with the ``pgserver`` package (the same server the test suite uses).

    .venv/bin/python backend/scripts/dev_db.py                 # start, print DATABASE_URL, wait
    .venv/bin/python backend/scripts/dev_db.py --migrate       # also run alembic + seed
    .venv/bin/python backend/scripts/dev_db.py --detach        # leave it running, return
    .venv/bin/python backend/scripts/dev_db.py --stop          # stop a detached server
    .venv/bin/python backend/scripts/dev_db.py --reset         # wipe the data directory first

The data directory defaults to ``<repo>/.devdb`` (ignored by git) and the port to 54329, so
it never collides with a Docker or system Postgres on 5432. The server listens on 127.0.0.1
only, with trust authentication, and is for development on this machine only. The printed
``DATABASE_URL`` is what the API and the worker need in their environment.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pgserver
import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_PGDATA = REPO_ROOT / ".devdb"
DEFAULT_PORT = 54329
DEFAULT_DB = "tenders_dev"
PG_USER = "postgres"


def database_url(port: int, db_name: str) -> str:
    return f"postgresql+psycopg://{PG_USER}@127.0.0.1:{port}/{db_name}"


def _is_running(pgdata: Path) -> bool:
    if not (pgdata / "postmaster.pid").exists():
        return False
    try:
        pgserver.pg_ctl(["status"], pgdata=pgdata, timeout=10)
    except subprocess.CalledProcessError:
        return False
    return True


def _running_port(pgdata: Path) -> int | None:
    """The port a running server listens on, from the fourth line of postmaster.pid."""
    try:
        lines = (pgdata / "postmaster.pid").read_text().splitlines()
        return int(lines[3])
    except (OSError, IndexError, ValueError):
        return None


def init_cluster(pgdata: Path) -> None:
    if (pgdata / "PG_VERSION").exists():
        return
    pgdata.parent.mkdir(parents=True, exist_ok=True)
    print(f"initialising a new cluster in {pgdata}", file=sys.stderr)
    pgserver.initdb(["-U", PG_USER, "--auth=trust", "-E", "UTF8"], pgdata=pgdata, timeout=120)


def start_server(pgdata: Path, port: int) -> None:
    # A short socket directory: unix socket paths are limited to about 100 characters.
    socket_dir = Path(tempfile.mkdtemp(prefix="tenders-pg-"))
    log = pgdata / "server.log"
    options = f"-p {port} -h 127.0.0.1 -k {socket_dir}"
    pgserver.pg_ctl(["-w", "-o", options, "-l", str(log), "start"], pgdata=pgdata, timeout=60)


def stop_server(pgdata: Path) -> None:
    if not _is_running(pgdata):
        print("no server is running", file=sys.stderr)
        return
    pgserver.pg_ctl(["-w", "-m", "fast", "stop"], pgdata=pgdata, timeout=60)
    print("server stopped", file=sys.stderr)


def ensure_database(port: int, db_name: str) -> None:
    admin = f"postgresql://{PG_USER}@127.0.0.1:{port}/postgres"
    for attempt in range(20):
        try:
            with psycopg.connect(admin, autocommit=True) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
                ).fetchone()
                if not exists:
                    conn.execute(f'CREATE DATABASE "{db_name}"')
            break
        except psycopg.OperationalError:
            if attempt == 19:
                raise
            time.sleep(0.25)
    with psycopg.connect(
        f"postgresql://{PG_USER}@127.0.0.1:{port}/{db_name}", autocommit=True
    ) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")


def migrate_and_seed(url: str) -> None:
    """Run the Alembic migrations and the seed against ``url`` in this process."""
    os.environ["DATABASE_URL"] = url
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from alembic import command
    from alembic.config import Config

    from app.config import get_settings
    from app.db.seed import seed
    from app.db.session import configure_engine, new_session

    get_settings.cache_clear()
    configure_engine(url)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    session = new_session()
    try:
        result = seed(session)
        session.commit()
    finally:
        session.close()
    print(f"migrated to head and seeded ({result})", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pgdata", type=Path, default=DEFAULT_PGDATA)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=DEFAULT_DB, help="database name to create and use")
    parser.add_argument("--migrate", action="store_true", help="run alembic upgrade head + seed")
    parser.add_argument("--detach", action="store_true", help="leave the server running")
    parser.add_argument("--stop", action="store_true", help="stop a running server and exit")
    parser.add_argument("--reset", action="store_true", help="delete the data directory first")
    args = parser.parse_args(argv)
    pgdata: Path = args.pgdata.expanduser().resolve()

    if args.stop:
        stop_server(pgdata)
        return 0

    if args.reset:
        if _is_running(pgdata):
            stop_server(pgdata)
        shutil.rmtree(pgdata, ignore_errors=True)

    if _is_running(pgdata):
        port = _running_port(pgdata) or args.port
        print(f"server already running on port {port}", file=sys.stderr)
    else:
        port = args.port
        init_cluster(pgdata)
        start_server(pgdata, port)
        print(f"server started on 127.0.0.1:{port} (log: {pgdata / 'server.log'})", file=sys.stderr)

    ensure_database(port, args.db)
    url = database_url(port, args.db)
    if args.migrate:
        migrate_and_seed(url)

    # The one line meant for copying or for `export $(... | tail -1)`.
    print(f"DATABASE_URL={url}")
    sys.stdout.flush()

    if args.detach:
        where = "" if pgdata == DEFAULT_PGDATA.resolve() else f" --pgdata {pgdata}"
        print(
            f"stop it later with: {sys.executable} {Path(__file__)} --stop{where}",
            file=sys.stderr,
        )
        return 0

    print("press Ctrl-C to stop", file=sys.stderr)
    stopped = False

    def _handle(signum, _frame) -> None:  # noqa: ANN001
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    while not stopped:
        time.sleep(0.5)
    stop_server(pgdata)
    return 0


if __name__ == "__main__":
    sys.exit(main())
