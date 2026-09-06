"""Deterministic red-flag rules: abnormal vitals, dangerous drug/drug and
drug/allergy combinations, and mentions of pending-critical results.

Kept 100% rule-based and dependency-free (no LLM call in this service) on
purpose: this is the safety-net check the incoming doctor is meant to be
able to trust without re-reading the whole chart, so its logic needs to be
inspectable and testable, not a model's best guess. Extending coverage means
adding a pattern/table entry here, not prompt-tuning.
"""
from __future__ import annotations

import re
from collections import defaultdict

from schemas import RedFlag, RedFlagSeverity, RedFlagType, StoredNote

# --- Vitals --------------------------------------------------------------

_BP_PATTERN = re.compile(r"\b(\d{2,3})/(\d{2,3})\b")
_HR_PATTERN = re.compile(r"\bHR\s*[:\-]?\s*(\d{2,3})\b", re.I)
_SPO2_PATTERN = re.compile(r"\bSpO2\s*[:\-]?\s*(\d{2,3})\s?%?", re.I)


def check_vitals(notes: list[StoredNote]) -> list[RedFlag]:
    flags: list[RedFlag] = []
    for note in notes:
        for m in _BP_PATTERN.finditer(note.text):
            sys_bp, dia_bp = int(m.group(1)), int(m.group(2))
            if not (40 <= sys_bp <= 300 and 20 <= dia_bp <= 200):
                continue  # not actually a blood pressure reading
            if sys_bp >= 180 or dia_bp >= 120:
                flags.append(RedFlag(
                    type=RedFlagType.VITAL_SIGN, severity=RedFlagSeverity.CRITICAL,
                    description=f"Hypertensive-crisis-range blood pressure ({sys_bp}/{dia_bp}).",
                    evidence=m.group(0), source_note_ids=[note.note_id],
                ))
            elif sys_bp <= 90 or dia_bp <= 60:
                flags.append(RedFlag(
                    type=RedFlagType.VITAL_SIGN, severity=RedFlagSeverity.HIGH,
                    description=f"Hypotensive blood pressure ({sys_bp}/{dia_bp}).",
                    evidence=m.group(0), source_note_ids=[note.note_id],
                ))
        for m in _HR_PATTERN.finditer(note.text):
            hr = int(m.group(1))
            if hr >= 130:
                flags.append(RedFlag(
                    type=RedFlagType.VITAL_SIGN, severity=RedFlagSeverity.HIGH,
                    description=f"Tachycardia (HR {hr}).", evidence=m.group(0), source_note_ids=[note.note_id],
                ))
            elif hr <= 45:
                flags.append(RedFlag(
                    type=RedFlagType.VITAL_SIGN, severity=RedFlagSeverity.HIGH,
                    description=f"Bradycardia (HR {hr}).", evidence=m.group(0), source_note_ids=[note.note_id],
                ))
        for m in _SPO2_PATTERN.finditer(note.text):
            spo2 = int(m.group(1))
            if spo2 < 90:
                flags.append(RedFlag(
                    type=RedFlagType.VITAL_SIGN, severity=RedFlagSeverity.CRITICAL,
                    description=f"Critical hypoxia (SpO2 {spo2}%).", evidence=m.group(0), source_note_ids=[note.note_id],
                ))
            elif spo2 < 94:
                flags.append(RedFlag(
                    type=RedFlagType.VITAL_SIGN, severity=RedFlagSeverity.MEDIUM,
                    description=f"Mild hypoxia (SpO2 {spo2}%).", evidence=m.group(0), source_note_ids=[note.note_id],
                ))
    return flags


# --- Drug mentions (shared by interaction + allergy checks) --------------

_DRUG_NAMES = [
    "nitroglycerin", "nitro", "aspirin", "asa", "heparin", "metoprolol",
    "morphine", "insulin", "epinephrine", "albuterol", "furosemide", "lasix",
    "warfarin", "clopidogrel", "plavix", "atorvastatin", "metformin",
    "lisinopril", "amiodarone", "adenosine", "naloxone", "narcan",
    "penicillin", "ibuprofen", "acetaminophen", "tylenol", "ondansetron",
    "zofran", "ketorolac", "toradol", "vancomycin", "ceftriaxone",
    "azithromycin", "prednisone", "dexamethasone", "sildenafil", "tadalafil",
    "diazepam", "lorazepam", "fentanyl", "hydromorphone", "oxycodone",
]
_DRUG_PATTERN = re.compile(r"\b(" + "|".join(re.escape(d) for d in _DRUG_NAMES) + r")\b", re.I)


def _find_drug_mentions(notes: list[StoredNote]) -> dict[str, list[tuple[str, str]]]:
    """drug (lowercase) -> [(note_id, verbatim matched text), ...]"""
    mentions: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for note in notes:
        for m in _DRUG_PATTERN.finditer(note.text):
            mentions[m.group(0).lower()].append((note.note_id, m.group(0)))
    return mentions


# --- Drug/drug interactions -----------------------------------------------

