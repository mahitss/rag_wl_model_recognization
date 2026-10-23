from __future__ import annotations

from pathlib import Path

from app.contract_validation import validate_api_contract
from app.main import create_app


def test_contract_validation_for_frontend_and_backend_routes(tmp_path):
    app = create_app()
    frontend_root = Path(__file__).resolve().parents[3] / "apps" / "web" / "src"
    validation = validate_api_contract(app, frontend_root)

    assert validation["missing_from_backend"] == []
    assert validation["missing_required_paths"] == []
