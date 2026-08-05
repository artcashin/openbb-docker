"""tick-lab: load FirstRate ticks into the shared store, and check derived bars.

    tick-lab load  ./FirstRate_sample.zip
    tick-lab compare --symbol MSFT --date 2023-05-12
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

from tick_lab.config import ConfigError, from_env
from tick_lab.firstrate import FirstRateFormatError, parse
from tick_lab.reference.base import ReferenceError, fetch_finest
from tick_lab.reference.yfinance_adapter import YFinanceAdapter
from tick_lab.report import compare as compare_frames
from tick_lab.rollup import aggregate, to_minute_bars
from tick_lab.store import TickStore

# Later releases add entries here; the CLI needs no other change.
ADAPTERS = {
    "yfinance": YFinanceAdapter,
}

TRADE_LIBRARY = "ticks"
QUOTE_LIBRARY = "quotes"

# The brief's original pattern ended in a `\b` word-boundary assertion, but
# "_" is itself a word character, so "trades_" / "quotes_" (the plural
# immediately followed by the "_YYYY-MM-DD" date suffix real FirstRate
# filenames carry) never produced a boundary and the match silently failed.
# A lookahead for "_", "." or end-of-string is what the boundary was meant
# to express: "trade"/"quote" (optionally plural) followed by a separator,
# not folded into a longer word.
_NAME = re.compile(r"^([A-Za-z0-9.\-]+)_(trade|quote)s?(?=[_.]|$)", re.IGNORECASE)

# What tick_lab.store.to_bounds actually accepts: a plain date, or an ISO
# date+time joined by "T" or a space. Anything else (typos like
# "2023-13-45", free text like "last-tuesday") is rejected here, at the
# argparse boundary, instead of reaching pandas/ArcticDB deep in `compare`
# and surfacing as a confusing parse error.
_DATE_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}:\d{2}(\.\d+)?)?$")


def date_or_datetime(value: str) -> str:
    """argparse `type=` validator for --date/--end.

    Accepts YYYY-MM-DD or an ISO YYYY-MM-DD[T ]HH:MM:SS datetime -- the two
    shapes tick_lab.store.to_bounds understands -- and rejects anything else
    with a message naming the expected format.
    """
    if not _DATE_SHAPE.match(value):
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD or an ISO "
            "YYYY-MM-DD[T ]HH:MM:SS datetime"
        )
    try:
        pd.Timestamp(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}: {err}; expected YYYY-MM-DD or an ISO "
            "YYYY-MM-DD[T ]HH:MM:SS datetime"
        ) from err
    return value


def symbol_from_filename(name: str) -> tuple[str, str]:
    """Derive (SYMBOL, kind) from a FirstRate file name."""
    match = _NAME.match(Path(name).name)
    if not match:
        raise ValueError(
            f"cannot tell the symbol and kind from {name!r}; "
            "expected something like MSFT_trades_2023-05-12.txt"
        )
    return match.group(1).upper(), match.group(2).lower()


def _iter_members(path: Path):
    """Yield (name, text) for each data file, from a zip or a directory."""
    if path.is_dir():
        for child in sorted(path.iterdir()):
            # Same noise-prefix convention as the zip branch below, so a
            # ".DS_Store" left behind by Finder in an extracted directory is
            # skipped exactly like the zip's own "__MACOSX/" entries are.
            if child.is_file() and not child.name.startswith(("_", ".")):
                yield child.name, child.read_text()
        return

    with zipfile.ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda i: i.filename):
            # Checking only the leaf name misses macOS zip noise that lives
            # in a prefixed *directory*, e.g. "__MACOSX/._MSFT_trades....txt"
            # -- the leaf "._MSFT_trades....txt" itself does start with ".",
            # but a plain "__MACOSX/junk" would slip through with a leaf-only
            # check. Test every path component instead.
            parts = Path(info.filename).parts
            if info.is_dir() or any(p.startswith(("_", ".")) for p in parts):
                continue
            yield Path(info.filename).name, archive.read(info).decode("utf-8")


def cmd_load(args) -> int:
    store = TickStore(from_env())
    failures = 0

    for name, text in _iter_members(Path(args.path)):
        try:
            symbol, kind_from_name = symbol_from_filename(name)
            kind, frame = parse(text)
        except (ValueError, FirstRateFormatError) as err:
            print(f"  SKIP {name}: {err}", file=sys.stderr)
            failures += 1
            continue

        if kind != kind_from_name:
            print(
                f"  SKIP {name}: filename says {kind_from_name}, contents look like {kind}",
                file=sys.stderr,
            )
            failures += 1
            continue

        library = TRADE_LIBRARY if kind == "trade" else QUOTE_LIBRARY
        if args.dry_run:
            print(f"  would write {len(frame):>8,} {kind} rows -> {library}/{symbol}")
            continue

        store.write(library, symbol, frame, metadata={"source": name, "kind": kind})
        print(f"  wrote {len(frame):>8,} {kind} rows -> {library}/{symbol}")

    return 1 if failures else 0


def cmd_compare(args) -> int:
    cfg = from_env()
    store = TickStore(cfg)

    trades = store.read(TRADE_LIBRARY, args.symbol, start=args.date, end=args.date)
    if trades.empty:
        print(
            f"no ticks stored for {args.symbol} on {args.date} "
            f"(library {TRADE_LIBRARY!r}) — run `tick-lab load` first",
            file=sys.stderr,
        )
        return 1

    ours_1m = to_minute_bars(trades, session=args.session)
    print(f"rolled {len(trades):,} ticks into {len(ours_1m):,} 1-minute bars "
          f"(session={args.session})")

    adapter = ADAPTERS[args.reference]()
    print(f"asking {adapter.name} for 1m bars...")

    try:
        result = fetch_finest(adapter, args.symbol, args.date, args.end or args.date)
    except ReferenceError as err:
        print(f"\n{adapter.name} cannot serve this window: {err.kind}", file=sys.stderr)
        print(f"  {err.detail}", file=sys.stderr)
        return 1

    notes = []
    for attempt in result.attempts:
        if attempt.error is not None:
            print(f"  {attempt.interval}: {attempt.error.kind} — {attempt.error.detail}")
    if result.stepped_down:
        notes.append(
            f"{adapter.name} could not serve 1m for this window; "
            f"compared at {result.interval} instead"
        )
        print(f"  falling back to {result.interval}")

    ours = aggregate(ours_1m, result.interval)
    report = compare_frames(
        ours, result.frame, args.symbol, result.interval,
        tolerance=args.tol, notes=notes,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print()
        print(report.to_text())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tick-lab", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    load = sub.add_parser("load", help="load a FirstRate zip (or directory) into ArcticDB")
    load.add_argument("path", help="path to the .zip or an extracted directory")
    load.add_argument("--dry-run", action="store_true",
                      help="report what would be written, write nothing")
    load.set_defaults(func=cmd_load)

    comp = sub.add_parser("compare", help="compare stored ticks against a reference source")
    comp.add_argument("--symbol", required=True)
    comp.add_argument("--date", required=True, type=date_or_datetime, help="YYYY-MM-DD")
    comp.add_argument("--end", type=date_or_datetime, help="end date; defaults to --date")
    comp.add_argument("--reference", default="yfinance", choices=sorted(ADAPTERS))
    comp.add_argument("--session", default="regular", choices=["regular", "all"])
    comp.add_argument("--tol", type=float, default=0.01,
                      help="price tolerance in the instrument's units (default 0.01)")
    comp.add_argument("--json", action="store_true")
    comp.set_defaults(func=cmd_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help(sys.stderr)
        return 2
    try:
        return args.func(args)
    except ConfigError as err:
        print(f"configuration error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
