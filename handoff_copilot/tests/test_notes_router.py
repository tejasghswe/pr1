"""Tests the /notes endpoint's own responsibilities (validation, PII scrub,
id assignment) without touching real Pinecone."""
import pytest
from fastapi.testclient import TestClient

from app import vectorstore
from app.main import app


@pytest.fixture(autouse=True)
def stub_ensure_index(monkeypatch):
    # Defends against TestClient triggering the startup event (which would
    # otherwise make a real Pinecone call) on FastAPI/Starlette versions that
    # run lifespan without an explicit `with TestClient(app) as client:`.
    monkeypatch.setattr(vectorstore, "ensure_index", lambda: None)


def test_create_note_scrubs_pii_and_assigns_id(monkeypatch):
    saved = {}

    async def fake_upsert(note):
        saved["note"] = note

    monkeypatch.setattr(vectorstore, "upsert_note", fake_upsert)

    client = TestClient(app)
    response = client.post(
        "/notes",
        json={
            "patient_id": "p1",
            "author": "dr_a",
            "note_type": "progress",
            "text": "call family at 555-123-4567",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["note_id"]
    assert "[REDACTED_PHONE]" in body["text"]
    assert saved["note"].note_id == body["note_id"]


def test_create_note_rejects_empty_text(monkeypatch):
    monkeypatch.setattr(vectorstore, "upsert_note", lambda note: None)
    client = TestClient(app)
    response = client.post(
        "/notes", json={"patient_id": "p1", "author": "dr_a", "note_type": "progress", "text": ""}
    )
    assert response.status_code == 422


def test_create_note_rejects_missing_patient_id(monkeypatch):
    monkeypatch.setattr(vectorstore, "upsert_note", lambda note: None)
    client = TestClient(app)
    response = client.post(
        "/notes", json={"author": "dr_a", "note_type": "progress", "text": "note text"}
    )
    assert response.status_code == 422
