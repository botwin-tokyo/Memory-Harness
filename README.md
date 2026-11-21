# Memory-harness

Memory-harness is a self-hosted backend that lets your applications remember facts across sessions and recall them through an HTTP API or the Model Context Protocol (MCP).

Created by **Prof. M. Botwin**. Copyright © 2026 **BotwinDOTTokyo LLC**.

## Features

- **Local memory.** Store facts, preferences, episodes, notes, and procedures in SQLite. A fresh installation starts empty. Keyword recall works without a model account or API key.
- **Recall with context.** Retrieve a token-budgeted set of memories, follow links to supporting records, and inspect revisions when a fact changes. Connect an embedding service to add vector retrieval.
- **Scoped access.** Give each application its own bearer key, allowed scopes, and trust level. Read-only keys let consumers recall memories without changing them.
- **Optional adapters.** Use MCP tools from an agent, or run the ingestion service to import documents, journal entries, conversations, and explicitly allowed repositories.

## Requirements

Use Python **3.11 or later** with `venv` and pip, Bash, and an SQLite build that allows loading the packaged `sqlite-vec` extension. The commands below use a Unix-style shell and `curl`. Installation needs access to the Python package index.

## Quick start

Download and extract the source. Open a terminal in the directory containing `bootstrap.sh`. These commands install the five local packages into a virtual environment and start the memory API in the foreground.

```bash
cp .env.example .env
./bootstrap.sh
.venv/bin/memory-harness
```

In a second terminal in the same directory, check the API. If you changed the port in `.env`, use that port here.

```bash
BASE_URL=http://127.0.0.1:8080
curl --fail --silent --show-error "$BASE_URL/healthz"
```

Look for `"status":"ok"`. The service creates its database and reference vocabulary on first startup. Keep the first terminal running for the next example.

## Configuration

Edit `.env` in the directory from which you launch the services, then restart the affected service. Blank values use the built-in defaults. Process environment variables take precedence. To try the memory API locally, leave the template blank.

For semantic retrieval, configure the harness embedding URL, key, and dimension, and initialize the vector index using the supplied `harness/scripts/bootstrap_vectors.py` utility. For model-assisted extraction, set the harness LLM base URL, model, and key. The ingestion adapter has separate model and OCR settings. For MCP, `MCP_ACCESS_TOKEN` authenticates the incoming client and `MCP_HARNESS_KEY` authenticates the adapter to the memory API.

<details>
<summary>Memory API and storage</summary>

| Setting | Purpose | Example value |
| --- | --- | --- |
| `MEMORY_HARNESS_SERVER__HOST` | Server host. | `127.0.0.1` |
| `MEMORY_HARNESS_SERVER__PORT` | Server port. | `8080` |
| `MEMORY_HARNESS_SERVER__CORS_ORIGINS` | Allowed browser origins as a JSON array. | `[]` |
| `MEMORY_HARNESS_DB__PATH` | SQLite database file. | `.state/harness.db` |
| `MEMORY_HARNESS_DB__BLOB_DIR` | Directory for large record bodies. | `.state/blobs` |
| `MEMORY_HARNESS_DB__BUSY_TIMEOUT_MS` | How long SQLite waits for a locked database, in milliseconds. | `5000` |
| `MEMORY_HARNESS_DB__INLINE_BODY_MAX_BYTES` | Maximum body size stored inline. | `4096` |
| `MEMORY_HARNESS_EMBEDDINGS__BASE_URL` | Embeddings base url. | `https://embed.example.com` |
| `MEMORY_HARNESS_EMBEDDINGS__API_KEY` | Embeddings api key. | `<your-credential>` |
| `MEMORY_HARNESS_EMBEDDINGS__TIMEOUT_S` | Embeddings timeout s. | `30.0` |
| `MEMORY_HARNESS_EMBEDDINGS__DIM` | Vector dimension reported by the embedding service. | `1024` |
| `MEMORY_HARNESS_EMBEDDINGS__MAX_BATCH` | Maximum texts sent per embedding request. | `128` |
| `MEMORY_HARNESS_LLM__BASE_URL` | Llm base url. | `https://model.example.com/v1` |
| `MEMORY_HARNESS_LLM__MODEL` | Llm model. | `your-model-id` |
| `MEMORY_HARNESS_LLM__API_KEY` | Llm api key. | `<your-credential>` |
| `MEMORY_HARNESS_LLM__MAX_TOKENS` | Llm max tokens. | `8192` |
| `MEMORY_HARNESS_LLM__TIMEOUT_S` | Llm timeout s. | `180.0` |
| `MEMORY_HARNESS_SECRETS__MODE_DEFAULT` | Default secret-handling mode setting. | `store-but-dont-train` |
| `MEMORY_HARNESS_SECRETS__ENTROPY_THRESHOLD` | Entropy threshold for detecting secret-like strings. | `4.5` |
| `MEMORY_HARNESS_CONFIG` | Optional path to backend TOML overrides. | `config.toml` |

