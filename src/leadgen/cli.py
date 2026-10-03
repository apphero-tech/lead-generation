"""Command line: `tool run --state FL [--unitid 134130 | --name "University of Florida"]`."""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from datetime import datetime

from .config import load_settings
from .db import connect, set_step
from .export import export_state
from .ipeds import load_institutions
from .pipeline import Pipeline

log = logging.getLogger("leadgen")


def setup_logging(log_dir, verbose: bool) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logfile = logging.FileHandler(log_dir / f"run_{datetime.now():%Y%m%d}.log", encoding="utf-8")
    logfile.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(logfile)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def select(conn, state: str, unitid: str, name: str, include_systems: bool):
    sql = "SELECT * FROM institutions WHERE state = ?"
    args = [state]
    if unitid:
        sql += " AND unitid = ?"
        args.append(unitid)
    elif name:
        sql += " AND lower(name) LIKE ?"
        args.append(f"%{name.lower()}%")
    elif not include_systems:
        sql += " AND is_system = 0"
    sql += " ORDER BY is_system DESC, name"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def cmd_run(args, settings) -> int:
    conn = connect(settings.db_path)
    state = args.state.upper()
    load_institutions(conn, settings, state)
    insts = select(conn, state, args.unitid, args.name, include_systems=True)
    if args.limit:
        insts = insts[: args.limit]
    if not insts:
        log.error("No institution matches the selection")
        return 1
    log.info("Processing %d institution(s) in %s", len(insts), state)
    pipe = Pipeline(conn, settings)
    for n, inst in enumerate(insts, 1):
        log.info("[%d/%d] %s (%s)", n, len(insts), inst["name"], inst["website"] or "no website")
        try:
            pipe.run_institution(inst, refresh=args.refresh)
        except KeyboardInterrupt:
            log.warning("Interrupted; re-run the same command to resume")
            break
        except Exception as exc:  # keep going; the failure is recorded and visible in Coverage
            log.exception("Failed on %s", inst["name"])
            set_step(conn, inst["unitid"], "extract", "failed", str(exc)[:300])
    path = export_state(conn, state, settings.out_dir)
    log.info("Excel file written: %s", path)
    return 0


def cmd_export(args, settings) -> int:
    conn = connect(settings.db_path)
    path = export_state(conn, args.state.upper(), settings.out_dir)
    log.info("Excel file written: %s", path)
    return 0


def cmd_status(args, settings) -> int:
    conn = connect(settings.db_path)
    rows = conn.execute(
        "SELECT s.step, s.status, COUNT(*) AS n FROM step_status s JOIN institutions i ON i.unitid = s.unitid "
        "WHERE i.state = ? GROUP BY s.step, s.status ORDER BY s.step", (args.state.upper(),)
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM institutions WHERE state = ?", (args.state.upper(),)).fetchone()[0]
    print(f"{args.state.upper()}: {total} institutions")
    for r in rows:
        print(f"  {r['step']:<8} {r['status']:<8} {r['n']}")
    return 0


def cmd_ui(args, settings) -> int:
    import webbrowser
    from .web import create_app
    url = f"http://127.0.0.1:{args.port}/"
    if args.host != "127.0.0.1" and not settings.ui_password:
        log.error("Refusing to listen on %s without a password: set LEADGEN_UI_PASSWORD in .env", args.host)
        return 1
    log.info("Interface: %s (Ctrl+C to stop)", url)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    create_app(settings).run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tool", description="US university contact list builder (free sources)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="crawl, extract and export one state")
    run.add_argument("--state", required=True, help="two-letter state code, e.g. FL")
    run.add_argument("--unitid", default="", help="only this IPEDS UNITID (e.g. 134130 = University of Florida)")
    run.add_argument("--name", default="", help="only institutions whose name contains this text")
    run.add_argument("--limit", type=int, default=0, help="process at most N institutions")
    run.add_argument("--refresh", action="store_true", help="re-download pages instead of using the cache")
    exp = sub.add_parser("export", help="rebuild the Excel file from the database")
    exp.add_argument("--state", required=True)
    stat = sub.add_parser("status", help="show step progress for a state")
    stat.add_argument("--state", required=True)
    ui = sub.add_parser("ui", help="open the web interface")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")
    ui.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to accept other machines (needs a password)")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings.log_dir, args.verbose)
    return {"run": cmd_run, "export": cmd_export, "status": cmd_status, "ui": cmd_ui}[args.command](args, settings)


if __name__ == "__main__":
    sys.exit(main())
