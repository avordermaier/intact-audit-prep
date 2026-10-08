import json
import shutil

import pytest

from audit_prep.loading import DEFAULT_DATA_DIR, load_case


def test_document_path_traversal_is_rejected(tmp_path):
    root = tmp_path / "northsea"
    shutil.copytree(DEFAULT_DATA_DIR, root)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    metadata = json.loads((root / "documents.json").read_text("utf-8"))
    metadata[0]["path"] = "../../secret.txt"
    (root / "documents.json").write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="inside"):
        load_case(root)


def test_duplicate_registry_identity_is_rejected(tmp_path):
    root = tmp_path / "northsea"
    shutil.copytree(DEFAULT_DATA_DIR, root)
    metadata = json.loads((root / "documents.json").read_text("utf-8"))
    metadata.append(metadata[0])
    (root / "documents.json").write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate"):
        load_case(root)


def test_extraction_status_defaults_to_ok_and_rejects_unknown_values(tmp_path):
    assert {doc.extraction_status for doc in load_case()[2]} == {"ok"}
    root = tmp_path / "northsea"
    shutil.copytree(DEFAULT_DATA_DIR, root)
    metadata = json.loads((root / "documents.json").read_text("utf-8"))
    metadata[0]["extraction_status"] = "unreliable"
    (root / "documents.json").write_text(json.dumps(metadata), encoding="utf-8")
    assert load_case(root)[2][0].extraction_status == "unreliable"
    metadata[0]["extraction_status"] = "unknown"
    (root / "documents.json").write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError):
        load_case(root)
