# 🗂️ P2P File Storage with Slicing

> Distributed Systems Project — UniUrb LM-18 · 2025–2026  
> Prof. Francesco Spegni

A peer-to-peer file storage system where files are **encrypted**, **split into slices**, and **distributed round-robin** across peer nodes. No single node holds a complete file. Peer discovery is managed by **Apache ZooKeeper**.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Docker Network                     │
│                                                      │
│   ┌────────────┐   ZK Watch   ┌────────────────┐    │
│   │ Node Alice │ ◄──────────► │   ZooKeeper    │    │
│   │  :8001     │              │     :2181      │    │
│   └─────┬──────┘              └────────────────┘    │
│         │ HTTP slice transfer                        │
│   ┌─────▼──────┐              ┌────────────────┐    │
│   │  Node Bob  │ ◄──────────► │  Node Carol    │    │
│   │   :8002    │              │    :8003       │    │
│   └────────────┘              └────────────────┘    │
└─────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites
- Docker Desktop installed
- Docker Compose v3.9+

### 1. Clone the repo
```bash
git clone https://github.com/YOUR_USERNAME/p2p-file-storage.git
cd p2p-file-storage
```

### 2. Start the network
```bash
docker compose up --build
```

This starts:
- `p2p-zookeeper` — peer coordination service
- `p2p-node-alice` — peer node on port 8001
- `p2p-node-bob`   — peer node on port 8002
- `p2p-node-carol` — peer node on port 8003

### 3. Verify everything is running
```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
```

---

## 📦 Using the CLI

All CLI commands run **inside** the Alice container:

```bash
# Open a shell in the Alice node
docker exec -it p2p-node-alice bash

# First time setup — set your secret key
python client_cli.py setup

# Upload a file
python client_cli.py upload /path/to/myfile.pdf

# List your files
python client_cli.py list

# Download a file (use the file_id from list)
python client_cli.py download <file_id>

# Delete a file (broadcasts to all nodes)
python client_cli.py delete <file_id>

# Rebuild file index from peers (after reconnecting)
python client_cli.py rebuild-index
```

---

## 🔐 How Encryption Works

1. User sets a secret password on first run
2. Password → AES-256 key via **Scrypt KDF** (memory-hard, brute-force resistant)
3. File is encrypted with **Fernet (AES-128-CBC + HMAC-SHA256)**
4. The key **never leaves your machine** — peers only see ciphertext

---

## ✂️ How Slicing Works

```
File → Encrypt → Split into N-byte blocks → Add metadata header → Distribute round-robin

Block format:
┌──────────────────────────────────────────┐
│ 4 bytes: header length (uint32)           │
│ N bytes: JSON metadata (plaintext)        │
│   {file_id, block_index, total_blocks...} │
│ M bytes: encrypted block data             │
└──────────────────────────────────────────┘
```

**Round-robin example** (5 blocks, 3 nodes):
- Block 0 → Alice, Block 1 → Bob, Block 2 → Carol
- Block 3 → Alice, Block 4 → Bob

---

## 🌐 API Endpoints

Each node exposes:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Node status + peer list |
| GET | `/peers` | Online peers from ZooKeeper |
| POST | `/slices` | Receive a slice |
| GET | `/slices/{file_id}/{block_index}` | Fetch a slice |
| GET | `/slices/owner/{owner_id}` | List slices for user |
| DELETE | `/slices/{file_id}` | Delete slices for file |
| GET | `/files?owner_id=X` | List user files |
| DELETE | `/files/{file_id}` | Broadcast delete |
| POST | `/rebuild-index?owner_id=X` | Rebuild index from peers |

---

## 🧪 Running Tests

```bash
cd node
pip install -r requirements.txt
pytest tests/ -v
```

---

## 🛠️ Project Structure

```
p2p-file-storage/
├── docker-compose.yml
├── .gitignore
├── README.md
├── node/
│   ├── Dockerfile
│   ├── main.py           # FastAPI server
│   ├── client_cli.py     # User CLI
│   ├── crypto_manager.py # AES-256 encryption
│   ├── slicing_engine.py # Slice + distribute + reassemble
│   ├── slice_store.py    # Filesystem slice storage
│   ├── cache_db.py       # SQLite local index
│   ├── zk_registry.py    # ZooKeeper peer registry
│   ├── requirements.txt
│   └── tests/
│       └── test_core.py
├── zookeeper/
│   └── zoo.cfg
└── .github/
    └── workflows/
        └── ci.yml
```

---

## 👥 Sharing Docker Images (for your friend)

```bash
# You: build and push
docker compose build
docker tag p2p-file-storage-node-alice YOUR_USERNAME/p2p-storage-node:latest
docker push YOUR_USERNAME/p2p-storage-node:latest

# Friend: pull and run
docker pull YOUR_USERNAME/p2p-storage-node:latest
docker compose up
```

---

## 📋 Requirements Checklist

- [x] Users can connect/disconnect from network (ZooKeeper ephemeral znodes)
- [x] First access: user sets secret key (Scrypt KDF)
- [x] On connect: rebuild file index from peers
- [x] Write file: encrypt → slice → distribute round-robin
- [x] Read file: fetch → sort → concat → decrypt → save
- [x] Delete file: broadcast delete to all nodes
- [x] ZooKeeper for peer coordination
- [x] Docker + Docker Compose
- [x] Git repository
