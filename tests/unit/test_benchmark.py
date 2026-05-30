"""Unit tests for the fragility benchmark engine.

Pure and deterministic: scoring math, ranking, leaderboard upsert/roundtrip,
and HTML rendering. No subprocess, no network.
"""

from __future__ import annotations

import pytest

from rewind.engines.benchmark import (
    FragilityScore,
    Leaderboard,
    render_leaderboard_html,
    score_results,
)
from rewind.engines.mutate import Mutation, MutationKind, MutationResult


def _result(*, crashed: bool, changed: bool) -> MutationResult:
    m = Mutation(
        kind=MutationKind.DROP_STEP,
        target_order_idx=0,
        description="x",
        _materialiser=lambda *a: "",
    )
    return MutationResult(
        mutation=m, mutated_session_id="s", crashed=crashed, output_changed=changed
    )


@pytest.mark.unit
def test_score_basic_mix() -> None:
    results = [
        _result(crashed=False, changed=False),  # survived
        _result(crashed=False, changed=False),  # survived
        _result(crashed=False, changed=True),  # changed
        _result(crashed=True, changed=True),  # crashed (changed ignored)
    ]
    score = score_results("agent-x", results, git_hash="abc1234")
    assert score.total_mutations == 4
    assert score.survived == 2
    assert score.output_changed == 1
    assert score.crashed == 1
    assert score.fragility_score == 0.5  # (1 changed + 1 crashed) / 4
    assert score.robustness == 0.5
    assert score.git_hash == "abc1234"


@pytest.mark.unit
def test_crashed_not_double_counted_as_changed() -> None:
    # A crashed run usually also has differing output; it must count once.
    score = score_results("a", [_result(crashed=True, changed=True)])
    assert score.crashed == 1
    assert score.output_changed == 0
    assert score.survived == 0
    assert score.fragility_score == 1.0


@pytest.mark.unit
def test_empty_results_is_zero_fragility() -> None:
    score = score_results("a", [])
    assert score.total_mutations == 0
    assert score.fragility_score == 0.0
    assert score.robustness == 1.0


@pytest.mark.unit
def test_all_survived_is_zero_fragility() -> None:
    score = score_results("a", [_result(crashed=False, changed=False) for _ in range(3)])
    assert score.fragility_score == 0.0
    assert score.survived == 3


@pytest.mark.unit
def test_leaderboard_ranks_most_robust_first() -> None:
    board = Leaderboard()
    board.upsert(score_results("fragile", [_result(crashed=True, changed=False)]))  # 1.0
    board.upsert(score_results("robust", [_result(crashed=False, changed=False)]))  # 0.0
    board.upsert(
        score_results(
            "mid", [_result(crashed=True, changed=False), _result(crashed=False, changed=False)]
        )
    )  # 0.5
    ranked = board.ranked()
    assert [s.agent_name for s in ranked] == ["robust", "mid", "fragile"]


@pytest.mark.unit
def test_leaderboard_upsert_replaces_same_agent() -> None:
    board = Leaderboard()
    board.upsert(score_results("a", [_result(crashed=True, changed=False)]))  # 1.0
    board.upsert(score_results("a", [_result(crashed=False, changed=False)]))  # 0.0
    assert len(board.scores) == 1
    assert board.scores[0].fragility_score == 0.0


@pytest.mark.unit
def test_leaderboard_json_roundtrip() -> None:
    board = Leaderboard()
    board.upsert(score_results("a", [_result(crashed=True, changed=False)], git_hash="deadbee"))
    board.upsert(score_results("b", [_result(crashed=False, changed=True)]))
    restored = Leaderboard.from_json(board.to_json())
    assert {s.agent_name for s in restored.scores} == {"a", "b"}
    a = next(s for s in restored.scores if s.agent_name == "a")
    assert a.git_hash == "deadbee"
    assert a.fragility_score == 1.0


@pytest.mark.unit
def test_render_html_includes_agents_and_escapes() -> None:
    board = Leaderboard()
    board.upsert(score_results("<script>", [_result(crashed=False, changed=False)]))
    html = render_leaderboard_html(board)
    assert "Agent Fragility Leaderboard" in html
    assert "&lt;script&gt;" in html  # escaped, not raw
    assert "<script>" not in html.split("<title>")[1]  # no raw injection in body


@pytest.mark.unit
def test_fragility_score_from_json_handles_missing_git_hash() -> None:
    d = {
        "agent_name": "x",
        "total_mutations": 2,
        "survived": 1,
        "output_changed": 1,
        "crashed": 0,
        "fragility_score": 0.5,
        "git_hash": None,
    }
    score = FragilityScore.from_json(d)
    assert score.git_hash is None
    assert score.fragility_score == 0.5
