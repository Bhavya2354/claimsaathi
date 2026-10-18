import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.middleware import RateLimitMiddleware
from app.config import database_settings
from app.db.models import Base
from app.db.session import create_engine

CaseRef = tuple[str, dict[str, str]]


async def _create_tables() -> None:
    engine = create_engine(database_settings())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    asyncio.run(_create_tables())
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def case(client: TestClient) -> CaseRef:
    created = client.post("/api/cases").json()
    return created["id"], {"Authorization": f"Bearer {created['token']}"}


def test_readiness_reports_database_and_routes(client: TestClient) -> None:
    body = client.get("/health/ready").json()
    assert body["database"] == "ok"
    assert body["models"] == {"reasoning": 1, "fast": 1, "vision": 1}


def test_case_needs_its_token(client: TestClient, case: CaseRef) -> None:
    case_id, auth = case
    assert client.get(f"/api/cases/{case_id}", headers=auth).status_code == 200
    assert client.get(f"/api/cases/{case_id}").status_code == 401
    wrong = {"Authorization": "Bearer not-the-token"}
    assert client.get(f"/api/cases/{case_id}", headers=wrong).status_code == 401
    assert client.get("/api/cases/short-id", headers=auth).status_code == 422


def test_facts_update_and_validation(client: TestClient, case: CaseRef) -> None:
    case_id, auth = case
    response = client.patch(
        f"/api/cases/{case_id}/facts",
        headers=auth,
        json={"patient_age": 64, "sum_insured": "500000", "claim_type": "cashless"},
    )
    assert response.status_code == 200
    facts = response.json()["effective_facts"]
    assert facts["patient_age"] == 64
    assert facts["claim_type"] == "cashless"
    bad = client.patch(f"/api/cases/{case_id}/facts", headers=auth, json={"patient_age": 400})
    assert bad.status_code == 422
    unknown = client.patch(f"/api/cases/{case_id}/facts", headers=auth, json={"admin": True})
    assert unknown.status_code == 422


def test_uploads_are_checked_by_content(client: TestClient, case: CaseRef) -> None:
    case_id, auth = case
    response = client.post(
        f"/api/cases/{case_id}/documents",
        headers=auth,
        files={"file": ("bill.pdf", b"MZ\x90\x00 not really a pdf", "application/pdf")},
    )
    assert response.status_code == 422
    assert "PDF, PNG, JPEG or WEBP" in response.json()["detail"]


def test_audit_without_documents_explains_what_is_missing(
    client: TestClient, case: CaseRef
) -> None:
    case_id, auth = case
    response = client.post(f"/api/cases/{case_id}/audit", headers=auth)
    assert response.status_code == 409
    assert "policy document" in response.json()["detail"]


def test_checklist_follows_claim_type(client: TestClient, case: CaseRef) -> None:
    case_id, auth = case
    body = client.get(f"/api/cases/{case_id}/checklist?claim_type=cashless", headers=auth).json()
    assert {item["group"] for item in body["items"]} == {"common", "cashless"}


def test_deleted_cases_are_gone(client: TestClient, case: CaseRef) -> None:
    case_id, auth = case
    assert client.delete(f"/api/cases/{case_id}", headers=auth).status_code == 204
    assert client.get(f"/api/cases/{case_id}", headers=auth).status_code == 404


def test_responses_carry_request_id_and_security_headers(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "trace-123"})
    assert response.headers["x-request-id"] == "trace-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_rate_limiter_refills_over_time() -> None:
    limiter = RateLimitMiddleware(app=None, per_minute=60, burst=2, trusted_proxy_hops=0)  # type: ignore[arg-type]
    assert limiter._take("1.2.3.4") == 0
    assert limiter._take("1.2.3.4") == 0
    wait = limiter._take("1.2.3.4")
    assert 0 < wait <= 1
    assert limiter._take("5.6.7.8") == 0  # buckets are per client
