"""Unit tests for cli.py helpers — port-readiness probe and command serialization."""

from __future__ import annotations

import asyncio
import socket

import pytest

from rewind.cli import _serialize_command, _split_command, _wait_for_proxy_bound


@pytest.mark.unit
def test_serialize_then_split_roundtrip_basic() -> None:
    argv = ["python", "agent.py"]
    assert _split_command(_serialize_command(argv)) == argv


@pytest.mark.unit
def test_serialize_then_split_roundtrip_path_with_spaces() -> None:
    argv = ["C:\\Program Files\\Python\\python.exe", "my agent.py", "--flag", "value with space"]
    assert _split_command(_serialize_command(argv)) == argv


@pytest.mark.unit
def test_serialize_then_split_roundtrip_quotes_in_args() -> None:
    argv = ["python", "-c", 'print("hello")']
    assert _split_command(_serialize_command(argv)) == argv


@pytest.mark.unit
def test_backcompat_split_legacy_space_joined_command() -> None:
    # Old sessions stored a space-joined string. shlex must still parse those.
    assert _split_command("python agent.py") == ["python", "agent.py"]


@pytest.mark.unit
def test_split_command_falls_back_when_json_is_not_a_list_of_strings() -> None:
    # JSON-shaped but not a list-of-strings: treat as legacy shell string.
    assert _split_command("[1, 2, 3]") == ["[1,", "2,", "3]"]


@pytest.mark.unit
def test_wait_for_proxy_returns_when_port_bound() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        asyncio.run(_wait_for_proxy_bound("127.0.0.1", port, timeout=2.0))
    finally:
        s.close()


@pytest.mark.unit
def test_wait_for_proxy_times_out_when_nothing_listens() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(TimeoutError, match="did not bind"):
        asyncio.run(_wait_for_proxy_bound("127.0.0.1", port, timeout=0.3))