</details>

<details>
<summary>Recall and graph tuning</summary>

| Setting | Purpose | Example value |
| --- | --- | --- |
| `MEMORY_HARNESS_RECALL__WEIGHTS__RELEVANCE` | Recall Weights relevance. | `2.0` |
| `MEMORY_HARNESS_RECALL__WEIGHTS__FRESHNESS` | Recall Weights freshness. | `0.3` |
| `MEMORY_HARNESS_RECALL__WEIGHTS__CONFIDENCE` | Recall Weights confidence. | `0.4` |
| `MEMORY_HARNESS_RECALL__WEIGHTS__SOURCE_TRUST` | Recall Weights source trust. | `0.3` |
| `MEMORY_HARNESS_RECALL__WEIGHTS__VECTOR_LEG` | Weight of vector results when merging keyword and vector rankings. | `0.5` |
| `MEMORY_HARNESS_RECALL__DEFAULT_TOKEN_BUDGET` | Recall default token budget. | `8000` |
| `MEMORY_HARNESS_RECALL__MAX_PREFERENCES` | Recall max preferences. | `12` |
| `MEMORY_HARNESS_RECALL__MAX_FACTS` | Recall max facts. | `20` |
| `MEMORY_HARNESS_RECALL__MAX_EPISODES` | Recall max episodes. | `6` |
| `MEMORY_HARNESS_RECALL__FACT_BUDGET_FLOOR` | Fraction of the post-core budget reserved from ordinary preferences. | `0.5` |
| `MEMORY_HARNESS_RECALL__KNN_K` | Number of vector neighbours considered during retrieval. | `200` |
| `MEMORY_HARNESS_RECALL__MAX_PROCEDURES` | Recall max procedures. | `1` |
| `MEMORY_HARNESS_RECALL__STALENESS_PENALTY` | Recall staleness penalty. | `0.1` |
| `MEMORY_HARNESS_RECALL__DISPUTE_PENALTY` | Recall dispute penalty. | `0.3` |
| `MEMORY_HARNESS_CORE__SHARED_BUDGET_TOKENS` | Core shared budget tokens. | `800` |
| `MEMORY_HARNESS_CORE__AGENT_BUDGET_TOKENS` | Core agent budget tokens. | `800` |
| `MEMORY_HARNESS_GRAPH__EXPAND_HOPS` | Number of link hops for recall expansion. | `2` |
| `MEMORY_HARNESS_GRAPH__HOP_DECAY` | Score multiplier per link hop. | `0.5` |
| `MEMORY_HARNESS_GRAPH__EXPLORE_MAX_DEPTH` | Maximum depth for graph exploration. | `5` |

</details>

<details>
<summary>Services and access</summary>

| Setting | Purpose | Example value |
| --- | --- | --- |
| `HARNESS_BASE` | Local HTTP address of the memory API for adapters. | `http://127.0.0.1:8080` |
| `INGEST_HARNESS_KEY` | Harness credential minted for the ingestion service. | `<your-credential>` |
| `MCP_HARNESS_KEY` | Harness credential minted for the MCP adapter. | `<your-credential>` |
| `CURATOR_API_KEY` | Harness credential minted for the curator. | `<your-credential>` |
| `PRIVATE_AUTHORITY_HOLDERS` | Comma-separated principal@node values expected by the private-authority audit. | `assistant@local` |
| `MCP_ACCESS_TOKEN` | Inbound bearer token for MCP clients. | `<your-credential>` |
| `MCP_CLIENT_TOKENS` | Comma-separated client:token entries for individually revocable MCP credentials. | `client-name:<generated-token>` |
| `MCP_ALLOWED_HOSTS` | Inbound hostnames including ports. Defaults to loopback. | `localhost:8099,127.0.0.1:8099` |
| `MCP_HOST` | MCP bind address. Defaults to loopback. | `127.0.0.1` |
| `MCP_PORT` | MCP listener port. | `8099` |
| `INGEST_BASE` | Local ingestion service URL used by MCP. | `http://127.0.0.1:8100` |
| `INGEST_HOST` | Ingest bind address. Defaults to loopback. | `127.0.0.1` |
| `INGEST_PORT` | Ingest listener port. | `8100` |
| `INGEST_STATE_DB` | Local ingestion ledger database. | `.state/ingest/state.db` |

