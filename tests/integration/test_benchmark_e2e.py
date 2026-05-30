"""End-to-end proof for the fragility benchmark pipeline.

Drives the real shared orchestration (`_run_mutation_suite`): a real
run_replay_proxy is started for the baseline and for every mutation, a real
agent subprocess runs against it, the outcomes are scored, and a leaderboard
is written to disk. No live LLM calls — the agent here makes no network
requests, so the proxy simply has nothing to serve, which is all this test
needs to exercise the full pipeline deterministically.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from rewind.cli import _run_mutation_suite
from rewind.engines.benchmark import Leaderboard, render_leaderboard_html, score_results
from rewind.storage.blobs import BlobStore
from rewind.storage.db import RewindDB, Session, Step
from tests.fixtures.proxy_runner import find_free_port


def _seed_session(tmp_path: Path) -> tuple[RewindDB, BlobStore, Session]:
    db = RewindDB(":memory:")
    blobs = BlobStore(tmp_path / "blobs")
    # Agent that ignores the proxy and prints a stable line, so baseline and
    # every mutation produce identical output -> deterministic "all survived".
    command = json.dumps([sys.executable, "-c", "print('agent-ok')"])
    session = Session(agent_name="e2e-bench", command=command, git_hash="testsha")
    db.save_session(session)
    provider_body = {"content": [{"type": "text", "text": "hi"}], "role": "assistant"}
    wrapped = {"status_code": 200, "headers": {}, "body": json.dumps(provider_body)}
    req_blob = blobs.write(json.dumps({"method": "POST", "body": {"m": 1}}).encode())
    resp_blob = blobs.write(json.dumps(wrapped).encode())
    db.save_step(
        Step(
            session_id=session.id,
            order_idx=0,
            type="llm_call",
            provider="anthropic",
            model="claude-haiku-4-5",
            match_key="k-0",
            req_blob=req_blob,
            resp_blob=resp_blob,
        )
    )
    return db, blobs, session


@pytest.mark.integration
def test_benchmark_pipeline_writes_leaderboard(tmp_path: Path) -> None:
    db, blobs, session = _seed_session(tmp_path)

    results = asyncio.run(
        _run_mutation_suite(
            db,
            blobs,
            session,
            session.command or "",
            mutator=None,
            port=find_free_port(),
            timeout=30,
        )
    )

    # One step yields the five syntactic mutations.
    assert len(results) == 5
    # The agent ignores the proxy and prints the same line every time, so it
    # survives every fault and never crashes.
    assert all(not r.crashed for r in results)
    assert all(not r.output_changed for r in results)

    score = score_results(session.agent_name, results, git_hash=session.git_hash)
    assert score.total_mutations == 5
    assert score.survived == 5
    assert score.fragility_score == 0.0
    assert score.git_hash == "testsha"

    board = Leaderboard()
    board.upsert(score)
    out = tmp_path / "site"
    out.mkdir()
    (out / "leaderboard.json").write_text(json.dumps(board.to_json()), encoding="utf-8")
    (out / "index.html").write_text(render_leaderboard_html(board), encoding="utf-8")

    assert (out / "leaderboard.json").exists()
    loaded = Leaderboard.from_json(json.loads((out / "leaderboard.json").read_text("utf-8")))
    assert loaded.scores[0].agent_name == "e2e-bench"
    assert "e2e-bench" in (out / "index.html").read_text("utf-8")
