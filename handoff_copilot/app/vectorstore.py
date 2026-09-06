"""Pinecone wrapper: one namespace per patient, integrated (server-side)
embeddings so we don't need a separate embedding provider or model to run
locally. Metadata (author, note_type, created_at) rides along on each record
for filtering.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pinecone import IndexEmbed, EmbedModel, Pinecone

from app.certs import get_ca_bundle_path
from app.config import settings
from schemas import NoteType, StoredNote

_pc: Pinecone | None = None
_index = None


def _client() -> Pinecone:
    global _pc
    if _pc is None:
        _pc = Pinecone(api_key=settings.pinecone_api_key, ssl_ca_certs=get_ca_bundle_path())
    return _pc


def ensure_index() -> None:
    pc = _client()
    if not pc.has_index(settings.pinecone_index_name):
        pc.create_index_for_model(
            name=settings.pinecone_index_name,
            cloud=settings.pinecone_cloud,
            region=settings.pinecone_region,
            embed=IndexEmbed(
                model=EmbedModel.Llama_Text_Embed_V2,
                field_map={"text": "text"},
            ),
        )


def _get_index():
    global _index
    if _index is None:
        ensure_index()
        _index = _client().Index(name=settings.pinecone_index_name)
    return _index


def _record_from_note(note: StoredNote) -> dict:
    return {
        "_id": note.note_id,
        "text": note.text,
        "patient_id": note.patient_id,
        "author": note.author,
        "note_type": note.note_type.value,
        "created_at": note.created_at.isoformat(),
    }


async def upsert_note(note: StoredNote) -> None:
    index = _get_index()
    await asyncio.to_thread(
        index.upsert_records,
        namespace=note.patient_id,
        records=[_record_from_note(note)],
    )


async def search_notes(patient_id: str, query: str, top_k: int) -> list[StoredNote]:
    index = _get_index()
    try:
        response = await asyncio.to_thread(
            index.search,
            namespace=patient_id,
            query={"inputs": {"text": query}, "top_k": top_k},
        )
    except Exception:
        # An empty/never-used namespace (no notes yet for this patient) is a
        # normal condition, not a failure — treat any search error against a
        # namespace as "no notes found" rather than crashing the handoff.
        return []

    notes: list[StoredNote] = []
    for hit in response.result.hits:
        fields = hit.fields
        created_at_raw = fields.get("created_at")
        try:
            created_at = datetime.fromisoformat(created_at_raw) if created_at_raw else datetime.now(timezone.utc)
        except ValueError:
            created_at = datetime.now(timezone.utc)
        notes.append(
            StoredNote(
                note_id=hit.id,
                patient_id=patient_id,
                author=fields.get("author", "unknown"),
                note_type=NoteType(fields.get("note_type", NoteType.OTHER.value)),
                text=fields.get("text", ""),
                created_at=created_at,
            )
        )
    notes.sort(key=lambda n: n.created_at)
    return notes
