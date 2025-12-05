"""
CLI entrypoint for dsxa-sdk.

Example:
    dsxa --base-url https://scanner --token $TOKEN scan-binary --file sample.docx --metadata App123 --protected-entity 3
"""

from __future__ import annotations

import asyncio
import base64
import pathlib
import time
from dataclasses import dataclass
from typing import List, Optional

import typer
from dotenv import load_dotenv
from rich import print_json

from .client import DSXAClient, AsyncDSXAClient, ScanMode
from .models import ScanResponse

# Load .env automatically so DSXA_BASE_URL / DSXA_AUTH_TOKEN etc. can be stored there.
load_dotenv()

app = typer.Typer(
    help="Command-line interface for DSX Application Scanner REST APIs.",
    no_args_is_help=True,
)


@dataclass
class CLIConfig:
    base_url: str
    auth_token: Optional[str]
    protected_entity: Optional[int]
    verify_tls: bool


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    base_url: str = typer.Option(
        ...,
        "--base-url",
        envvar="DSXA_BASE_URL",
        help="DSXA scanner base URL including scheme (e.g., https://scanner:443). "
        "Set via flag or DSXA_BASE_URL env var / .env file.",
    ),
    auth_token: Optional[str] = typer.Option(
        None,
        "--token",
        envvar="DSXA_AUTH_TOKEN",
        help="Auth token (Bearer). Optional when DSXA accepts anonymous requests. "
        "Set via flag or DSXA_AUTH_TOKEN env var / .env file.",
    ),
    protected_entity: Optional[int] = typer.Option(1, "--protected-entity", envvar="DSXA_PROTECTED_ENTITY"),
    verify_tls: bool = typer.Option(True, "--verify-tls/--no-verify-tls", envvar="DSXA_VERIFY_TLS"),
):
    """
    Capture shared CLI options / environment configuration.
    """
    if ctx.invoked_subcommand is None and ctx.resilient_parsing:
        return
    ctx.obj = CLIConfig(
        base_url=base_url.rstrip("/"),
        auth_token=auth_token,
        protected_entity=int(protected_entity) if protected_entity is not None else 1,
        verify_tls=verify_tls,
    )


def get_client(ctx: typer.Context) -> DSXAClient:
    cfg: CLIConfig = ctx.obj
    return DSXAClient(
        base_url=cfg.base_url,
        auth_token=cfg.auth_token,
        default_protected_entity=cfg.protected_entity,
        verify_tls=cfg.verify_tls,
    )


@app.command("scan-binary")
def scan_binary(
    ctx: typer.Context,
    file: pathlib.Path = typer.Option(..., "--file", exists=True, readable=True, help="Path to the file to scan"),
    custom_metadata: Optional[str] = typer.Option(None, "--metadata"),
    password: Optional[str] = typer.Option(None, "--password", help="Password for encrypted file"),
    base64_header: bool = typer.Option(False, "--base64-header", help="Send via binary endpoint with X-Content-Type: base64"),
):
    """Submit a file in binary mode."""
    client = get_client(ctx)
    with file.open("rb") as fh:
        resp = client.scan_binary(fh.read(), custom_metadata=custom_metadata, password=password, base64_header=base64_header)
    print_scan_response(resp)
    client.close()


@app.command("scan-base64")
def scan_base64(
    ctx: typer.Context,
    file: pathlib.Path = typer.Option(..., "--file", exists=True, readable=True),
    custom_metadata: Optional[str] = typer.Option(None, "--metadata"),
    password: Optional[str] = typer.Option(None, "--password"),
):
    """Submit a file encoded to base64."""
    client = get_client(ctx)
    with file.open("rb") as fh:
        encoded = base64.b64encode(fh.read())
    resp = client.scan_base64(encoded, custom_metadata=custom_metadata, password=password)
    print_scan_response(resp)
    client.close()


@app.command("scan-file")
def scan_file(
    ctx: typer.Context,
    file: pathlib.Path = typer.Option(..., "--file", exists=True, readable=True),
    mode: ScanMode = typer.Option(ScanMode.BINARY, "--mode", case_sensitive=False),
    custom_metadata: Optional[str] = typer.Option(None, "--metadata"),
    password: Optional[str] = typer.Option(None, "--password"),
):
    """Convenience command (auto base64 encoding when mode=base64)."""
    client = get_client(ctx)
    resp = client.scan_file(str(file), mode=mode, custom_metadata=custom_metadata, password=password)
    print_scan_response(resp)
    client.close()


@app.command("scan-hash")
def scan_hash(
    ctx: typer.Context,
    hash_value: str = typer.Option(..., "--hash", help="SHA256 hash to submit"),
    custom_metadata: Optional[str] = typer.Option(None, "--metadata"),
):
    """Submit a hash for reputation scanning."""
    client = get_client(ctx)
    resp = client.scan_hash(hash_value, custom_metadata=custom_metadata)
    print_scan_response(resp)
    client.close()