</details>

<details>
<summary>Ingestion, library, and model adapters</summary>

| Setting | Purpose | Example value |
| --- | --- | --- |
| `ARTIFACT_SCOPE` | Scope for imported reference material. | `reference` |
| `OCR_BASE` | OCR service base URL. | `https://ocr.example.com` |
| `OCR_KEY` | OCR service credential. | `<your-credential>` |
| `LLM_BASE` | Ingest summarizer endpoint. | `https://model.example.com/v1` |
| `LLM_MODEL` | Ingest summarizer model ID. | `your-model-id` |
| `LLM_KEY` | Ingest summarizer credential. | `<your-credential>` |
| `EMBED_BASE` | Ingest embedding endpoint. | `https://embed.example.com` |
| `EMBED_KEY` | Ingest embedding credential. | `<your-credential>` |
| `GIT_ALLOWLIST` | Comma-separated owners or repositories explicitly enabled for import. | `your-username/sample-repo` |
| `GIT_PUSH_INDEX` | Set to 1 to publish generated indexes to explicitly imported repositories. | `0` |
| `CHROMIUM_BIN` | Chromium executable for optional page rendering. | `chromium` |
| `INGEST_ALLOW_PRIVATE` | Set to 1 only for a local test fixture that must fetch private IP addresses. | `0` |
| `BLOG_FEED_URL` | RSS feed for optional blog backfills. | `https://blog.example.com/rss.xml` |
| `BLOG_ALLOWED_HOSTS` | Comma-separated HTTPS hostnames allowed for blog articles. | `blog.example.com` |
| `LIBRARY_DIR` | Local directory for documents you upload. | `.state/library` |
| `SEED_KEY` | User-tier harness credential for approved seed records. | `<your-credential>` |
| `SEED_API_KEY` | Inbound bearer credential for protected ingestion routes. | `<your-credential>` |
| `SEED_MODEL_BASE` | Optional seed-generation endpoint overriding LLM_BASE. | `https://model.example.com/v1` |
| `SEED_MODEL_NAME` | Optional seed model overriding LLM_MODEL. | `your-model-id` |
| `SEED_MODEL_KEY` | Optional seed credential overriding LLM_KEY. | `<your-credential>` |
| `SEED_MODEL_MAX_TOKENS` | Output token limit for seed generation. | `8000` |
| `SEED_MODEL_TEMPERATURE` | Sampling temperature for seed generation. | `0.7` |
| `SEED_PROMPT_PATH` | User-supplied template for generating memory drafts. | `templates/episodes.txt` |
| `SEED_FACTS_PROMPT_PATH` | User-supplied template for generating fact drafts. | `templates/facts.txt` |
| `SEED_LEDGER` | Path to the local seed ledger. | `.state/ingest/seed.db` |
| `SEED_OUTDIR` | Output directory for drafts. | `.state/drafts` |
| `EMOTION_FLOOR` | Minimum similarity for matching an emotion. | `0.55` |
| `EMOTION_CACHE` | Path to the optional emotion vector cache. | `.state/ingest/emotion_vectors.json` |
| `OPENCLAW_STATE_DIR` | Explicit adapter state directory when directly using the conversation reader. | `/path/to/adapter-state` |
| `HERMES_HOME` | Explicit adapter state directory when directly using the conversation reader. | `/path/to/adapter-data` |

</details>

<details>
<summary>Fleet telemetry and registration</summary>

