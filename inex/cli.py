import argparse
import json
import logging
import logging.config
import sys
from dataclasses import asdict
from pathlib import Path

from inex.app import build_app
from inex.config import load_settings
from inex.errors import InexError
from inex.validators import (
    parse_count,
    parse_end,
    parse_filename,
    parse_format,
    parse_order,
    parse_repo,
    parse_start,
)

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 130
STDOUT_STREAM = "ext://sys.stdout"
STDERR_STREAM = "ext://sys.stderr"
OUTPUT_ENCODING = "utf-8"


def configure_logging(path):
    path = Path(path)
    if not path.is_file():
        return None
    config = json.loads(path.read_text(encoding="utf-8"))
    log_file = None
    for handler in config.get("handlers", {}).values():
        if handler.get("stream") == STDOUT_STREAM:
            handler["stream"] = STDERR_STREAM
        filename = handler.get("filename")
        if filename:
            handler.setdefault("encoding", OUTPUT_ENCODING)
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            log_file = log_file or filename
    logging.config.dictConfig(config)
    return log_file


def _window(args):
    return {
        "count": parse_count(args.count),
        "start": parse_start(args.since),
        "end": parse_end(args.until),
        "order": parse_order("asc" if args.oldest_first else "desc"),
    }


def _count(app, args):
    result = app.collection.count(*parse_repo(args.repo))
    return result, f"{result.owner}/{result.repo} has {result.issue_count} issues."


def _collect(app, args):
    owner, repo = parse_repo(args.repo)
    result = app.collection.collect(owner, repo, **_window(args))
    return result, (
        f"Collected {result.collected} issues from {owner}/{repo} "
        f"({result.skipped} already in the database)."
    )


def _classify(app, args):
    owner, repo = parse_repo(args.repo)
    if args.preview:
        p = app.classification.preview(owner, repo, **_window(args))
        return p, (
            f"{p.would_classify} of {p.total_remaining} unclassified issues in {owner}/{repo} "
            f"would be classified with {p.model} ({p.prompt_version})."
        )
    result = app.classification.classify(owner, repo, **_window(args))
    summary = f"Classified {result.classified} issues from {owner}/{repo}."
    if result.failed:
        total = sum(result.failed.values())
        breakdown = ", ".join(f"{status}={n}" for status, n in sorted(result.failed.items()))
        summary += f" {total} failed and were not labelled ({breakdown}); re-run to retry them."
    return result, summary


def _link(app, args):
    owner, repo = parse_repo(args.repo)
    result = app.linkage.link(
        owner,
        repo,
        args.package,
        start=parse_start(args.since),
        end=parse_end(args.until),
        version_range=args.version_range,
        last_n_versions=parse_count(args.last_versions),
        version_start=parse_start(args.versions_from),
        version_end=parse_end(args.versions_to),
    )
    return result, (
        f"Linked {result.linked_issues} issues in {owner}/{repo} to {result.package_name} "
        f"releases; {result.unlinkable_pre_release} predate the first release."
    )


def _import(app, args):
    if args.preview:
        p = app.imports.preview_list(args.csv_path, args.column)
        return p, (
            f"{p.total_issues} issues across {p.distinct_repos} repositories: "
            f"{p.already_in_db} already in the database, {p.to_collect} to collect, "
            f"{p.unparseable_count} unreadable rows."
        )
    r = app.imports.import_list(args.csv_path, args.column, classify=not args.collect_only)
    return r, (
        f"Collected {r.collected}, skipped {r.skipped}, failed {r.failed}, "
        f"classified {r.classified}."
    )


def _load(app, args):
    temperature = app.settings.temperature if args.temperature is None else args.temperature
    r = app.imports.load_jsonl(args.path, args.model, args.prompt_version, temperature)
    return r, (
        f"Loaded {r.issues} issues and {r.classifications} classifications from {r.files} files."
    )


def _query(app, args):
    result = app.queries.run(args.sql)
    lines = ["\t".join(result.columns)]
    lines += ["\t".join("" if v is None else str(v) for v in row) for row in result.rows]
    if result.truncated:
        lines.append(f"(truncated at {app.queries.max_rows} rows)")
    return result, "\n".join(lines)


def _export(app, args):
    result = app.queries.export(args.sql, parse_format(args.format), parse_filename(args.name))
    return result, f"Exported {result.rows_exported} rows to {result.path}."


def _chat(app, args):
    from inex.agent.chat import run_chat

    run_chat(app)
    return None, None


