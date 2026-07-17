"""politrack command-line interface."""

from __future__ import annotations

import time

import typer

from . import config, db, pipeline

app = typer.Typer(help="Politician-trade watcher and analysis pipeline.", no_args_is_help=True)


@app.command()
def init_db() -> None:
    """Create the SQLite schema."""
    conn = db.connect()
    db.init_db(conn)
    conn.close()
    typer.echo(f"Initialized {config.DB_PATH}")


@app.command()
def cycle(
    source: list[str] = typer.Option(None, "--source", help="Limit to specific sources (house/senate/oge)."),
    limit_filings: int = typer.Option(0, help="Max new filings to extract this cycle (0 = all)."),
    no_analyze: bool = typer.Option(False, help="Skip the analysis stage."),
    analyze_limit: int = typer.Option(0, help="Max trades to analyze this cycle (0 = unlimited)."),
    loop: bool = typer.Option(False, help="Dev mode: repeat every 30 minutes."),
) -> None:
    """Run one watcher cycle: poll -> extract -> analyze -> report."""
    while True:
        stats = pipeline.run_cycle(
            sources=list(source) if source else None,
            limit_filings=limit_filings,
            analyze=not no_analyze,
            analyze_limit=analyze_limit,
        )
        typer.echo(f"Cycle done: {stats}")
        if not loop:
            break
        time.sleep(1800)


@app.command()
def analyze(trade_id: int = typer.Option(..., "--trade-id")) -> None:
    """Analyze a single trade by id (testing helper)."""
    conn = db.connect()
    trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if trade is None:
        typer.echo(f"No trade with id {trade_id}")
        raise typer.Exit(1)
    pipeline.analyze_trade_row(conn, trade)
    row = conn.execute("SELECT * FROM analyses WHERE trade_id = ?", (trade_id,)).fetchone()
    typer.echo(dict(row) if row else "skipped/failed")
    conn.close()


@app.command()
def backfill(count: int = typer.Option(100, help="Target number of analyzed trades.")) -> None:
    """Promote recent backlog filings and analyze up to COUNT trades (launch content)."""
    stats = pipeline.run_backfill(count)
    typer.echo(f"Backfill done: {stats}")


if __name__ == "__main__":
    app()
