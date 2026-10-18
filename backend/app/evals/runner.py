"""Runs synthetic cases through the real pipeline and scores the results.

Every case goes through upload, OCR/extraction, masking, policy indexing, the
agent graph and the checker, exactly as in the app. Scores:

- payout: settlement within 2% of the true amount, and the decision
- lines: share of true bill lines found, and share labelled with the right category
- terms: room limit, co-payment, pre-existing wait, cataract sub-limit, proportionate clause
- review: the insurer reason's verdict and the recommendation, for rejection cases
- letters: drafted letters that pass the checker (citations and rupee figures)
- chat: multi-turn questions answered with the expected fact and a citation
- models: attempts, failures and latency per provider and model, from `model_calls`
"""

import logging
import re
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.agents.advocate import LetterKind
from app.api.container import Container
from app.db.models import Base, Case, ModelCall, utcnow
from app.db.session import transaction
from app.domain import DocumentKind
from app.evals.synthetic import SyntheticCase
from app.services.views import AuditView

log = logging.getLogger(__name__)

_PAYOUT_TOLERANCE = Decimal("0.02")
# A waiting period read as "3 years" (1,095 days) or "36 months" (1,080) is the same term.
_WAIT_TOLERANCE_DAYS = 31


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@dataclass
class CaseScore:
    case_id: str
    scenario: str
    seconds: dict[str, float] = field(default_factory=dict)
    payout_ok: bool | None = None
    payout_error: float | None = None
    decision_ok: bool | None = None
    line_recall: float | None = None
    label_accuracy: float | None = None
    terms: dict[str, bool] = field(default_factory=dict)
    audit_verified: bool | None = None
    review_verdict_ok: bool | None = None
    review_recommendation_ok: bool | None = None
    letter_verified: bool | None = None
    chat: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def _score_terms(audit: AuditView, truth: dict[str, Any]) -> dict[str, bool]:
    got = audit.terms
    room, true_room = got.room_rent, truth.get("room_rent") or {}
    ped_true = next(w["days"] for w in truth["waiting_periods"] if w["kind"] == "pre_existing")
    ped_got = next((w.days for w in got.waiting_periods if w.kind == "pre_existing"), None)
    cataract = next((s for s in got.sub_limits if "cataract" in s.treatment.lower()), None)
    copay_true = truth.get("copay")
    copay_got = got.copay.percent if got.copay is not None else Decimal(0)
    copay_want = Decimal(str(copay_true["percent"])) if copay_true else Decimal(0)
    return {
        "room_rent": room is not None
        and room.basis == true_room.get("basis")
        and room.value is not None
        and Decimal(str(room.value)) == Decimal(str(true_room.get("value"))),
        "copay": copay_got == copay_want,
        "pre_existing_wait": ped_got is not None
        and abs(ped_got - ped_true) <= _WAIT_TOLERANCE_DAYS,
        "cataract_sub_limit": cataract is not None and cataract.amount == Decimal(40_000),
        "proportionate_clause": got.proportionate_deduction == truth["proportionate_deduction"],
    }


def _score_lines(audit: AuditView, truth_lines: list[dict[str, Any]]) -> tuple[float, float]:
    predicted = {_norm(line.description): line for line in audit.coverage.lines}
    found = labelled = 0
    for line in truth_lines:
        match = predicted.get(_norm(line["description"]))
        if match is None:
            match = next(
                (p for key, p in predicted.items() if _norm(line["description"]) in key), None
            )
        if match is None:
            continue
        found += 1
        labelled += match.category.value == line["category"]
    total = len(truth_lines) or 1
    return found / total, labelled / max(found, 1)


