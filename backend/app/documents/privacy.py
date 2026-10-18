"""Masking personal data before any text leaves the machine for a hosted model.

Identifiers (Aadhaar, PAN, phone, email, policy and hospital record numbers) are
always masked. Person names are masked only on lines that read like a name field
("Patient Name:", "Mr.", "S/o"), because a small NER model also tags drug and
brand names on bills as people, and masking those would break the audit.

The token-to-value map is kept encrypted (Fernet), so a final letter addressed
to the insurer can be filled back in without storing names in clear text.
"""

import json
import re
from collections.abc import Iterable
from typing import Any, ClassVar

from cryptography.fernet import Fernet, InvalidToken
from presidio_analyzer import (
    AnalyzerEngine,
    EntityRecognizer,
    RecognizerRegistry,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpArtifacts, NlpEngineProvider
from presidio_analyzer.predefined_recognizers import (
    EmailRecognizer,
    InAadhaarRecognizer,
    InPanRecognizer,
    InPassportRecognizer,
    InVoterRecognizer,
    SpacyRecognizer,
)

from app.config import PrivacySettings
from app.errors import ConfigurationError

_NAME_CUES = re.compile(
    r"\b(patient|name|insured|proposer|policy\s*holder|nominee|attendant|member|"
    r"mr|mrs|ms|miss|master|dr|shri|smt|s/o|d/o|w/o|c/o)\b",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"\[[A-Z_]+_\d+\]")
# One more capitalised word after a detected name, on the same line.
_NAME_WORD = re.compile(r" ([A-Z][a-z]{1,20})\b")
_NOT_NAME_WORDS = {
    "age",
    "sex",
    "male",
    "female",
    "date",
    "bed",
    "ward",
    "room",
    "uhid",
    "mobile",
    "phone",
    "address",
    "policy",
    "dob",
    "consultant",
    "doctor",
    "hospital",
    "ip",
}
_MIN_KNOWN_VALUE = 3


class _LabelledIdRecognizer(EntityRecognizer):
    """Values that follow a label, e.g. `Policy No: 12/34/5678` or `UHID - MH0012`."""

    PATTERNS: ClassVar[dict[str, re.Pattern[str]]] = {
        "POLICY_NUMBER": re.compile(
            r"policy\s{0,3}(?:no|number|num|#)\.?\s{0,3}[:\-]?\s{0,3}([A-Z0-9][A-Z0-9/\-]{5,40})",
            re.I,
        ),
        "MEDICAL_RECORD_NUMBER": re.compile(
            r"\b(?:uhid|mrn|mr\s?no|ip\s?no|ipd\s?no|reg(?:istration)?\s?no)\.?\s{0,3}[:\-]?\s{0,3}"
            r"([A-Z0-9][A-Z0-9/\-]{3,30})",
            re.I,
        ),
        "IN_MOBILE": re.compile(r"(?<!\d)((?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5})(?!\d)"),
    }

    def __init__(self) -> None:
        super().__init__(supported_entities=list(self.PATTERNS), name="labelled_ids")

    def load(self) -> None:
        return None

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None
    ) -> list[RecognizerResult]:
        found = []
        for entity, pattern in self.PATTERNS.items():
            if entity not in entities:
                continue
            for match in pattern.finditer(text):
                found.append(RecognizerResult(entity, match.start(1), match.end(1), score=0.9))
        return found