| Setting | Purpose | Example value |
| --- | --- | --- |
| `MEMORY_FLEET_ENABLED` | Set to 0 to disable configured telemetry. | `0` |
| `MEMORY_FLEET_TELEMETRY_KEY` | Outbound credential for optional fleet publishing. | `<your-credential>` |
| `MEMORY_FLEET_INGEST_URL` | Secure WebSocket URL of your telemetry receiver. | `wss://fleet.example.com/ingest` |
| `MEMORY_FLEET_NODE_ID` | Node identifier for telemetry. | `local-node` |
| `MEMORY_FLEET_SOURCE_ID` | Source identifier override for telemetry. | `harness` |
| `MEMORY_FLEET_HEARTBEAT_S` | Seconds between telemetry heartbeats. | `30` |
| `MEMORY_FLEET_SPOOL_MAX` | Maximum buffered telemetry events. | `2000` |
| `MEMORY_FLEET_MCP_URL` | HTTPS endpoint used for fleet registration. | `https://fleet.example.com/mcp` |
| `MCP_AGENT_KEY` | Fleet registration credential. | `<your-credential>` |

</details>

## Store and recall your first memory

In the second terminal, mint a key for a local client. This key can use the shared scope. The command prints its ID and its raw value once. Keep the ID for revocation. At the hidden prompt, paste only the raw value and press Enter.

```bash
.venv/bin/python harness/scripts/mint_key.py demo local --scopes shared --home shared
read -rs -p "Harness key: " HARNESS_KEY
echo
```

Store a fact, then search for it.

```bash
curl --fail --silent --show-error "$BASE_URL/v1/remember" \
  -H "Authorization: Bearer $HARNESS_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"type":"fact","body":"The calibration sample is violet.","emotion":"Neutrality"}'

curl --fail --silent --show-error "$BASE_URL/v1/recall" \
  -H "Authorization: Bearer $HARNESS_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query":"calibration sample"}'
```

The write response includes a `lineage_id` and a revision. In the recall response, look for the sentence under `facts`. A client can use the lineage ID to request revision history at `GET /v1/history/{lineage_id}`.

| Write field | When to use it |
| --- | --- |
| `type` | Required. Use `fact` for a statement like the example. The authenticated `GET /v1/types` route lists supported types. |
| `body` | Required. The text you want to remember. |
| `emotion` | Supply a vocabulary name for a memory write. Use `Neutrality` for the example. The authenticated `GET /v1/emotions` route lists the vocabulary. |
| `scope` | Optional. Select a scope allowed by the key. The example key defaults to `shared`. |
| `tags` | Optional array of labels, such as `["calibration"]`. |
| `memory_id` | Supply the lineage ID when revising an existing memory. |

## Build and deployment

The bootstrap script builds and installs the Python packages in `.venv/`. For a persistent deployment, run the installed `memory-harness` command under a process supervisor with the project directory as its working directory. Use a single worker for the SQLite writer. Keep the database and blob directory on persistent storage and back them up together using a SQLite-consistent backup.

| Service | Installed executable | Default local endpoint |
| --- | --- | --- |
| Memory API | `.venv/bin/memory-harness` | `http://127.0.0.1:8080` |
| Ingestion | `.venv/bin/memory-ingest` | `http://127.0.0.1:8100` |
| MCP | `.venv/bin/memory-mcp` | `http://127.0.0.1:8099/mcp` |

If remote clients need the memory API, terminate TLS at a reverse proxy and keep the service bound to loopback. For example, a Caddy site for a hostname you control can forward the authenticated API routes:

```caddyfile
memory.example.com {
    reverse_proxy /v1/* 127.0.0.1:8080
}
```

## Security

Keep `.env`, minted keys, uploaded documents, and the contents of `.state/` private. The supplied ignore rules cover the standard local paths. Give each client only the scopes it needs. Keep ingestion on a trusted local interface because its file and conversation imports can read data accessible to the service account. Review the text you send to any configured external model provider.

## Project structure

```text
bootstrap.sh    Build and install the backend in a virtual environment
.env.example    Settings template for all services
harness/        Memory API, SQLite schema, workers, and administration scripts
contract/       Shared data vocabulary and tool definitions
ingest/         Document, conversation, and repository importers
mcp/            MCP adapter for agent clients
fleet/          Optional service telemetry and registration
```

## Contributing

Issues and pull requests are welcome. For bug reports, include the Python version, the failing command or route, and a minimal example using synthetic data. Contributions are accepted under the project's license.

## License

Memory-harness is free to use, modify, and share for noncommercial purposes under the **PolyForm Noncommercial License 1.0.0**. Include the license and required notice when redistributing it. To sell the software or use it commercially, open an issue requesting permission from **BotwinDOTTokyo LLC**. See the [full license](LICENSE).