class EvalRunner:
    def __init__(self, container: Container, data_dir: Path) -> None:
        self._c = container
        self._data = data_dir

    async def prepare(self) -> None:
        async with self._c.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def _new_case(self, facts: dict[str, Any]) -> str:
        case, _ = await self._c.cases.create()
        async with transaction(self._c.sessions) as session:
            stored = await session.get(Case, case.id)
            if stored is not None:
                stored.facts = facts
        return case.id

    async def _upload(self, case_id: str, case: SyntheticCase) -> None:
        for kind, filename in case.documents.items():
            data = (self._data / case.case_id / filename).read_bytes()
            async with self._c.sessions() as session:
                stored = await self._c.cases.load(session, case_id)
            async with transaction(self._c.sessions) as session:
                document = await self._c.ingest.accept(session, stored, data, filename)
            # Policy wordings are typed by the uploader in the UI; others are auto-detected.
            hint = DocumentKind.POLICY if kind == "policy" else None
            await self._c.ingest.process(document.id, data, hint)
        async with self._c.sessions() as session:
            stored = await self._c.cases.load(session, case_id)
        failed = [f"{d.filename}: {d.error}" for d in stored.documents if d.status == "failed"]
        if failed:
            raise RuntimeError("document failed: " + "; ".join(failed))

    async def run_case(self, case: SyntheticCase, *, chat: bool) -> CaseScore:
        score = CaseScore(case_id=case.case_id, scenario=case.scenario)
        try:
            started = time.perf_counter()
            case_id = await self._new_case(case.facts)
            await self._upload(case_id, case)
            score.seconds["ingest"] = time.perf_counter() - started

            started = time.perf_counter()
            audit = await self._c.claims.audit(case_id)
            score.seconds["audit"] = time.perf_counter() - started
            expected = Decimal(case.expected["payable_total"])
            got = audit.coverage.payable_total
            error = abs(got - expected) / max(expected, Decimal(1))
            score.payout_error = float(error)
            score.payout_ok = error <= _PAYOUT_TOLERANCE if expected else got <= Decimal(1)
            score.decision_ok = audit.coverage.decision == case.expected["decision"]
            score.line_recall, score.label_accuracy = _score_lines(audit, case.lines)
            score.terms = _score_terms(audit, case.terms)
            score.audit_verified = audit.verified

            if case.rejection is not None:
                started = time.perf_counter()
                review = await self._c.claims.review(case_id)
                score.seconds["review"] = time.perf_counter() - started
                verdicts = {r.verdict.value for r in review.review.reasons}
                score.review_verdict_ok = case.rejection["verdict"] in verdicts
                score.review_recommendation_ok = (
                    review.review.recommendation == case.rejection["recommendation"]
                )
                if case.rejection["recommendation"] == "appeal":
                    started = time.perf_counter()
                    letter = await self._c.claims.letter(case_id, LetterKind.GRIEVANCE)
                    score.seconds["letter"] = time.perf_counter() - started
                    score.letter_verified = letter.verified

            if chat:
                score.chat = await self._chat(case_id, case)
            await self._c.cases.delete(case_id)
        except Exception as exc:
            log.exception("eval case failed", extra={"case": case.case_id})
            score.error = f"{type(exc).__name__}: {exc}"[:300]
        return score

    async def _chat(self, case_id: str, case: SyntheticCase) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for conversation, turns in enumerate(case.qa):
            if conversation:
                await self._c.claims.clear_chat(case_id)
            for turn_index, turn in enumerate(turns):
                started = time.perf_counter()
                reply = await self._c.claims.chat(case_id, turn["question"])
                text = reply.text.lower()
                results.append(
                    {
                        "turn": turn_index,
                        "long": bool(turn.get("long_answer")),
                        "correct": any(e.lower() in text for e in turn["expect_any"]),
                        "cited": bool(reply.clauses),
                        "verified": reply.verified,
                        "seconds": time.perf_counter() - started,
                        "words": len(reply.text.split()),
                    }
                )
        return results

    async def model_usage(self, since: Any) -> list[dict[str, Any]]:
        async with self._c.sessions() as session:
            query = select(ModelCall).where(ModelCall.created_at >= since)
            rows = list(await session.scalars(query))
        grouped: dict[tuple[str, str, str], list[ModelCall]] = defaultdict(list)
        for row in rows:
            grouped[(row.task, row.provider, row.model)].append(row)
        usage = []
        for (task, provider, model), calls in sorted(grouped.items()):
            ok = [c for c in calls if c.outcome == "ok"]
            usage.append(
                {
                    "task": task,
                    "model": f"{provider}:{model}",
                    "attempts": len(calls),
                    "ok": len(ok),
                    "rate_limited": sum(c.outcome == "rate_limited" for c in calls),
                    "invalid_output": sum(c.outcome == "invalid_output" for c in calls),
                    "median_ms": int(statistics.median(c.latency_ms for c in ok)) if ok else None,
                }
            )
        return usage


def _rate(values: list[bool | None]) -> float | None:
    known = [v for v in values if v is not None]
    return round(sum(known) / len(known), 3) if known else None