DB_MESSAGES = {
    "init": "Database ready at {path}.",
    "reset": "Database reset at {path}.",
    "clear": "Database cleared at {path}.",
}


def _db(app, args):
    result = getattr(app.maintenance, args.action)()
    return result, DB_MESSAGES[args.action].format(path=result.path)


def _add_repo_window(parser):
    parser.add_argument("repo", metavar="OWNER/REPO")
    parser.add_argument("--count", type=int)
    parser.add_argument("--since", metavar="DATE")
    parser.add_argument("--until", metavar="DATE")
    parser.add_argument("--oldest-first", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m inex",
        description="Collect, classify and link GitHub issues from npm projects.",
    )
    parser.add_argument("--db", type=Path, help="SQLite database to use")
    parser.add_argument("--classifier", help="classifier model, e.g. qwen3:30b")
    parser.add_argument("--profile", type=Path, help="classification profile YAML")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    count = commands.add_parser("count", help="count a repository's issues on GitHub")
    count.add_argument("repo", metavar="OWNER/REPO")
    count.set_defaults(handler=_count)

    collect = commands.add_parser("collect", help="collect issues into the database")
    _add_repo_window(collect)
    collect.set_defaults(handler=_collect)

    classify = commands.add_parser("classify", help="classify collected issues")
    _add_repo_window(classify)
    classify.add_argument("--preview", action="store_true")
    classify.set_defaults(handler=_classify)

    link = commands.add_parser("link", help="link issues to the npm release live when filed")
    link.add_argument("repo", metavar="OWNER/REPO")
    link.add_argument("--package", required=True)
    link.add_argument("--since", metavar="DATE")
    link.add_argument("--until", metavar="DATE")
    link.add_argument("--version-range")
    link.add_argument("--last-versions", type=int)
    link.add_argument("--versions-from", metavar="DATE")
    link.add_argument("--versions-to", metavar="DATE")
    link.set_defaults(handler=_link)

    issue_list = commands.add_parser("import", help="collect and classify a CSV list of issues")
    issue_list.add_argument("csv_path", metavar="LIST.csv")
    issue_list.add_argument("--column")
    issue_list.add_argument("--collect-only", action="store_true")
    issue_list.add_argument("--preview", action="store_true")
    issue_list.set_defaults(handler=_import)

    load = commands.add_parser("load", help="load issues and labels from JSONL files")
    load.add_argument("path", metavar="FILE_OR_DIR")
    load.add_argument("--model", required=True)
    load.add_argument("--prompt-version", required=True)
    load.add_argument("--temperature", type=float)
    load.set_defaults(handler=_load)

    query = commands.add_parser("query", help="run a read-only SELECT")
    query.add_argument("sql", metavar="SQL")
    query.set_defaults(handler=_query)

    export = commands.add_parser("export", help="export a SELECT to CSV or JSON")
    export.add_argument("sql", metavar="SQL")
    export.add_argument("--format", default="csv")
    export.add_argument("--name")
    export.set_defaults(handler=_export)

    chat = commands.add_parser("chat", help="talk to the agent")
    chat.set_defaults(handler=_chat)

    db = commands.add_parser("db", help="create, reset or clear the database")
    actions = db.add_subparsers(dest="action", required=True, metavar="ACTION")
    actions.add_parser("init", help="create the schema")
    for action in ("reset", "clear"):
        sub = actions.add_parser(action, help=f"{action} every table")
        sub.add_argument("--yes", action="store_true", required=True)
    db.set_defaults(handler=_db)
    return parser


def _report_error(args, code, message):
    if args.json:
        print(json.dumps({"status": "error", "code": code, "message": message}))
    else:
        print(f"error: {message}", file=sys.stderr)


def _use_utf8_output():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding=OUTPUT_ENCODING, errors="replace")


def main(argv=None, build=build_app) -> int:
    _use_utf8_output()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return exit_.code
    log_file = None
    app = None
    try:
        settings = load_settings(database=args.db, classifier=args.classifier, profile=args.profile)
        log_file = configure_logging(settings.logging_config)
        app = build(settings)
        result, summary = args.handler(app, args)
    except InexError as error:
        _report_error(args, error.code, error.message)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception:
        logger.exception("Command %s failed", args.command)
        hint = f"see {log_file}" if log_file else "see the log for details"
        _report_error(args, "internal_error", f"unexpected failure; {hint}.")
        return EXIT_ERROR
    finally:
        if app is not None:
            app.close()
    if result is not None:
        if args.json:
            print(json.dumps({"status": "ok", **asdict(result)}, default=str))
        else:
            print(summary)
    return EXIT_OK
