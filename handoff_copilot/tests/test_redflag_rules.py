from datetime import datetime, timezone

from redflag_service.rules import (
    check_allergy_conflicts,
    check_drug_interactions,
    check_pending_critical,
    check_red_flags,
    check_vitals,
)
from schemas import NoteType, StoredNote


def _note(note_id: str, text: str) -> StoredNote:
    return StoredNote(
        note_id=note_id, patient_id="p1", author="dr_a", note_type=NoteType.PROGRESS,
        text=text, created_at=datetime.now(timezone.utc),
    )


class TestCheckVitals:
    def test_flags_hypertensive_crisis(self):
        flags = check_vitals([_note("n1", "BP 190/125, patient anxious")])
        assert any(f.severity.value == "critical" for f in flags)

    def test_flags_hypotension(self):
        flags = check_vitals([_note("n1", "BP 85/55 on arrival")])
        assert any("Hypotensive" in f.description for f in flags)

    def test_normal_bp_not_flagged(self):
        flags = check_vitals([_note("n1", "BP 120/80, stable")])
        assert flags == []

    def test_flags_tachycardia_and_bradycardia(self):
        assert any("Tachycardia" in f.description for f in check_vitals([_note("n1", "HR 150")]))
        assert any("Bradycardia" in f.description for f in check_vitals([_note("n1", "HR 38")]))

    def test_flags_critical_and_mild_hypoxia(self):
        assert any("Critical hypoxia" in f.description for f in check_vitals([_note("n1", "SpO2 85%")]))
        assert any("Mild hypoxia" in f.description for f in check_vitals([_note("n1", "SpO2 92%")]))

    def test_ignores_non_vital_numeric_ratios(self):
        # e.g. a date-like or unrelated fraction shouldn't be misread as BP
        flags = check_vitals([_note("n1", "seen 12/25, discharge planned")])
        assert flags == []

    def test_empty_input(self):
        assert check_vitals([]) == []


class TestCheckDrugInteractions:
    def test_flags_nitrate_pde5_combo(self):
        flags = check_drug_interactions([_note("n1", "gave nitro"), _note("n2", "on sildenafil")])
        assert any(f.type.value == "drug_interaction" for f in flags)

    def test_single_drug_alone_not_flagged(self):
        assert check_drug_interactions([_note("n1", "gave nitro for chest pain")]) == []

    def test_unrelated_drugs_not_flagged(self):
        assert check_drug_interactions([_note("n1", "gave insulin and albuterol")]) == []

    def test_empty_input(self):
        assert check_drug_interactions([]) == []


class TestCheckAllergyConflicts:
    def test_flags_allergy_and_administration_in_different_notes(self):
        notes = [_note("n1", "allergic to penicillin"), _note("n2", "gave penicillin for infection")]
        flags = check_allergy_conflicts(notes)
        assert len(flags) == 1
        assert flags[0].severity.value == "critical"

    def test_negative_allergy_phrasing_not_flagged(self):
        notes = [_note("n1", "NKDA"), _note("n2", "gave penicillin for infection")]
        assert check_allergy_conflicts(notes) == []

    def test_allergy_mentioned_without_administration_not_flagged(self):
        assert check_allergy_conflicts([_note("n1", "allergic to penicillin")]) == []

    def test_allergy_named_in_same_note_as_drug_not_self_flagged(self):
        # "allergic to penicillin" alone in one note shouldn't count as both
        # the allergy AND the administration just because the word appears once.
        assert check_allergy_conflicts([_note("n1", "patient reports allergic to penicillin")]) == []

    def test_empty_input(self):
        assert check_allergy_conflicts([]) == []


class TestCheckPendingCritical:
    def test_flags_trending_troponin(self):
        flags = check_pending_critical([_note("n1", "trending troponin, repeat in 3h")])
        assert flags

    def test_normal_note_not_flagged(self):
        assert check_pending_critical([_note("n1", "patient resting comfortably")]) == []

    def test_empty_input(self):
        assert check_pending_critical([]) == []


class TestCheckRedFlagsCombined:
    def test_empty_notes_returns_no_flags(self):
        assert check_red_flags([]) == []

    def test_combines_all_categories(self):
        notes = [_note("n1", "BP 190/125, allergic to penicillin"), _note("n2", "gave penicillin, trending troponin")]
        flags = check_red_flags(notes)
        types_found = {f.type.value for f in flags}
        assert "vital_sign" in types_found
        assert "allergy_conflict" in types_found
        assert "pending_critical_result" in types_found
