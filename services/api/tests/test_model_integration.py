import math
import os

import pytest

from app.config import Settings
from app.embeddings.service import EmbeddingService


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("WL_RUN_MODEL_INTEGRATION") != "1",
    reason="set WL_RUN_MODEL_INTEGRATION=1 to use an already cached model",
)
def test_pinned_embedding_model_loads_offline_and_returns_normalized_vector():
    settings = Settings(_env_file=None)
    service = EmbeddingService(settings)
    vector = service.query_vector("How do plants store energy from sunlight?")
    assert len(vector) == settings.embedding_dimensions == 384
    assert all(math.isfinite(value) for value in vector)
    assert math.hypot(*vector) == pytest.approx(1.0, abs=1e-5)
