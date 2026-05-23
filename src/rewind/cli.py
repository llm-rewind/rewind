"""Rewind CLI — all commands. Implementations added phase by phase."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

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

console = Console()


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
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
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
    mitm_pem.chmod(0o600)

    CA_CERT_PATH.write_bytes(cert_pem)
    CA_KEY_PATH.write_bytes(key_pem)
    CA_KEY_PATH.chmod(0o600)


@click.group()
@click.version_option()
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
        command=" ".join(command),
        git_hash=_get_git_hash(),
    )
    db.save_session(session)

    stop = asyncio.Event()
    proxy_task = asyncio.create_task(
        run_record_proxy(db, blobs, session.id, port=port, _stop=stop)
    )
    await asyncio.sleep(0.8)  # wait for proxy to bind

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

    t0 = time.monotonic()
    proc = subprocess.run(list(command), env=env, check=False)
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

    icon = (
        "[green]✓[/green]"
        if proc.returncode == 0
        else f"[red]✗ exit {proc.returncode}[/red]"
    )
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
    console.print("[yellow]not implemented yet[/yellow]")


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
    console.print("[yellow]not implemented yet[/yellow]")


@cli.command()
@click.argument("session_id_a")
@click.argument("session_id_b")
def bisect(session_id_a: str, session_id_b: str) -> None:
    """Find the exact step where two sessions diverged."""
    console.print("[yellow]not implemented yet[/yellow]")


@cli.command(name="export")
@click.argument("session_id")
@click.option("--output", default=None, help="Output path (default: <session_id[:8]>.rw)")
def export_session(session_id: str, output: str | None) -> None:
    """Export a session as a portable cassette file (.rw)."""
    console.print("[yellow]not implemented yet[/yellow]")


@cli.command(name="import")
@click.argument("path")
def import_session(path: str) -> None:
    """Import a cassette file (.rw) into the local database."""
    console.print("[yellow]not implemented yet[/yellow]")


@cli.command()
@click.option("--days", default=7, show_default=True, help="Number of days to include")
def stats(days: int) -> None:
    """Show cost analytics for recent sessions."""
    console.print("[yellow]not implemented yet[/yellow]")


if __name__ == "__main__":
    cli()
