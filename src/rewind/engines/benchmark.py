"""Fragility benchmark — turn a mutation run into a comparable score.

`rewind mutate` tells you, for one agent, which fault injections it survives.
The benchmark turns that into a single number — a **fragility score** — so
different agents (or the same agent across commits) can be ranked on a
leaderboard. The intended use is a weekly CI job that mutation-tests a set of
recorded agents and publishes the board, so regressions in robustness show up
as a rising score.

Definitions (all derived from `MutationResult`s, no live calls):

* **survived**        — agent produced byte-identical output under the mutation
* **output_changed**  — output differed (the fault changed behaviour)
* **crashed**         — agent exited non-zero or timed out
* **fragility_score** — ``(output_changed + crashed) / total``, in ``[0, 1]``.
  0.0 = nothing perturbed its behaviour (most robust); 1.0 = every fault did.
* **robustness**      — ``1 - fragility_score``.

Lower fragility is better. The score is a measurement of observed behaviour
under the mutation set, not an estimate — but it is only as meaningful as the
oracle (`mutate` uses stdout equality; see its caveat) and the mutation set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rewind.engines.mutate import MutationResult


@dataclass
class FragilityScore:
    agent_name: str
    total_mutations: int
    survived: int
    output_changed: int
    crashed: int
    fragility_score: float
    git_hash: str | None = None
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def robustness(self) -> float:
        return round(1.0 - self.fragility_score, 4)

    def to_json(self) -> dict[str, object]:
        return {
            "agent_name": self.agent_name,
            "total_mutations": self.total_mutations,
            "survived": self.survived,
            "output_changed": self.output_changed,
            "crashed": self.crashed,
            "fragility_score": self.fragility_score,
            "git_hash": self.git_hash,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_json(cls, d: dict[str, object]) -> FragilityScore:
        return cls(
            agent_name=str(d["agent_name"]),
            total_mutations=_to_int(d["total_mutations"]),
            survived=_to_int(d["survived"]),
            output_changed=_to_int(d["output_changed"]),
            crashed=_to_int(d["crashed"]),
            fragility_score=_to_float(d["fragility_score"]),
            git_hash=str(d["git_hash"]) if d.get("git_hash") is not None else None,
            recorded_at=str(d.get("recorded_at") or datetime.now(UTC).isoformat()),
        )


def _to_int(v: object) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError(f"expected an int-like value, got {type(v).__name__}")
    return int(v)


def _to_float(v: object) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError(f"expected a float-like value, got {type(v).__name__}")
    return float(v)


def score_results(
    agent_name: str,
    results: list[MutationResult],
    *,
    git_hash: str | None = None,
) -> FragilityScore:
    """Reduce a list of MutationResults to a single FragilityScore."""
    total = len(results)
    crashed = sum(1 for r in results if r.crashed)
    # A crashed run is counted as crashed only; output_changed is the set of
    # runs that finished but produced different output.
    output_changed = sum(1 for r in results if not r.crashed and r.output_changed)
    survived = total - crashed - output_changed
    fragility = round((output_changed + crashed) / total, 4) if total else 0.0
    return FragilityScore(
        agent_name=agent_name,
        total_mutations=total,
        survived=survived,
        output_changed=output_changed,
        crashed=crashed,
        fragility_score=fragility,
        git_hash=git_hash,
    )


@dataclass
class Leaderboard:
    scores: list[FragilityScore] = field(default_factory=list)

    def ranked(self) -> list[FragilityScore]:
        """Most robust first (lowest fragility). Ties broken by agent name."""
        return sorted(self.scores, key=lambda s: (s.fragility_score, s.agent_name))

    def upsert(self, score: FragilityScore) -> None:
        """Insert the score, replacing any prior entry for the same agent.

        Weekly runs re-score the same agents; the board keeps the latest result
        per agent rather than accumulating duplicates.
        """
        self.scores = [s for s in self.scores if s.agent_name != score.agent_name]
        self.scores.append(score)

    def to_json(self) -> dict[str, object]:
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "scores": [s.to_json() for s in self.ranked()],
        }

    @classmethod
    def from_json(cls, d: dict[str, object]) -> Leaderboard:
        raw = d.get("scores", [])
        scores: list[FragilityScore] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    scores.append(FragilityScore.from_json(item))
        return cls(scores=scores)


def render_leaderboard_html(board: Leaderboard) -> str:
    """Render the leaderboard as a self-contained static HTML page."""
    rows: list[str] = []
    for rank, s in enumerate(board.ranked(), start=1):
        rows.append(
            "<tr>"
            f"<td>{rank}</td>"
            f"<td>{_esc(s.agent_name)}</td>"
            f"<td>{s.fragility_score:.2%}</td>"
            f"<td>{s.robustness:.2%}</td>"
            f"<td>{s.survived}</td>"
            f"<td>{s.output_changed}</td>"
            f"<td>{s.crashed}</td>"
            f"<td>{s.total_mutations}</td>"
            f"<td><code>{_esc(s.git_hash or '—')}</code></td>"
            f"<td>{_esc(s.recorded_at)}</td>"
            "</tr>"
        )
    generated = datetime.now(UTC).isoformat()
    return _HTML_TEMPLATE.format(rows="\n".join(rows), generated=_esc(generated))


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rewind Agent Fragility Leaderboard</title>
<style>
  body {{
    font-family: system-ui, sans-serif;
    max-width: 60rem;
    margin: 2rem auto;
    padding: 0 1rem;
  }}
  h1 {{ margin-bottom: 0.2rem; }}
  .sub {{ color: #666; margin-top: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1.5rem; }}
  th, td {{
    text-align: left;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid #e2e2e2;
  }}
  th {{ background: #fafafa; }}
  tr:first-child td {{ font-weight: 600; }}
  code {{ font-size: 0.85em; }}
  footer {{ margin-top: 2rem; color: #888; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>Agent Fragility Leaderboard</h1>
<p class="sub">Lower fragility is better. Fragility = share of injected faults
that changed the agent's behaviour or crashed it.
Generated by <code>rewind benchmark</code>.</p>
<table>
<thead>
<tr><th>#</th><th>Agent</th><th>Fragility</th><th>Robustness</th><th>Survived</th>
<th>Changed</th><th>Crashed</th><th>Mutations</th><th>Commit</th><th>Recorded</th></tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
<footer>Generated {generated} · rewind</footer>
</body>
</html>
"""
