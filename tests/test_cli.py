"""Tests for cli.py: subcommand dispatch and dry-run behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openrouter_free_monitor import cli


def test_init_creates_db_file(tmp_path: Path, capsys) -> None:
    db = tmp_path / "snapshots.db"
    args = cli._build_parser().parse_args(["init"])
    args.db = str(db)
    rc = cli.cmd_init(args, cli.load_config())
    assert rc == 0
    assert db.exists()
    captured = capsys.readouterr()
    assert "Initialized SQLite store" in captured.out


def test_status_empty_store(tmp_path: Path, capsys) -> None:
    db = tmp_path / "snapshots.db"
    args = cli._build_parser().parse_args(["init"])
    args.db = str(db)
    cli.cmd_init(args, cli.load_config())
    args = cli._build_parser().parse_args(["status"])
    args.db = str(db)
    rc = cli.cmd_status(args, cli.load_config())
    assert rc == 0
    captured = capsys.readouterr()
    assert "No snapshots recorded" in captured.out


def test_config_command_hides_secrets(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-supersecret12345")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    args = cli._build_parser().parse_args(["config"])
    args.db = str(tmp_path / "snapshots.db")
    rc = cli.cmd_config(args, cli.load_config())
    assert rc == 0
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["api_key_set"] is True
    assert body["api_key_prefix"].startswith("sk-or-v")
    assert "supersecret" not in captured.out