class PiiMasker:
    ENTITIES: ClassVar[list[str]] = [
        "PERSON",
        "EMAIL_ADDRESS",
        "IN_AADHAAR",
        "IN_PAN",
        "IN_PASSPORT",
        "IN_VOTER",
        "IN_MOBILE",
        "POLICY_NUMBER",
        "MEDICAL_RECORD_NUMBER",
    ]

    def __init__(self, settings: PrivacySettings) -> None:
        try:
            self._fernet = Fernet(settings.pii_vault_key.get_secret_value().encode())
        except ValueError as exc:
            raise ConfigurationError("PII_VAULT_KEY is not a valid Fernet key") from exc
        self._threshold = settings.pii_score_threshold
        nlp = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": settings.pii_spacy_model}],
            }
        ).create_engine()
        registry = RecognizerRegistry(supported_languages=["en"])
        for recognizer in (
            SpacyRecognizer(supported_entities=["PERSON"]),
            EmailRecognizer(),
            InAadhaarRecognizer(),
            InPanRecognizer(),
            InPassportRecognizer(),
            InVoterRecognizer(),
            _LabelledIdRecognizer(),
        ):
            registry.add_recognizer(recognizer)
        self._analyzer = AnalyzerEngine(
            nlp_engine=nlp, registry=registry, supported_languages=["en"]
        )

    def mask(self, text: str, vault: dict[str, str]) -> str:
        """Replace personal data with stable tokens, adding new ones to `vault` (token -> value)."""
        results = self._analyzer.analyze(
            text=text, language="en", entities=self.ENTITIES, score_threshold=self._threshold
        )
        people = _merge_names([_extend_name(r, text) for r in results if _keep(r, text)], text)
        spans = _non_overlapping([*people, *_known_values(text, vault)])
        known = {value: token for token, value in vault.items()}
        pieces: list[str] = []
        cursor = 0
        for result in spans:
            value = text[result.start : result.end]
            token = known.get(value)
            if token is None:
                count = sum(1 for t in vault if t.startswith(f"[{result.entity_type}_"))
                token = f"[{result.entity_type}_{count + 1}]"
                vault[token] = value
                known[value] = token
            pieces.extend((text[cursor : result.start], token))
            cursor = result.end
        pieces.append(text[cursor:])
        return "".join(pieces)

    @staticmethod
    def unmask(text: str, vault: dict[str, str]) -> str:
        return _TOKEN.sub(lambda m: vault.get(m.group(0), m.group(0)), text)

    @staticmethod
    def remask(text: str, vault: dict[str, str]) -> str:
        """Re-mask text the owner edited in clear, so stored letters never hold raw values."""
        for token, value in sorted(vault.items(), key=lambda item: -len(item[1])):
            if value:
                text = text.replace(value, token)
        return text

    @classmethod
    def unmask_tree(cls, value: Any, vault: dict[str, str]) -> Any:
        """Unmask every string inside JSON-like data."""
        if isinstance(value, str):
            return cls.unmask(value, vault)
        if isinstance(value, list):
            return [cls.unmask_tree(item, vault) for item in value]
        if isinstance(value, dict):
            return {key: cls.unmask_tree(item, vault) for key, item in value.items()}
        return value

    def seal(self, vault: dict[str, str]) -> bytes:
        return self._fernet.encrypt(json.dumps(vault).encode())

    def unseal(self, sealed: bytes | None) -> dict[str, str]:
        if not sealed:
            return {}
        try:
            data = json.loads(self._fernet.decrypt(sealed))
        except InvalidToken as exc:
            raise ConfigurationError("PII vault cannot be decrypted with PII_VAULT_KEY") from exc
        return {str(k): str(v) for k, v in data.items()}


def _keep(result: RecognizerResult, text: str) -> bool:
    if result.entity_type != "PERSON":
        return True
    line_start = text.rfind("\n", 0, result.start) + 1
    line_end = text.find("\n", result.end)
    line = text[line_start : line_end if line_end != -1 else len(text)]
    return bool(_NAME_CUES.search(line))


def _extend_name(result: RecognizerResult, text: str) -> RecognizerResult:
    """The small NER model often stops after two words of an Indian name; finish the name."""
    if result.entity_type != "PERSON":
        return result
    end = result.end
    while match := _NAME_WORD.match(text, end):
        if match.group(1).lower() in _NOT_NAME_WORDS:
            break
        end = match.end()
    return RecognizerResult("PERSON", result.start, end, result.score)


def _merge_names(results: list[RecognizerResult], text: str) -> list[RecognizerResult]:
    """Join PERSON spans separated only by a space ("Ramesh Kumar" + "Sharma")."""
    ordered = sorted(results, key=lambda r: r.start)
    merged: list[RecognizerResult] = []
    for result in ordered:
        last = merged[-1] if merged else None
        if (
            last is not None
            and last.entity_type == result.entity_type == "PERSON"
            and result.start >= last.end
            and text[last.end : result.start].strip(" ") == ""
            and "\n" not in text[last.end : result.start]
        ):
            merged[-1] = RecognizerResult("PERSON", last.start, result.end, last.score)
        else:
            merged.append(result)
    return merged


def _known_values(text: str, vault: dict[str, str]) -> list[RecognizerResult]:
    """Values already in the vault are masked wherever they recur, even if NER misses them."""
    found = []
    for token, value in vault.items():
        if len(value) < _MIN_KNOWN_VALUE:
            continue
        entity = token.strip("[]").rsplit("_", 1)[0]
        for match in re.finditer(re.escape(value), text):
            found.append(RecognizerResult(entity, match.start(), match.end(), score=1.0))
    return found


def _non_overlapping(results: Iterable[RecognizerResult]) -> list[RecognizerResult]:
    ordered = sorted(results, key=lambda r: (r.start, -(r.end - r.start), -r.score))
    kept: list[RecognizerResult] = []
    for result in ordered:
        if kept and result.start < kept[-1].end:
            continue
        kept.append(result)
    return kept
