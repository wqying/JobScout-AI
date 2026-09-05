import argparse
import asyncio
from pathlib import Path

from app.db.session import get_engine, get_session_factory
from app.immigration.importer import LcaImporter, LcaImportError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import official DOL H-1B sponsorship disclosure data"
    )
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--fiscal-year", required=True, type=int)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--record-layout-version", required=True)
    return parser


async def _run() -> int:
    args = _parser().parse_args()
    try:
        async with get_session_factory()() as session:
            summary = await LcaImporter(session).import_file(
                args.file,
                args.fiscal_year,
                args.source_url,
                args.record_layout_version,
            )
    except LcaImportError as exc:
        print(f"Import failed [{exc.code}]: {exc}")
        return 1
    finally:
        await get_engine().dispose()

    cache_label = "already imported" if summary.cached else "imported"
    print(
        f"{cache_label}: FY{summary.fiscal_year}, {summary.rows_accepted}/{summary.rows_read} "
        f"rows accepted, {summary.employers} employer aggregates, id={summary.import_id}"
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
