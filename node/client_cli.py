"""
P2P Storage CLI — user-facing command line tool.
Usage:
  python client_cli.py setup              # first-run key setup
  python client_cli.py upload <file>      # encrypt + slice + distribute
  python client_cli.py download <file_id> # fetch + reassemble + decrypt
  python client_cli.py delete <file_id>   # broadcast delete
  python client_cli.py list               # list your files
"""
import asyncio
import os
import sys
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from cache_db import CacheDB
from crypto_manager import CryptoManager
from slicing_engine import SlicingEngine

app    = typer.Typer(help="P2P File Storage CLI")
console = Console()

# ── config ────────────────────────────────────────────────────
NODE_URL  = os.getenv("NODE_URL",  "http://localhost:8000")
OWNER_ID  = os.getenv("NODE_ID",   "alice")
DATA_DIR  = os.getenv("DATA_DIR",  "/data")
DB_PATH   = os.getenv("DB_PATH",   "/data/cache.db")
WORK_DIR  = os.getenv("WORK_DIR",  "/data/working_dir")
BLOCK_SIZE = int(os.getenv("BLOCK_SIZE", str(512 * 1024)))

Path(WORK_DIR).mkdir(parents=True, exist_ok=True)


def _get_crypto() -> CryptoManager:
    if CryptoManager.is_first_run(DATA_DIR):
        console.print("[red]No key found. Run 'setup' first.[/red]")
        raise typer.Exit(1)
    password = typer.prompt("🔑 Enter your secret key", hide_input=True)
    return CryptoManager(password, data_dir=DATA_DIR)


# ── SETUP ─────────────────────────────────────────────────────
@app.command()
def setup():
    """First-run: set your secret key."""
    if not CryptoManager.is_first_run(DATA_DIR):
        console.print("[yellow]Key already configured.[/yellow]")
        raise typer.Exit()
    console.print("[bold cyan]First-time setup — choose a secret key[/bold cyan]")
    console.print("[dim]This key encrypts all your files. Never share it.[/dim]\n")
    password = typer.prompt("Enter secret key", hide_input=True)
    confirm  = typer.prompt("Confirm secret key", hide_input=True)
    if password != confirm:
        console.print("[red]Keys do not match. Try again.[/red]")
        raise typer.Exit(1)
    CryptoManager(password, data_dir=DATA_DIR)
    console.print("[green]✓ Key set up successfully![/green]")


# ── UPLOAD ────────────────────────────────────────────────────
@app.command()
def upload(file_path: str = typer.Argument(..., help="Path to file to upload")):
    """Encrypt, slice and distribute a file across the P2P network."""
    if not Path(file_path).exists():
        console.print(f"[red]File not found: {file_path}[/red]")
        raise typer.Exit(1)

    crypto = _get_crypto()
    engine = SlicingEngine(crypto, block_size=BLOCK_SIZE, owner_id=OWNER_ID)

    async def _run():
        # Get online peers from this node
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{NODE_URL}/peers")
            peers = resp.json()

        # Include self as a peer for storage
        peers.insert(0, {
            "node_id": OWNER_ID,
            "host":    os.getenv("NODE_HOST", "localhost"),
            "port":    int(os.getenv("NODE_PORT", "8000")),
        })

        if not peers:
            console.print("[red]No peers available. Make sure nodes are running.[/red]")
            raise typer.Exit(1)

        console.print(f"[cyan]Slicing {file_path}...[/cyan]")
        slices, file_id = engine.slice_file(file_path)
        console.print(f"  → {len(slices)} slices | file_id: [bold]{file_id}[/bold]")

        console.print(f"[cyan]Distributing to {len(peers)} peers...[/cyan]")
        assignment = await engine.distribute(slices, peers)

        for peer_id, indices in assignment.items():
            console.print(f"  → [green]{peer_id}[/green]: blocks {indices}")

        # Update local cache
        db = CacheDB(DB_PATH)
        db.upsert_file(file_id, Path(file_path).name, len(slices), OWNER_ID)
        for sl in slices:
            db.upsert_slice(file_id, sl["block_index"],
                            sl.get("assigned_peer", OWNER_ID))
        db.close()

        console.print(f"\n[bold green]✓ Upload complete![/bold green] file_id: {file_id}")
        return file_id

    asyncio.run(_run())


# ── DOWNLOAD ──────────────────────────────────────────────────
@app.command()
def download(
    file_id: str = typer.Argument(..., help="File ID to download"),
    out_dir: str = typer.Option(WORK_DIR, help="Output directory"),
):
    """Fetch, reassemble and decrypt a file."""
    crypto = _get_crypto()
    engine = SlicingEngine(crypto, owner_id=OWNER_ID)
    db     = CacheDB(DB_PATH)

    file_info = db.get_file(file_id)
    if not file_info:
        console.print(f"[red]File {file_id} not found in local cache.[/red]")
        console.print("[dim]Try running: python client_cli.py rebuild-index[/dim]")
        db.close()
        raise typer.Exit(1)

    slice_map = db.get_slice_map(file_id)
    if not slice_map:
        console.print("[red]No slice map found for this file.[/red]")
        db.close()
        raise typer.Exit(1)

    async def _run():
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{NODE_URL}/peers")
            peers_list = resp.json()

        peers_dict = {p["node_id"]: p for p in peers_list}
        # Add self
        peers_dict[OWNER_ID] = {
            "host": os.getenv("NODE_HOST", "localhost"),
            "port": int(os.getenv("NODE_PORT", "8000")),
        }

        out_path = str(Path(out_dir) / file_info["filename"])
        console.print(f"[cyan]Fetching {len(slice_map)} slices...[/cyan]")
        await engine.fetch_file(file_id, slice_map, peers_dict, out_path)
        console.print(f"[bold green]✓ File saved to: {out_path}[/bold green]")

    asyncio.run(_run())
    db.close()


# ── LIST ──────────────────────────────────────────────────────
@app.command(name="list")
def list_files():
    """List all your files from local cache."""
    db    = CacheDB(DB_PATH)
    files = db.list_files(OWNER_ID)
    db.close()

    if not files:
        console.print("[dim]No files found. Upload something first.[/dim]")
        return

    table = Table(title=f"Files for {OWNER_ID}")
    table.add_column("File ID",       style="cyan",  no_wrap=True)
    table.add_column("Filename",      style="white")
    table.add_column("Total Blocks",  style="green", justify="right")
    table.add_column("Created",       style="dim")

    for f in files:
        table.add_row(
            f["file_id"][:16] + "...",
            f["filename"],
            str(f["total_blocks"]),
            str(f["created_at"])[:19],
        )
    console.print(table)


# ── DELETE ────────────────────────────────────────────────────
@app.command()
def delete(file_id: str = typer.Argument(..., help="File ID to delete")):
    """Broadcast delete to all nodes."""
    async def _run():
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(f"{NODE_URL}/files/{file_id}")
            result = resp.json()
        console.print(f"[green]✓ Deleted file {file_id}[/green]")
        console.print(result)

    asyncio.run(_run())


# ── REBUILD INDEX ─────────────────────────────────────────────
@app.command()
def rebuild_index():
    """Ask all peers for your slices and rebuild local file index."""
    async def _run():
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{NODE_URL}/rebuild-index",
                params={"owner_id": OWNER_ID}
            )
            result = resp.json()
        console.print(f"[green]✓ Index rebuilt: {result['slices_found']} slices found[/green]")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
