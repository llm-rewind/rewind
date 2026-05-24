"""Rewind CLI — all commands. Implementations added phase by phase."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rewind.engines.mutate import MutationResult

import click
from rich.console import Console

from rewind.constants import (
    CA_CERT_DAYS,
    CA_CERT_PATH,
    CA_COMMON_NAME,
    CA_KEY_BITS,
    CA_KEY_PATH,
    DEFAULT_PROXY_PORT,
    MODEL_COSTS,
    REWIND_DIR,
)
from rewind.storage.db import Session, Step

# Windows console defaults to cp1252 which can't encode unicode glyphs (✓, ●, →).
# Reconfigure stdio to UTF-8 before Rich initialises its renderer.
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

console = Console()


async def _wait_for_proxy_bound(host: str, port: int, timeout: float = 10.0) -> None:
    """Poll the proxy TCP port until it accepts connections.

    The old code slept 0.8s and hoped. On a cold start that races with the
    subprocess and silently drops the first request, which is exactly the
    sort of thing the test suite cannot catch but a user hits on the first run.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    last_err: OSError | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            return
        except OSError as e:
            last_err = e
            await asyncio.sleep(0.05)
    raise TimeoutError(
        f"Proxy on {host}:{port} did not bind within {timeout}s (last error: {last_err})"
    )


def _serialize_command(command: tuple[str, ...] | list[str]) -> str:
    """Serialize argv as JSON for storage in the sessions.command column.

    Using JSON instead of shell-joining means paths with spaces, quotes, and
    backslashes round-trip exactly the same on every platform. The previous
    implementation used " ".join then .split() which silently corrupted any
    Windows path containing "Program Files".
    """
    import json

    return json.dumps(list(command))


def _split_command(command_str: str) -> list[str]:
    """Parse a stored command back into argv.

    New sessions store the command as a JSON-encoded list. Older sessions
    (pre-v0.2.0) stored a space-joined string, so fall back to shlex with the
    platform-appropriate posix flag when JSON parsing fails. POSIX shlex
    treats backslash as escape which mangles Windows paths, so non-POSIX
    mode is used on Windows.
    """
    import json

    s = command_str.strip()
    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                return parsed
        except json.JSONDecodeError:
            pass
    return shlex.split(command_str, posix=os.name != "nt")


def _get_git_hash() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _estimate_cost(steps: list[Step]) -> float:
    total = 0.0
    for step in steps:
        if step.model is not None and step.model in MODEL_COSTS:
            inp, out = MODEL_COSTS[step.model]
            total += step.input_tok * inp / 1000 + step.output_tok * out / 1000
    return total


