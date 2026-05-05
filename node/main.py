"""
P2P File Storage — Node Server
Exposes HTTP API for slice upload/download/delete/list.
Manages ZooKeeper registration and peer discovery.
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from cache_db import CacheDB
from slice_store import SliceStore
from zk_registry import ZKPeerRegistry

# ── config from environment ───────────────────────────────────
NODE_ID   = os.getenv("NODE_ID",   "node-a")
NODE_HOST = os.getenv("NODE_HOST", "localhost")
NODE_PORT = int(os.getenv("NODE_PORT", "8000"))
ZK_HOST   = os.getenv("ZK_HOST",   "localhost:2181")
STORE_DIR = os.getenv("STORE_DIR", "/data/slices")
DB_PATH   = os.getenv("DB_PATH",   "/data/cache.db")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s")
logger = logging.getLogger("node")

# ── singletons ────────────────────────────────────────────────
store    = SliceStore(store_dir=STORE_DIR)
cache_db = CacheDB(db_path=DB_PATH)
registry: ZKPeerRegistry = None  # set in lifespan


def _on_peers_changed(peers: list):
    """Called by ZooKeeper watch when peer list changes."""
    logger.info(f"Peer list changed → {[p['node_id'] for p in peers]}")


# ── lifespan (startup / shutdown) ─────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global registry
    logger.info(f"Starting node {NODE_ID} on port {NODE_PORT}")

    # Connect to ZooKeeper and register
    registry = ZKPeerRegistry(
        zk_host=ZK_HOST,
        node_id=NODE_ID,
        host=NODE_HOST,
        port=NODE_PORT,
        on_peers_changed=_on_peers_changed,
    )
    try:
        registry.connect()
        logger.info(f"Node {NODE_ID} registered in ZooKeeper ✓")
    except Exception as e:
        logger.error(f"ZooKeeper connection failed: {e}")

    yield  # app runs here

    # Shutdown
    if registry:
        registry.disconnect()
    cache_db.close()
    logger.info(f"Node {NODE_ID} disconnected cleanly.")


app = FastAPI(
    title=f"P2P Storage Node — {NODE_ID}",
    version="1.0.0",
    lifespan=lifespan,
)


# ═══════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════
@app.get("/health")
def health():
    peers = registry.get_peers() if registry else []
    return {
        "status":  "online",
        "node_id": NODE_ID,
        "peers":   [p["node_id"] for p in peers],
    }


# ═══════════════════════════════════════════════════════════════
# PEERS
# ═══════════════════════════════════════════════════════════════
@app.get("/peers")
def get_peers():
    """Return list of currently online peers (from ZooKeeper)."""
    if not registry:
        return []
    return registry.get_peers()


# ═══════════════════════════════════════════════════════════════
# SLICES — storage endpoints (called by other nodes)
# ═══════════════════════════════════════════════════════════════
@app.post("/slices", status_code=201)
async def receive_slice(
    file_id:     str        = Form(...),
    block_index: int        = Form(...),
    owner_id:    str        = Form(...),
    file:        UploadFile = File(...),
):
    """Accept and store a slice sent by another node."""
    data = await file.read()
    store.save_slice(
        owner_id=owner_id,
        file_id=file_id,
        block_index=block_index,
        raw=data,
    )
    # Update local cache too
    cache_db.upsert_slice(file_id, block_index, NODE_ID)
    logger.info(f"Stored slice {file_id}[{block_index}] from {owner_id}")
    return {"stored": True, "node_id": NODE_ID}


@app.get("/slices/{file_id}/{block_index}")
def get_slice(file_id: str, block_index: int):
    """Serve a specific slice to a requesting node."""
    # Find owner by listing all
    for meta in store.list_all():
        if meta["file_id"] == file_id and meta["block_index"] == block_index:
            data = store.get_slice(meta["owner_id"], file_id, block_index)
            if data:
                return Response(content=data,
                                media_type="application/octet-stream")
    raise HTTPException(404, f"Slice {file_id}[{block_index}] not found")


@app.get("/slices/owner/{owner_id}", response_model=List[dict])
def list_slices_for_user(owner_id: str):
    """
    List all slice metadata this node holds for a given user.
    Called by other nodes on connect to rebuild the file index.
    """
    return store.list_for_user(owner_id)


@app.delete("/slices/{file_id}")
def delete_slices(file_id: str):
    """Delete all slices for a file_id. Called as part of broadcast delete."""
    count = store.delete_file(file_id)
    cache_db.delete_file(file_id)
    logger.info(f"Deleted {count} slices for file {file_id}")
    return {"deleted": count}


# ═══════════════════════════════════════════════════════════════
# FILES — high-level operations (called by the CLI client)
# ═══════════════════════════════════════════════════════════════
@app.get("/files")
def list_files(owner_id: str):
    """List all files the user owns (from local cache)."""
    return cache_db.list_files(owner_id)


@app.delete("/files/{file_id}")
async def delete_file(file_id: str):
    """
    Broadcast DELETE to all known peers + delete locally.
    Called by the CLI when user deletes a file.
    """
    peers = registry.get_peers() if registry else []
    results = {"self": store.delete_file(file_id)}

    async with httpx.AsyncClient(timeout=10.0) as client:
        for peer in peers:
            url = f"http://{peer['host']}:{peer['port']}/slices/{file_id}"
            try:
                resp = await client.delete(url)
                results[peer["node_id"]] = resp.json().get("deleted", 0)
            except Exception as e:
                results[peer["node_id"]] = f"error: {e}"

    cache_db.delete_file(file_id)
    return {"file_id": file_id, "deleted_per_node": results}


@app.post("/rebuild-index")
async def rebuild_index(owner_id: str):
    """
    Ask all peers for slice metadata and rebuild local cache.
    Called on node connect.
    """
    peers = registry.get_peers() if registry else []
    all_metas = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        for peer in peers:
            url = (f"http://{peer['host']}:{peer['port']}"
                   f"/slices/owner/{owner_id}")
            try:
                resp = await client.get(url)
                metas = resp.json()
                for m in metas:
                    m["peer_node_id"] = peer["node_id"]
                all_metas.extend(metas)
            except Exception as e:
                logger.warning(f"Could not fetch index from {peer['node_id']}: {e}")

    cache_db.rebuild_from_metas(all_metas, NODE_ID)
    logger.info(f"Rebuilt index for {owner_id}: {len(all_metas)} slices found")
    return {"owner_id": owner_id, "slices_found": len(all_metas)}
