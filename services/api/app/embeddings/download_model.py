"""Explicitly download the configured embedding model into its ignored local cache."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import get_settings
from app.embeddings.service import ModelUnavailable


def main() -> None:
    settings = get_settings()
    try:
        from fastembed import TextEmbedding

        TextEmbedding(
            model_name=settings.embedding_model_id,
            cache_dir=str(settings.embedding_cache_path),
            revision=settings.embedding_model_revision,
            local_files_only=False,
            cuda=False,
        )
    except Exception as exc:
        raise ModelUnavailable(
            "Could not download the configured model. Check the model identifier, "
            "revision, and network connection."
        ) from exc
    print(
        f"Cached {settings.embedding_model_id} at revision "
        f"{settings.embedding_model_revision} in the configured local cache."
    )


if __name__ == "__main__":
    main()