_DANGEROUS_PAIRS: list[tuple[tuple[str, ...], tuple[str, ...], str]] = [
    (("nitroglycerin", "nitro"), ("sildenafil", "tadalafil"),
     "Nitrates combined with PDE5 inhibitors can cause severe, life-threatening hypotension."),
    (("warfarin",), ("ibuprofen", "ketorolac", "toradol", "aspirin", "asa"),
     "Warfarin combined with an NSAID/antiplatelet increases bleeding risk."),
    (("clopidogrel", "plavix"), ("ibuprofen", "ketorolac", "toradol"),
     "Antiplatelet combined with an NSAID increases bleeding risk."),
    (("morphine", "fentanyl", "hydromorphone", "oxycodone"), ("diazepam", "lorazepam"),
     "Opioid combined with a benzodiazepine increases respiratory depression risk."),
]


def check_drug_interactions(notes: list[StoredNote]) -> list[RedFlag]:
    mentions = _find_drug_mentions(notes)
    flags: list[RedFlag] = []
    for group_a, group_b, description in _DANGEROUS_PAIRS:
        drug_a = next((d for d in group_a if d in mentions), None)
        drug_b = next((d for d in group_b if d in mentions), None)
        if not (drug_a and drug_b):
            continue
        note_a, text_a = mentions[drug_a][0]
        note_b, text_b = mentions[drug_b][0]
        flags.append(RedFlag(
            type=RedFlagType.DRUG_INTERACTION, severity=RedFlagSeverity.HIGH,
            description=description,
            evidence=f"{text_a!r} and {text_b!r} both mentioned in notes",
            source_note_ids=sorted({note_a, note_b}),
        ))
    return flags


# --- Allergy conflicts -----------------------------------------------------

_ALLERGY_PATTERN = re.compile(r"allerg(?:y|ic)\s+to\s+([a-zA-Z]+)", re.I)
_NEGATIVE_ALLERGY_WORDS = {"none", "nka", "nkda", "no known", "unknown"}

_ALLERGY_CLASS_MAP: dict[str, list[str]] = {
    "penicillin": ["penicillin"],
    "nsaids": ["ibuprofen", "ketorolac", "toradol", "aspirin", "asa"],
    "aspirin": ["aspirin", "asa"],
    "asa": ["aspirin", "asa"],
    "sulfa": [],
    "morphine": ["morphine"],
    "codeine": ["oxycodone"],
}


def check_allergy_conflicts(notes: list[StoredNote]) -> list[RedFlag]:
    allergy_mentions: list[tuple[str, str, str]] = []  # allergen, note_id, verbatim text
    for note in notes:
        for m in _ALLERGY_PATTERN.finditer(note.text):
            allergen = m.group(1).strip().lower()
            if allergen in _NEGATIVE_ALLERGY_WORDS:
                continue
            allergy_mentions.append((allergen, note.note_id, m.group(0)))

    if not allergy_mentions:
        return []

    drug_mentions = _find_drug_mentions(notes)
    flags: list[RedFlag] = []
    seen: set[tuple[str, str]] = set()
    for allergen, allergy_note_id, allergy_evidence in allergy_mentions:
        related_drugs = _ALLERGY_CLASS_MAP.get(allergen, [allergen])
        for drug in related_drugs:
            for admin_note_id, admin_text in drug_mentions.get(drug, []):
                if admin_note_id == allergy_note_id:
                    continue  # the allergy statement itself naming the drug isn't an administration
                key = (allergen, admin_note_id)
                if key in seen:
                    continue
                seen.add(key)
                flags.append(RedFlag(
                    type=RedFlagType.ALLERGY_CONFLICT, severity=RedFlagSeverity.CRITICAL,
                    description=f"Patient noted allergic to '{allergen}' but '{drug}' also mentioned in notes.",
                    evidence=f"{allergy_evidence!r} vs {admin_text!r}",
                    source_note_ids=sorted({allergy_note_id, admin_note_id}),
                ))
    return flags


# --- Pending / critical results --------------------------------------------

_RESULT_TERMS = r"(troponin|potassium|lactate|creatinine|hemoglobin|wbc|inr|glucose)"
_PENDING_PATTERN_A = re.compile(
    r"\b(pending|trending|elevated|critical|abnormal)\b.{0,40}\b" + _RESULT_TERMS + r"\b", re.I
)
_PENDING_PATTERN_B = re.compile(
    r"\b" + _RESULT_TERMS + r"\b.{0,40}\b(pending|trending|elevated|critical|abnormal)\b", re.I
)


def check_pending_critical(notes: list[StoredNote]) -> list[RedFlag]:
    flags: list[RedFlag] = []
    seen: set[tuple[str, str]] = set()
    for note in notes:
        for pattern in (_PENDING_PATTERN_A, _PENDING_PATTERN_B):
            for m in pattern.finditer(note.text):
                key = (note.note_id, m.group(0).lower())
                if key in seen:
                    continue
                seen.add(key)
                flags.append(RedFlag(
                    type=RedFlagType.PENDING_CRITICAL_RESULT, severity=RedFlagSeverity.MEDIUM,
                    description=f"Mentions a pending or abnormal result: '{m.group(0)}'.",
                    evidence=m.group(0), source_note_ids=[note.note_id],
                ))
    return flags


def check_red_flags(notes: list[StoredNote]) -> list[RedFlag]:
    return [
        *check_vitals(notes),
        *check_drug_interactions(notes),
        *check_allergy_conflicts(notes),
        *check_pending_critical(notes),
    ]
