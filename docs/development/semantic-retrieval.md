# Local semantic and hybrid retrieval

## Model and runtime

M2 uses FastEmbed 0.9.0 with CPU ONNX Runtime and the English `BAAI/bge-small-en-v1.5` sentence embedding model. The model card reports MIT licensing, 33.4 million parameters, 384-dimensional vectors, and a 512-token input limit. FastEmbed's supported-model table reports a quantized ONNX bundle of about 0.067 GB. The pinned upstream ONNX mirror revision is `c32e6154d1bb7a0e47c5e745fd895e7700f44385`; the configured model ID remains `BAAI/bge-small-en-v1.5`. Model details: [BAAI model card](https://huggingface.co/BAAI/bge-small-en-v1.5), [FastEmbed supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/), and [pinned Qdrant ONNX model](https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q/tree/c32e6154d1bb7a0e47c5e745fd895e7700f44385).

The runtime is optional; the default retrieval mode stays lexical, and app startup never downloads or loads the embedding model. The explicit model download is approximately 67 MB and writes to the ignored `artifacts/embedding-models` cache. The model runs on CPU; no GPU, paid API, or hosted service is used. The evaluation machine reports 16 GB installed RAM (15.3 GiB visible to Windows), an AMD Ryzen 5 7520U (4 cores / 8 logical processors), and Python 3.12.13.

Install the optional runtime and explicitly fetch the pinned model from the repository root in PowerShell:

```powershell
uv sync --locked --project services/api --extra dev --extra semantic
uv run --locked --project services/api --extra semantic python services/api/app/embeddings/download_model.py
```

The second command is the only application setup command that downloads model files. The model cache is local and excluded from Git. On Windows without Developer Mode, Hugging Face may warn that it cannot use cache symlinks; model loading still works but can use more disk space.

Set the desired default mode in `.env` after caching the model:

```dotenv
WL_RETRIEVAL_MODE=hybrid
```

Requests can also choose `mode` as `lexical`, `semantic`, or `hybrid`. Example:

```powershell
Invoke-RestMethod -Method Get -Uri http://127.0.0.1:8000/api/v1/embeddings/status | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/embeddings/rebuild -ContentType 'application/json' -Body '{}' | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/retrieve -ContentType 'application/json' -Body '{"query":"How do plants store energy from sunlight?","mode":"hybrid","limit":5}' | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/answers -ContentType 'application/json' -Body '{"question":"How do plants store energy from sunlight?","mode":"semantic","limit":5}' | ConvertTo-Json -Depth 8
```

`GET /api/v1/embeddings/status` reports model load state and total/current/stale/missing vector counts. `POST /api/v1/embeddings/rebuild` indexes new or stale chunks in bounded batches; pass `{"force":true}` to rebuild every vector. Model/revision, vector dimension, and chunking configuration are included in the index fingerprint. Changing any of them marks existing vectors stale. Rebuild commits one batch at a time, so a later failure leaves the existing FTS data intact and status reports the incomplete embedding index. Document deletion cascades to its vectors. When the configured retrieval mode is semantic or hybrid, a successful PDF upload attempts this incremental rebuild automatically; a missing local model does not undo the stored document/FTS index and is reported as `embeddingStatus: unavailable`.

The API uses FastEmbed with `local_files_only=True`; an absent local model is reported without attempting a download. Hybrid retrieval falls back to lexical results and reports `retrievalMode: lexical_fallback` with a reason. Semantic-only retrieval returns a structured unavailable/stale error instead of silently using lexical results.

## Storage and ranking

Vectors are normalized float32 BLOBs in SQLite, keyed to real chunk IDs with a foreign key and cascade deletion. Query and passage vectors use the same pinned encoder; query text gets BGE's retrieval instruction prefix. Similarity is the dot product of L2-normalized vectors (cosine similarity). Exact scanning is suitable for the expected small local corpus and keeps the architecture free of another service. Query time is O(number of indexed chunks × 384); the search retains only the top configured candidates in memory. It is not intended for a large multi-user corpus.

Hybrid retrieval takes up to 50 candidates independently from FTS5 BM25 and exact cosine search, then applies reciprocal-rank fusion: `sum(1 / (60 + rank))`. Duplicate chunk IDs are fused once. Ties are resolved by document ID, page, chunk index, and chunk ID. The final output limit remains governed by `WL_MAX_RETRIEVAL_RESULTS`.

The initial `WL_SEMANTIC_MIN_SIMILARITY=0.60` abstention cutoff was selected on the small synthetic evaluation below. It is a retrieval relevance heuristic, not a probability or a factual correctness guarantee. Semantic similarity alone does not establish that a chunk answers a question. Returned evidence always includes the original stored text and its actual document/page citation. Semantic-only answers abstain when no candidate clears the cutoff; hybrid requests fall back to lexical results and abstain when that fallback has no matches.

## Evaluation

The tracked dataset `evaluations/semantic/retrieval_eval.json` contains three synthetic, two-page science documents and seven labeled queries: exact terms, paraphrases, synonyms, a multi-page topic, and two out-of-scope questions. Run the real-model comparison after the explicit cache step:

```powershell
uv run --locked --project services/api --extra semantic python evaluations/semantic/evaluate_retrieval.py
```

The evaluator builds a temporary SQLite database, embeds six page chunks, and compares lexical-only, semantic-only, and hybrid retrieval. Recall@5 is averaged over answerable queries; precision@5 treats missing result slots as non-relevant and includes unanswerable queries; MRR is averaged over all seven queries. It reports wall and process CPU time for initial indexing and Python traced peak allocations. The latter does not include ONNX Runtime native memory.

On the configured pinned model and this six-chunk set, all three modes measured Recall@5 `1.00`, Precision@5 `0.20`, and MRR `0.714`. This shows no measured ranking improvement over FTS5 on this tiny fixture. Initial index+model load took 3.9 seconds wall / 4.2 seconds process CPU; traced Python peak was 23.4 MB and excludes ONNX Runtime native memory. Results are intentionally not checked in as generated artifacts. This tiny synthetic corpus is useful for regression and threshold sanity checks only; it does not establish general retrieval quality or superiority to FTS5. Add human-judged passages from representative learner materials before changing the threshold or claiming a retrieval improvement.

Automated unit/API tests use deterministic fake embeddings and require no model cache or network. The separate `integration` test marker is opt-in with `WL_RUN_MODEL_INTEGRATION=1` and only loads an already cached model.

## Limitations

- Model and retrieval are English-only with a 512-token model input limit; the M1 chunks are character-bounded and may be truncated by the encoder for unusually long passages.
- Exact vector search scales linearly with indexed chunks; no ANN structure/vector database is included.
- The model is pretrained and used without training or fine-tuning.
- Lexical fallback is a capability fallback, not a promise that lexical results are relevant.
- Scores and citations express retrieval provenance, not verified answer truth.