def _mean(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return round(statistics.mean(known), 3) if known else None


def _median_pct(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return round(100 * statistics.median(known), 2) if known else None


def summarise(scores: list[CaseScore], usage: list[dict[str, Any]]) -> dict[str, Any]:
    done = [s for s in scores if s.error is None]
    chats = [turn for s in done for turn in s.chat]
    term_names = sorted({k for s in done for k in s.terms})
    timings: dict[str, list[float]] = defaultdict(list)
    for s in done:
        for step, seconds in s.seconds.items():
            timings[step].append(seconds)
    by_scenario: dict[str, list[CaseScore]] = defaultdict(list)
    for s in done:
        by_scenario[s.scenario].append(s)
    return {
        "generated_at": utcnow().isoformat(),
        "cases": len(scores),
        "failed": len(scores) - len(done),
        "payout_within_2pct": _rate([s.payout_ok for s in done]),
        "median_payout_error_pct": _median_pct([s.payout_error for s in done]),
        "decision_accuracy": _rate([s.decision_ok for s in done]),
        "line_recall": _mean([s.line_recall for s in done]),
        "label_accuracy": _mean([s.label_accuracy for s in done]),
        "terms_accuracy": {name: _rate([s.terms.get(name) for s in done]) for name in term_names},
        "audit_verified": _rate([s.audit_verified for s in done]),
        "review_verdict_accuracy": _rate([s.review_verdict_ok for s in done]),
        "review_recommendation_accuracy": _rate([s.review_recommendation_ok for s in done]),
        "letter_verified": _rate([s.letter_verified for s in done]),
        "chat": {
            "turns": len(chats),
            "correct": _rate([t["correct"] for t in chats]),
            "cited": _rate([t["cited"] for t in chats]),
            "follow_up_correct": _rate([t["correct"] for t in chats if t["turn"] > 0]),
            "long_answer_correct": _rate([t["correct"] for t in chats if t["long"]]),
            "median_seconds": round(statistics.median(t["seconds"] for t in chats), 1)
            if chats
            else None,
        },
        "median_seconds": {
            step: round(statistics.median(values), 1) for step, values in timings.items()
        },
        "payout_by_scenario": {
            name: _rate([s.payout_ok for s in group]) for name, group in sorted(by_scenario.items())
        },
        "models": usage,
        "errors": [{"case": s.case_id, "error": s.error} for s in scores if s.error],
    }


def render_markdown(summary: dict[str, Any]) -> str:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{100 * value:.0f}%"

    rows = [
        ("Settlement within 2% of the true amount", pct(summary["payout_within_2pct"])),
        ("Median settlement error", f"{summary['median_payout_error_pct']}%"),
        ("Payable / not payable decision", pct(summary["decision_accuracy"])),
        ("Bill lines found", pct(summary["line_recall"])),
        ("Bill lines labelled correctly", pct(summary["label_accuracy"])),
        ("Audits passing the checker", pct(summary["audit_verified"])),
        ("Insurer reasons judged correctly", pct(summary["review_verdict_accuracy"])),
        ("Appeal / accept recommendation", pct(summary["review_recommendation_accuracy"])),
        ("Letters passing the checker", pct(summary["letter_verified"])),
        ("Chat answers with the expected fact", pct(summary["chat"]["correct"])),
        ("Chat follow-up turns correct", pct(summary["chat"]["follow_up_correct"])),
        ("Chat long answers correct", pct(summary["chat"]["long_answer_correct"])),
        ("Chat answers citing a clause", pct(summary["chat"]["cited"])),
    ]
    terms = [(f"Policy term read: {k}", pct(v)) for k, v in summary["terms_accuracy"].items()]
    lines = [
        f"# Evaluation ({summary['cases']} synthetic cases, {summary['failed']} failed)",
        "",
        "| Metric | Result |",
        "| --- | --- |",
        *(f"| {name} | {value} |" for name, value in [*rows, *terms]),
        "",
        "## Median time per step (seconds)",
        "",
        "| Step | Seconds |",
        "| --- | --- |",
        *(f"| {k} | {v} |" for k, v in summary["median_seconds"].items()),
        "",
        "## Models used",
        "",
        "| Task | Model | Attempts | OK | Rate limited | Invalid output | Median ms |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *(
            f"| {m['task']} | {m['model']} | {m['attempts']} | {m['ok']} | {m['rate_limited']} "
            f"| {m['invalid_output']} | {m['median_ms'] or '-'} |"
            for m in summary["models"]
        ),
    ]
    return "\n".join(lines) + "\n"
