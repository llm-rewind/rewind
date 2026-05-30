"""Benchmark every recorded agent in the local Rewind DB and build the leaderboard.

This is the driver the weekly CI job runs. It walks the local Rewind database,
mutation-tests every session that has a runnable command (skipping mutation-
derived sessions), scores each one's fragility, and writes a ranked leaderboard
(leaderboard.json + index.html) to the output directory.

It performs zero live LLM calls: every agent is re-run in replay mode against
its recorded cassette. Sessions without a stored command are skipped — there is
nothing to re-run — so the board only ever reflects agents that can actually be
exercised.

Usage:
    python scripts/run_fragility_benchmark.py --output-dir benchmark_site
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from rewind.cli import _run_mutation_suite
from rewind.engines.benchmark import Leaderboard, render_leaderboard_html, score_results
from rewind.engines.mutate import delete_mutated_sessions
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session


def _benchmarkable(session: Session) -> bool:
    # A runnable command is required to re-run the agent. Mutation-derived
    # sessions (produced by a prior benchmark/mutate run) are not agents in
    # their own right and must not pollute the board.
    if not session.command:
        return False
    return "base_session_id" not in session.metadata


async def _run(output_dir: Path, port: int, timeout: int) -> int:
    db = RewindDB.get_or_create()
    blobs = BlobStore()

    sessions = [s for s in db.list_sessions(limit=10_000) if _benchmarkable(s)]
    if not sessions:
        print("No benchmarkable sessions found (none have a stored command).")

    board = Leaderboard()
    for session in sessions:
        command_str = session.command or ""
        print(f"Benchmarking {session.agent_name} ({session.id[:8]})...")
        try:
            results = await _run_mutation_suite(
                db, blobs, session, command_str, mutator=None, port=port, timeout=timeout
            )
        finally:
            delete_mutated_sessions(db, session.id)
        if not results:
            print(f"  no mutable steps; skipping {session.id[:8]}")
            continue
        score = score_results(session.agent_name, results, git_hash=session.git_hash)
        board.upsert(score)
        print(f"  fragility {score.fragility_score:.1%} over {score.total_mutations} mutations")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "leaderboard.json").write_text(
        json.dumps(board.to_json(), indent=2, sort_keys=True), encoding="utf-8"
    )
    (output_dir / "index.html").write_text(render_leaderboard_html(board), encoding="utf-8")
    print(f"Wrote leaderboard for {len(board.scores)} agent(s) to {output_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="benchmark_site", type=Path)
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--timeout", default=60, type=int)
    args = parser.parse_args()
    return asyncio.run(_run(args.output_dir, args.port, args.timeout))


if __name__ == "__main__":
    sys.exit(main())
