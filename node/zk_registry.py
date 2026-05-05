"""
ZKPeerRegistry — manages peer discovery via Apache ZooKeeper.
- Creates an ephemeral znode when node comes online
- Watches /p2p-storage/peers for changes
- Automatically removes its znode when node disconnects or crashes
"""
import json
import logging
from typing import Callable, Dict, List, Optional

from kazoo.client import KazooClient
from kazoo.exceptions import NodeExistsError
from kazoo.recipe.watchers import ChildrenWatch

logger = logging.getLogger(__name__)

ZK_ROOT  = "/p2p-storage"
ZK_PEERS = f"{ZK_ROOT}/peers"


class ZKPeerRegistry:
    def __init__(
        self,
        zk_host: str,
        node_id: str,
        host: str,
        port: int,
        on_peers_changed: Optional[Callable[[List[dict]], None]] = None,
    ):
        self.zk_host   = zk_host
        self.node_id   = node_id
        self.host      = host
        self.port      = port
        self.on_peers_changed = on_peers_changed
        self._peers: Dict[str, dict] = {}
        self.zk = KazooClient(hosts=zk_host)

    # ── connect ───────────────────────────────────────────────
    def connect(self) -> None:
        self.zk.start(timeout=10)
        logger.info(f"[ZK] Connected to {self.zk_host}")

        # Ensure base paths exist
        self.zk.ensure_path(ZK_PEERS)

        # Register this node as ephemeral znode
        self._register()

        # Start watching peer list
        self._watch_peers()

    def _register(self) -> None:
        path = f"{ZK_PEERS}/{self.node_id}"
        data = json.dumps({
            "node_id": self.node_id,
            "host":    self.host,
            "port":    self.port,
        }).encode()
        try:
            self.zk.create(path, data, ephemeral=True, makepath=True)
            logger.info(f"[ZK] Registered as {self.node_id}")
        except NodeExistsError:
            # Already exists (e.g. quick restart) — overwrite
            self.zk.set(path, data)
            logger.info(f"[ZK] Re-registered as {self.node_id}")

    def _watch_peers(self) -> None:
        @ChildrenWatch(self.zk, ZK_PEERS, send_event=True)
        def _on_change(children, event=None):
            updated: Dict[str, dict] = {}
            for child in children:
                if child == self.node_id:
                    continue   # skip self
                try:
                    raw, _ = self.zk.get(f"{ZK_PEERS}/{child}")
                    info = json.loads(raw.decode())
                    updated[child] = info
                except Exception as e:
                    logger.warning(f"[ZK] Could not read peer {child}: {e}")
            self._peers = updated
            peer_list = list(updated.values())
            logger.info(f"[ZK] Peers updated → {[p['node_id'] for p in peer_list]}")
            if self.on_peers_changed:
                self.on_peers_changed(peer_list)

    # ── public ────────────────────────────────────────────────
    def get_peers(self) -> List[dict]:
        """Returns list of {node_id, host, port} for all online peers."""
        return list(self._peers.values())

    def get_peers_dict(self) -> Dict[str, dict]:
        """Returns {node_id: {host, port}} dict."""
        return dict(self._peers)

    # ── disconnect ────────────────────────────────────────────
    def disconnect(self) -> None:
        """Gracefully stop — ephemeral znode is auto-removed by ZK."""
        self.zk.stop()
        self.zk.close()
        logger.info(f"[ZK] Disconnected — znode removed automatically")