@app.command("scan-by-path")
def scan_by_path(
    ctx: typer.Context,
    stream_path: str = typer.Option(..., "--stream-path", help="Remote path (Stream-Path header value)"),
    custom_metadata: Optional[str] = typer.Option(None, "--metadata"),
    password: Optional[str] = typer.Option(None, "--password"),
    poll: bool = typer.Option(True, "--poll/--no-poll", help="Poll /result/by_path until verdict != Scanning"),
    interval: float = typer.Option(5.0, "--interval", help="Polling interval seconds"),
    timeout: float = typer.Option(900.0, "--timeout", help="Polling timeout seconds"),
):
    """Initiate scan-by-path and optionally poll until verdict ready."""
    client = get_client(ctx)
    submit = client.scan_by_path(stream_path, custom_metadata=custom_metadata, password=password)
    typer.echo(f"Submitted scan_guid={submit.scan_guid}, verdict={submit.verdict}")
    if poll:
        verdict = client.poll_scan_by_path(submit.scan_guid, interval_seconds=interval, timeout_seconds=timeout)
        print_scan_response(verdict)
    client.close()


@app.command("scan-files")
def scan_files(
    ctx: typer.Context,
    files: List[pathlib.Path] = typer.Argument(..., readable=True, exists=True),
    mode: ScanMode = typer.Option(ScanMode.BINARY, "--mode", case_sensitive=False),
    custom_metadata: Optional[str] = typer.Option(None, "--metadata"),
    password: Optional[str] = typer.Option(None, "--password"),
    concurrency: int = typer.Option(5, "--concurrency", min=1),
):
    """
    Scan one or more explicit file paths concurrently using the async client.
    Example:
        dsxa scan-files dsxa_sdk/tests/assets/samples/* --concurrency 4
    """
    if not files:
        typer.echo("No files specified", err=True)
        raise typer.Exit(code=1)
    asyncio.run(
        _scan_paths(
            ctx,
            files,
            mode=mode,
            custom_metadata=custom_metadata,
            password=password,
            concurrency=concurrency,
        )
    )


@app.command("scan-folder")
def scan_folder(
    ctx: typer.Context,
    folder: pathlib.Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True),
    pattern: str = typer.Option("**/*", "--pattern", help="Glob pattern relative to folder."),
    mode: ScanMode = typer.Option(ScanMode.BINARY, "--mode", case_sensitive=False),
    custom_metadata: Optional[str] = typer.Option(None, "--metadata"),
    password: Optional[str] = typer.Option(None, "--password"),
    concurrency: int = typer.Option(5, "--concurrency", min=1),
):
    """
    Scan all files under a folder (matching the given glob pattern) using the async client.
    Examples:
        dsxa scan-folder dsxa_sdk/tests/assets/samples --pattern "**/*"
        dsxa scan-folder ./samples --pattern "**/*.pdf" --concurrency 8
    """
    if not folder.is_dir():
        typer.echo(f"{folder} is not a directory", err=True)
        raise typer.Exit(code=1)
    files = [p for p in folder.glob(pattern) if p.is_file()]
    if not files:
        typer.echo("No files matched the provided pattern", err=True)
        raise typer.Exit(code=1)
    asyncio.run(
        _scan_paths(
            ctx,
            files,
            mode=mode,
            custom_metadata=custom_metadata,
            password=password,
            concurrency=concurrency,
        )
    )


async def _scan_paths(
    ctx: typer.Context,
    paths: List[pathlib.Path],
    *,
    mode: ScanMode,
    custom_metadata: Optional[str],
    password: Optional[str],
    concurrency: int,
):
    client = get_async_client(ctx)
    sem = asyncio.Semaphore(max(1, concurrency))
    start = time.perf_counter()
    success = 0
    failures = 0

    async def process(path: pathlib.Path):
        nonlocal success, failures
        async with sem:
            try:
                data = await asyncio.to_thread(path.read_bytes)
                resp = await client.scan_binary(
                    data,
                    custom_metadata=custom_metadata,
                    password=password,
                    base64_header=(mode == ScanMode.BASE64),
                )
                typer.echo(f"{path}: {resp.verdict.value} (scan_guid={resp.scan_guid})")
                success += 1
            except Exception as exc:  # pragma: no cover - CLI helper
                failures += 1
                typer.echo(f"{path}: ERROR {exc}", err=True)

    await asyncio.gather(*(process(p) for p in paths))
    await client.aclose()
    elapsed = time.perf_counter() - start
    typer.echo(
        f"Processed {len(paths)} file(s) in {elapsed:.2f}s "
        f"(scanned={success}, errors={failures})"
    )


def print_scan_response(resp: ScanResponse):
    print_json(data=resp.model_dump(by_alias=True))
def get_async_client(ctx: typer.Context) -> AsyncDSXAClient:
    cfg: CLIConfig = ctx.obj
    return AsyncDSXAClient(
        base_url=cfg.base_url,
        auth_token=cfg.auth_token,
        default_protected_entity=cfg.protected_entity,
        verify_tls=cfg.verify_tls,
    )