def _generate_ca(rewind_dir: Path) -> None:
    """Generate mitmproxy-compatible CA cert + key. Writes three files to rewind_dir."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=CA_KEY_BITS)
    public_key = key.public_key()
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME)])
    # Python 3.13's stricter X.509 verifier rejects chains where the trust
    # anchor lacks a SubjectKeyIdentifier extension ("Missing Subject Key
    # Identifier"). Without SKI mitmproxy will still issue per-host certs,
    # but no modern client will validate them, and the whole HTTPS
    # interception path silently fails with a ConnectError. RFC 5280
    # recommends SKI on every CA cert anyway.
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=CA_CERT_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(public_key),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    # mitmproxy expects key+cert concatenated in mitmproxy-ca.pem
    mitm_pem = rewind_dir / "mitmproxy-ca.pem"
    mitm_pem.write_bytes(key_pem + cert_pem)
    _restrict_to_owner(mitm_pem)

    CA_CERT_PATH.write_bytes(cert_pem)
    CA_KEY_PATH.write_bytes(key_pem)
    _restrict_to_owner(CA_KEY_PATH)


def _restrict_to_owner(path: Path) -> None:
    """Restrict file permissions to the current user.

    On POSIX, chmod 600. On Windows, replace the file's DACL with one
    that grants Full Control to the current user only. Plain chmod is a
    silent no-op on Windows and would leave the CA private key
    world-readable. We use icacls because cryptography for setting NTFS
    ACLs from Python is more dependency than this single call deserves.
    """
    if os.name == "nt":
        try:
            user = os.environ.get("USERNAME") or os.environ.get("USER") or "%USERNAME%"
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            # icacls failure does not abort init; the file is at least in the
            # user's profile directory and a warning is the right level here.
            pass
    else:
        path.chmod(0o600)


@click.group()
@click.version_option(package_name="llm-rewind")
def cli() -> None:
    """Rewind — time-travel debugger for AI agents.

    Record any production run, replay any failure locally at zero LLM cost.
    """


@cli.command()
def init() -> None:
    """Initialize Rewind: generate CA cert and create local database."""
    REWIND_DIR.mkdir(parents=True, exist_ok=True)

    from rewind.storage.blobs import BlobStore
    from rewind.storage.db import RewindDB

    BlobStore()
    RewindDB.get_or_create()

    ca_pem = REWIND_DIR / "mitmproxy-ca.pem"
    if ca_pem.exists():
        console.print("[yellow]Already initialized.[/yellow] ~/.rewind/ exists.")
        console.print(f"CA cert: [cyan]{CA_CERT_PATH}[/cyan]")
        return

    console.print("Generating CA certificate (4096-bit RSA, ~5 seconds)…")
    _generate_ca(REWIND_DIR)
    console.print("[green]✓[/green] Rewind initialized at [cyan]~/.rewind/[/cyan]")
    console.print("\nTrust the CA cert so HTTPS interception works:")
    console.print(f"  [cyan]{CA_CERT_PATH}[/cyan]\n")

    if sys.platform == "darwin":
        console.print("macOS:")
        console.print(
            f"  sudo security add-trusted-cert -d -r trustRoot"
            f" -k /Library/Keychains/System.keychain {CA_CERT_PATH}"
        )
    elif sys.platform == "win32":
        console.print("Windows (run as Administrator):")
        console.print(f'  certutil -addstore -f "ROOT" {CA_CERT_PATH}')
    else:
        console.print("Linux:")
        console.print(
            f"  sudo cp {CA_CERT_PATH} /usr/local/share/ca-certificates/rewind-ca.crt"
            f" && sudo update-ca-certificates"
        )


@cli.command()
@click.argument("command", nargs=-1, required=True)
@click.option("--name", "-n", default=None, help="Agent name label for this session")
@click.option(
    "--port",
    "-p",
    default=DEFAULT_PROXY_PORT,
    show_default=True,
    help="Proxy listen port",
)
def record(command: tuple[str, ...], name: str | None, port: int) -> None:
    """Record an agent run. All LLM calls are captured as a local cassette.

    Example: rewind record python my_agent.py
    """
    if not (REWIND_DIR / "mitmproxy-ca.pem").exists():
        console.print("[red]Not initialized.[/red] Run [cyan]rewind init[/cyan] first.")
        raise SystemExit(1)
    asyncio.run(_record_async(command, name, port))


async def _record_async(command: tuple[str, ...], name: str | None, port: int) -> None:
    from rewind.proxy.addon import run_record_proxy
    from rewind.storage.blobs import BlobStore
    from rewind.storage.db import RewindDB

    db = RewindDB.get_or_create()
    blobs = BlobStore()
    session = Session(
        agent_name=name or Path(command[0]).stem,
        command=_serialize_command(command),
        git_hash=_get_git_hash(),
    )
    db.save_session(session)

    stop = asyncio.Event()
    proxy_task = asyncio.create_task(run_record_proxy(db, blobs, session.id, port=port, _stop=stop))
    await _wait_for_proxy_bound("127.0.0.1", port)

    console.print(f"[green]●[/green] Recording [cyan]{session.id[:8]}[/cyan]")
    console.print(f"  Proxy:   [dim]http://127.0.0.1:{port}[/dim]")
    console.print(f"  Command: [dim]{' '.join(command)}[/dim]\n")

    env = {
        **os.environ,
        "HTTPS_PROXY": f"http://127.0.0.1:{port}",
        "HTTP_PROXY": f"http://127.0.0.1:{port}",
        "SSL_CERT_FILE": str(CA_CERT_PATH),
        "REQUESTS_CA_BUNDLE": str(CA_CERT_PATH),
    }

    # Critical: must NOT use subprocess.run() here. That call blocks the
    # asyncio event loop, which means the mitmproxy task running on the
    # same loop cannot process any incoming requests for the entire
    # duration of the subprocess. The agent's HTTPS calls would hit the
    # proxy port, complete the TCP handshake, then hang because the loop
    # never wakes up to handle the CONNECT. asyncio.create_subprocess_exec
    # cooperates with the loop, so the proxy keeps serving while the
    # subprocess runs.
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(*command, env=env)
    returncode = await proc.wait()
    elapsed = time.monotonic() - t0

    stop.set()
    try:
        await asyncio.wait_for(proxy_task, timeout=3.0)
    except TimeoutError:
        proxy_task.cancel()

    steps = db.get_steps(session.id)
    total_cost = _estimate_cost(steps)
    session.ended_at = datetime.now(UTC)
    session.total_cost_usd = total_cost
    db.save_session(session)

    icon = "[green]✓[/green]" if returncode == 0 else f"[red]✗ exit {returncode}[/red]"
    console.print(
        f"\n{icon} Captured [cyan]{len(steps)}[/cyan] LLM call(s)"
        f" — ~${total_cost:.4f} | {elapsed:.1f}s"
    )
    console.print(f"  [dim]rewind inspect {session.id[:8]}[/dim]")


@cli.command()
@click.argument("session_id")
@click.option("--permissive", is_flag=True, help="Allow cassette misses (costs real tokens)")
@click.option("--command", "override_command", default=None, help="Override stored command")
@click.option("--port", default=DEFAULT_PROXY_PORT, show_default=True, help="Proxy listen port")
def replay(session_id: str, permissive: bool, override_command: str | None, port: int) -> None:
    """Replay a recorded session at zero LLM cost."""
    asyncio.run(_replay_async(session_id, permissive, override_command, port))


async def _replay_async(
    session_id: str, permissive: bool, override_command: str | None, port: int
) -> None:
    from rewind.exceptions import RewindSessionNotFoundError
    from rewind.proxy.addon import run_replay_proxy
    from rewind.storage.blobs import BlobStore
    from rewind.storage.db import RewindDB

    db = RewindDB.get_or_create()
    session = db.get_session(session_id)
    if session is None:
        raise RewindSessionNotFoundError(session_id)

    blobs = BlobStore()
    command_str = override_command or session.command or ""
    if not command_str:
        console.print(
            "[red]No command stored for this session.[/red] "
            "Use [cyan]--command[/cyan] to specify one."
        )
        raise SystemExit(1)

    steps = db.get_steps(session.id)
    mode_label = "[yellow]permissive[/yellow]" if permissive else "[green]strict[/green]"

    stop = asyncio.Event()
    proxy_task = asyncio.create_task(
        run_replay_proxy(db, blobs, session.id, port=port, permissive=permissive, _stop=stop)
    )
    await _wait_for_proxy_bound("127.0.0.1", port)

    console.print(f"[green]▶[/green] Replaying [cyan]{session.id[:8]}[/cyan] ({mode_label} mode)")
    console.print(f"  Cassette: [dim]{len(steps)} step(s)[/dim]")
    console.print(f"  Proxy:    [dim]http://127.0.0.1:{port}[/dim]")
    console.print(f"  Command:  [dim]{command_str}[/dim]\n")

    env = {
        **os.environ,
        "HTTPS_PROXY": f"http://127.0.0.1:{port}",
        "HTTP_PROXY": f"http://127.0.0.1:{port}",
        "SSL_CERT_FILE": str(CA_CERT_PATH),
        "REQUESTS_CA_BUNDLE": str(CA_CERT_PATH),
        "REWIND_MODE": "replay",
    }

    # Same loop-blocking rule as record: must use async subprocess so the
    # ReplayAddon task keeps serving cassette bytes while the agent runs.
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(*_split_command(command_str), env=env)
    returncode = await proc.wait()
    elapsed = time.monotonic() - t0

    stop.set()
    try:
        await asyncio.wait_for(proxy_task, timeout=3.0)
    except TimeoutError:
        proxy_task.cancel()

    icon = "[green]✓[/green]" if returncode == 0 else f"[red]✗ exit {returncode}[/red]"
    console.print(f"\n{icon} Replay complete — {elapsed:.1f}s (zero LLM cost)")


@cli.command(name="list")
@click.option("--limit", default=20, show_default=True, help="Number of sessions to show")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON array")
def list_sessions(limit: int, as_json: bool) -> None:
    """List recorded sessions."""
    import json as _json

    from rich.table import Table

    from rewind.storage.db import RewindDB

    db = RewindDB.get_or_create()
    sessions = db.list_sessions(limit=limit)

    if not sessions:
        console.print("[dim]No sessions recorded yet. Run [cyan]rewind record[/cyan] first.[/dim]")
        return

    if as_json:
        data = [
            {
                "id": s.id,
                "agent_name": s.agent_name,
                "git_hash": s.git_hash,
                "command": s.command,
                "started_at": s.started_at.isoformat(),
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "total_cost_usd": s.total_cost_usd,
                "steps": db.count_steps(s.id),
            }
            for s in sessions
        ]
        console.print(_json.dumps(data, indent=2))
        return

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("ID", style="cyan", width=9)
    table.add_column("Agent", max_width=20)
    table.add_column("Started", style="dim", width=19)
    table.add_column("Steps", justify="right", width=5)
    table.add_column("Cost", justify="right", width=8)
    table.add_column("Git", style="dim", width=8)

    for s in sessions:
        steps = db.count_steps(s.id)
        started = s.started_at.strftime("%Y-%m-%d %H:%M:%S")
        cost = f"${s.total_cost_usd:.4f}" if s.total_cost_usd else "—"
        git = (s.git_hash or "—")[:7]
        table.add_row(s.id[:8], s.agent_name, started, str(steps), cost, git)

    console.print(table)


@cli.command()
@click.argument("session_id")
@click.option("--verbose", is_flag=True, help="Show match_key for each step")
def inspect(session_id: str, verbose: bool) -> None:
    """Inspect a recorded session in detail."""
    from rich.table import Table

    from rewind.exceptions import RewindSessionNotFoundError
    from rewind.storage.db import RewindDB

    db = RewindDB.get_or_create()
    session = db.get_session(session_id)
    if session is None:
        raise RewindSessionNotFoundError(session_id)

    started = session.started_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    if session.ended_at:
        ended = session.ended_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        delta = session.ended_at - session.started_at
        duration = f"{delta.total_seconds():.1f}s"
    else:
        ended = "[yellow]running[/yellow]"
        duration = "—"

    console.print(f"\n[bold]Session[/bold] [cyan]{session.id[:8]}[/cyan]")
    console.print(f"  Agent:   {session.agent_name}")
    if session.git_hash:
        console.print(f"  Git:     [dim]{session.git_hash}[/dim]")
    if session.command:
        console.print(f"  Command: [dim]{session.command}[/dim]")
    console.print(f"  Started: {started}")
    console.print(f"  Ended:   {ended}  ({duration})")
    console.print(f"  Cost:    ~${session.total_cost_usd:.4f} (estimated)")

    steps = db.get_steps(session.id)
    if not steps:
        console.print("\n[dim]No steps recorded.[/dim]")
        return

    console.print(f"  Steps:   {len(steps)}\n")

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("#", justify="right", width=3)
    table.add_column("Type", width=10)
    table.add_column("Provider", width=10)
    table.add_column("Model", width=22)
    table.add_column("In", justify="right", width=6)
    table.add_column("Out", justify="right", width=6)
    table.add_column("ms", justify="right", width=6)
    if verbose:
        table.add_column("match_key", width=16)

    for step in steps:
        row = [
            str(step.order_idx),
            step.type,
            step.provider or "—",
            step.model or "—",
            str(step.input_tok) if step.input_tok else "—",
            str(step.output_tok) if step.output_tok else "—",
            str(step.latency_ms) if step.latency_ms else "—",
        ]
        if verbose:
            row.append((step.match_key or "")[:16])
        table.add_row(*row)

    console.print(table)


@cli.command()
@click.argument("session_id_a")
@click.argument("session_id_b")
def diff(session_id_a: str, session_id_b: str) -> None:
    """Diff two recorded sessions step by step."""
    from rich.table import Table

    from rewind.engines.diff import DiffStatus, diff_sessions
    from rewind.storage.blobs import BlobStore
    from rewind.storage.db import RewindDB

    db = RewindDB.get_or_create()
    blobs = BlobStore()
    result = diff_sessions(db, blobs, session_id_a, session_id_b)

    if result.is_identical:
        console.print(
            f"[green]✓[/green] Sessions [cyan]{session_id_a[:8]}[/cyan] and "
            f"[cyan]{session_id_b[:8]}[/cyan] are [bold]identical[/bold]."
        )
        return

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("#", justify="right", width=3)
    table.add_column("Status", width=10)
    table.add_column("Model A", width=22)
    table.add_column("Model B", width=22)
    table.add_column("Resp A", width=12, style="dim")
    table.add_column("Resp B", width=12, style="dim")

    status_style = {
        DiffStatus.MATCH: "dim",
        DiffStatus.DIVERGED: "red",
        DiffStatus.ADDED: "yellow",
        DiffStatus.REMOVED: "yellow",
    }

    for sd in result.steps:
        style = status_style[sd.status]
        model_a = (sd.step_a.model or "—") if sd.step_a else "—"
        model_b = (sd.step_b.model or "—") if sd.step_b else "—"
        blob_a = (sd.step_a.resp_blob or "")[:10] if sd.step_a else "—"
        blob_b = (sd.step_b.resp_blob or "")[:10] if sd.step_b else "—"
        table.add_row(
            str(sd.order_idx),
            f"[{style}]{sd.status.value}[/{style}]",
            model_a,
            model_b,
            blob_a,
            blob_b,
        )

    console.print(table)
    first = result.first_divergence
    if first:
        console.print(
            f"\nFirst divergence at step [cyan]{first.order_idx}[/cyan]. "
            f"Run [cyan]rewind bisect {session_id_a[:8]} {session_id_b[:8]}[/cyan] for root cause."
        )


@cli.command()
@click.argument("session_id_a")
@click.argument("session_id_b")
def bisect(session_id_a: str, session_id_b: str) -> None:
    """Find the exact step where two sessions diverged."""
    from rewind.engines.bisect import bisect_sessions
    from rewind.storage.blobs import BlobStore
    from rewind.storage.db import RewindDB

    db = RewindDB.get_or_create()
    blobs = BlobStore()
    result = bisect_sessions(db, blobs, session_id_a, session_id_b)
    console.print(result.summary())


@cli.command()
@click.argument("session_id")
@click.option(
    "--command",
    "override_command",
    default=None,
    help="Override stored command (must be the same agent that produced the session)",
)
@click.option(
    "--port",
    default=DEFAULT_PROXY_PORT,
    show_default=True,
    help="Proxy listen port",
)
@click.option(
    "--timeout",
    default=60,
    show_default=True,
    help="Per-mutation subprocess timeout in seconds",
)
@click.option(
    "--keep-sessions",
    is_flag=True,
    default=False,
    help="Keep mutated sessions in the local DB after the run (default: delete them)",
)
def mutate(
    session_id: str,
    override_command: str | None,
    port: int,
    timeout: int,
    keep_sessions: bool,
) -> None:
    """Mutation-test an agent against a recorded session.

    Systematically perturbs the cassette (drops steps, replaces responses
    with errors, truncates, returns 429/500) and re-runs the agent
    against each mutation. Reports which mutations the agent survives
    and which expose fragility. The novel piece: tells you where your
    agent will silently misbehave when production drifts.

    Caveats: the survival oracle is stdout equality, so an agent that
    prints the same thing while doing the wrong thing under the hood
    is reported as SURVIVED. Capture the agent's actual side effects
    (writes, API calls, return values) if you need a stronger oracle.
    """
    asyncio.run(_mutate_async(session_id, override_command, port, timeout, keep_sessions))


async def _mutate_async(
    session_id: str,
    override_command: str | None,
    port: int,
    timeout: int,
    keep_sessions: bool,
) -> None:
    from rewind.engines.mutate import (
        MutationResult,
        delete_mutated_sessions,
        generate_mutations,
    )
    from rewind.exceptions import RewindSessionNotFoundError
    from rewind.storage.blobs import BlobStore
    from rewind.storage.db import RewindDB

    db = RewindDB.get_or_create()
    blobs = BlobStore()

    session = db.get_session(session_id)
    if session is None:
        raise RewindSessionNotFoundError(session_id)
    command_str = override_command or session.command or ""
    if not command_str:
        console.print("[red]No command stored.[/red] Use --command to specify one.")
        raise SystemExit(1)

    console.print(f"[cyan]Mutation testing session {session.id[:8]}[/cyan]")
    console.print(f"  Command: [dim]{command_str}[/dim]\n")

    # Baseline: run the agent against the unmutated cassette so we know
    # what "correct" output looks like.
    console.print("[dim]Running baseline replay...[/dim]")
    baseline_code, baseline_out = await _run_replay(
        db, blobs, session.id, command_str, port=_alloc_port(port), timeout=timeout
    )
    if baseline_code != 0:
        console.print(
            f"[yellow]Baseline exits non-zero ({baseline_code}). "
            "Mutation results may be hard to interpret.[/yellow]\n"
        )

    mutations = list(generate_mutations(db, session.id))
    if not mutations:
        console.print("[yellow]No mutable steps in this session.[/yellow]")
        return

    console.print(f"Running {len(mutations)} mutations...\n")

    results: list[MutationResult] = []
    try:
        for m in mutations:
            new_id = m.apply(db, blobs, session.id)
            # Allocate a fresh port per mutation. Reusing the same port
            # races against TIME_WAIT on the previous proxy's socket and
            # produces "Address already in use" mid-run.
            rc, out = await _run_replay(
                db, blobs, new_id, command_str, port=_alloc_port(port), timeout=timeout
            )
            results.append(
                MutationResult(
                    mutation=m,
                    mutated_session_id=new_id,
                    exit_code=rc,
                    stdout=out,
                    crashed=(rc is None or rc != 0),
                    output_changed=(out != baseline_out),
                )
            )

        _print_mutation_report(results)
    finally:
        # Always runs, including on KeyboardInterrupt mid-loop, so the DB
        # does not accumulate orphaned mutated sessions on abort.
        if not keep_sessions:
            removed = delete_mutated_sessions(db, session.id)
            if removed:
                console.print(
                    f"\n[dim]Cleaned up {removed} mutated session(s) "
                    f"(pass --keep-sessions to retain).[/dim]"
                )


def _alloc_port(fallback: int) -> int:
    """Return an unused TCP port, or `fallback` if probing fails."""
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
        s.close()
        return port
    except OSError:
        return fallback


async def _run_replay(
    db: object,
    blobs: object,
    session_id: str,
    command_str: str,
    *,
    port: int,
    timeout: int,
) -> tuple[int | None, str]:
    """Run replay for one cassette and return (exit_code, captured stdout)."""
    from rewind.proxy.addon import run_replay_proxy

    stop = asyncio.Event()
    proxy_task = asyncio.create_task(
        run_replay_proxy(
            db,  # type: ignore[arg-type]
            blobs,  # type: ignore[arg-type]
            session_id,
            port=port,
            permissive=False,
            _stop=stop,
        )
    )
    try:
        await _wait_for_proxy_bound("127.0.0.1", port)
        env = {
            **os.environ,
            "HTTPS_PROXY": f"http://127.0.0.1:{port}",
            "HTTP_PROXY": f"http://127.0.0.1:{port}",
            "SSL_CERT_FILE": str(CA_CERT_PATH),
            "REQUESTS_CA_BUNDLE": str(CA_CERT_PATH),
            "REWIND_MODE": "replay",
        }
        proc = await asyncio.create_subprocess_exec(
            *_split_command(command_str),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return None, ""
        return proc.returncode, stdout_b.decode("utf-8", errors="replace")
    finally:
        stop.set()
        try:
            await asyncio.wait_for(proxy_task, timeout=3.0)
        except TimeoutError:
            proxy_task.cancel()


def _print_mutation_report(results: list[MutationResult]) -> None:
    from rich.table import Table

    from rewind.engines.mutate import MutationResult

    table = Table(title="Mutation Report", show_lines=False)
    table.add_column("Mutation")
    table.add_column("Step", justify="right")
    table.add_column("Outcome")
    table.add_column("Detail")

    survived = 0
    output_changed = 0
    crashed = 0
    for r in results:
        assert isinstance(r, MutationResult)
        if r.crashed:
            outcome = "[red]CRASHED[/red]"
            crashed += 1
        elif r.output_changed:
            outcome = "[yellow]OUTPUT CHANGED[/yellow]"
            output_changed += 1
        else:
            outcome = "[green]SURVIVED[/green]"
            survived += 1
        table.add_row(
            r.mutation.kind.value,
            str(r.mutation.target_order_idx),
            outcome,
            r.mutation.description,
        )

    console.print(table)
    console.print(
        f"\n[green]Survived: {survived}[/green] | "
        f"[yellow]Changed: {output_changed}[/yellow] | "
        f"[red]Crashed: {crashed}[/red] | Total: {len(results)}"
    )
    if crashed:
        console.print(
            "\n[red]Crashed mutations indicate fragility. "
            "The agent does not gracefully handle these failure modes.[/red]"
        )


@cli.command(name="export")
@click.argument("session_id")
@click.option("--output", default=None, help="Output path (default: <session_id[:8]>.rw)")
def export_session(session_id: str, output: str | None) -> None:
    """Export a session as a portable cassette file (.rw)."""
    from rewind.engines.cassette import export_cassette, save_cassette_file
    from rewind.exceptions import RewindSessionNotFoundError
    from rewind.storage.blobs import BlobStore
    from rewind.storage.db import RewindDB

    db = RewindDB()
    blobs = BlobStore()
    session = db.get_session(session_id)
    if session is None:
        raise RewindSessionNotFoundError(session_id)

    cassette = export_cassette(db, blobs, session.id)
    out_path = Path(output) if output else Path(f"{session.id[:8]}.rw")
    save_cassette_file(cassette, out_path)

    step_count = len(cassette["steps"])
    blob_count = len(cassette["blobs"])
    size_kb = out_path.stat().st_size // 1024
    console.print(
        f"[green]Exported[/green] {session.id[:8]}... → {out_path} "
        f"({step_count} steps, {blob_count} blobs, ~{size_kb}KB)"
    )


@cli.command(name="import")
@click.argument("path")
def import_session(path: str) -> None:
    """Import a cassette file (.rw) into the local database."""
    from rewind.engines.cassette import import_cassette, load_cassette_file
    from rewind.storage.blobs import BlobStore
    from rewind.storage.db import RewindDB

    rw_path = Path(path)
    if not rw_path.exists():
        console.print(f"[red]File not found:[/red] {rw_path}")
        raise SystemExit(1)

    db = RewindDB()
    blobs = BlobStore()
    data = load_cassette_file(rw_path)
    session_id = import_cassette(data, db, blobs)

    step_count = len(data.get("steps", []))
    console.print(
        f"[green]Imported[/green] session [bold]{session_id[:8]}...[/bold] ({step_count} steps)"
    )


@cli.command()
@click.option("--days", default=7, show_default=True, help="Number of days to include")
def stats(days: int) -> None:
    """Show cost analytics for recent sessions."""
    from collections import defaultdict

    from rich.panel import Panel
    from rich.table import Table

    from rewind.storage.db import RewindDB

    db = RewindDB()
    since = datetime.now(UTC) - timedelta(days=days)
    all_sessions = db.list_sessions(limit=10000)
    recent = [s for s in all_sessions if s.started_at >= since]

    if not recent:
        console.print(f"[dim]No sessions in the last {days} days.[/dim]")
        return

    # Aggregate per-agent stats
    agent_sessions: dict[str, int] = defaultdict(int)
    agent_steps: dict[str, int] = defaultdict(int)
    agent_input_tok: dict[str, int] = defaultdict(int)
    agent_output_tok: dict[str, int] = defaultdict(int)
    agent_cost: dict[str, float] = defaultdict(float)
    agent_latency_sum: dict[str, int] = defaultdict(int)
    model_counts: dict[str, int] = defaultdict(int)

    total_steps = 0
    total_input = 0
    total_output = 0
    total_cost = 0.0

    for session in recent:
        name = session.agent_name
        agent_sessions[name] += 1
        steps = db.get_steps(session.id)
        for step in steps:
            total_steps += 1
            agent_steps[name] += 1
            agent_input_tok[name] += step.input_tok
            agent_output_tok[name] += step.output_tok
            agent_latency_sum[name] += step.latency_ms
            total_input += step.input_tok
            total_output += step.output_tok
            if step.model:
                model_counts[step.model] += 1
            in_cost, out_cost = MODEL_COSTS.get(step.model or "", (0.0, 0.0))
            step_cost = step.input_tok / 1000 * in_cost + step.output_tok / 1000 * out_cost
            agent_cost[name] += step_cost
            total_cost += step_cost

    most_expensive = max(agent_cost, key=lambda k: agent_cost[k]) if agent_cost else "—"
    most_common_model = max(model_counts, key=lambda k: model_counts[k]) if model_counts else "—"

    summary_lines = [
        f"[bold]Sessions:[/bold] {len(recent)} (last {days} days)",
        f"[bold]LLM steps:[/bold] {total_steps}",
        f"[bold]Tokens:[/bold] {total_input:,} input / {total_output:,} output",
        f"[bold]Estimated cost:[/bold] ${total_cost:.4f}",
        f"[bold]Most expensive agent:[/bold] {most_expensive} "
        f"(${agent_cost.get(most_expensive, 0.0):.4f})",
        f"[bold]Most common model:[/bold] {most_common_model}",
    ]
    console.print(Panel("\n".join(summary_lines), title="Summary", border_style="blue"))

    table = Table(title="Per-Agent Breakdown (estimated costs)", border_style="dim")
    table.add_column("Agent", style="bold")
    table.add_column("Sessions", justify="right")
    table.add_column("Steps", justify="right")
    table.add_column("Input tok", justify="right")
    table.add_column("Output tok", justify="right")
    table.add_column("Est. cost", justify="right")
    table.add_column("Avg latency", justify="right")

    for name in sorted(agent_sessions):
        s_count = agent_steps[name]
        avg_lat = f"{agent_latency_sum[name] // s_count}ms" if s_count else "—"
        table.add_row(
            name,
            str(agent_sessions[name]),
            str(s_count),
            f"{agent_input_tok[name]:,}",
            f"{agent_output_tok[name]:,}",
            f"${agent_cost[name]:.4f}",
            avg_lat,
        )

    console.print(table)


if __name__ == "__main__":
    cli()
