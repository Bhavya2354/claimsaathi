"""Composition root: builds every component once per process from settings."""

from dataclasses import dataclass

import httpx
import truststore
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.advocate import ClaimAdvocate
from app.agents.auditor import BillAuditor
from app.agents.checker import Checker
from app.agents.graph import ClaimGraph
from app.agents.policy_expert import PolicyExpert
from app.config import (
    agent_settings,
    api_settings,
    case_settings,
    database_settings,
    model_call_settings,
    privacy_settings,
    provider_settings,
    retrieval_settings,
    route_settings,
)
from app.db.session import create_engine, session_factory
from app.documents.extraction import Extractor
from app.documents.ocr import OcrEngine
from app.documents.privacy import PiiMasker
from app.documents.reader import DocumentReader
from app.llm.router import ModelRouter, build_router
from app.policy.index import PolicyIndex
from app.rules.catalogue import load_catalogue
from app.services.cases import CaseService
from app.services.claims import ClaimService
from app.services.ingest import IngestService
from app.services.reads import CaseReads
from app.services.telemetry import DatabaseCallRecorder


@dataclass(frozen=True, slots=True)
class Container:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    router: ModelRouter
    index: PolicyIndex
    masker: PiiMasker
    cases: CaseService
    ingest: IngestService
    claims: ClaimService
    reads: CaseReads

    async def close(self) -> None:
        await self.index.close()
        await self.http.aclose()
        await self.engine.dispose()


def build_container() -> Container:
    # Every setting section is loaded here, so a misconfigured process fails at boot.
    api_settings()
    agents = agent_settings()
    retrieval = retrieval_settings()
    limits = case_settings()

    engine = create_engine(database_settings())
    sessions = session_factory(engine)
    # The OS trust store: corporate proxies and antivirus TLS inspection break certifi alone.
    http = httpx.AsyncClient(verify=truststore.SSLContext(), follow_redirects=False)
    router = build_router(
        http,
        provider_settings(),
        route_settings(),
        model_call_settings(),
        DatabaseCallRecorder(sessions),
    )
    index = PolicyIndex(retrieval)
    masker = PiiMasker(privacy_settings())
    reader = DocumentReader(OcrEngine(router), limits, agents)
    expert = PolicyExpert(router, index, retrieval)
    graph = ClaimGraph(
        router=router,
        expert=expert,
        auditor=BillAuditor(router, load_catalogue(), agents),
        advocate=ClaimAdvocate(router, index, retrieval),
        checker=Checker(router, retrieval),
        settings=agents,
    )
    return Container(
        engine=engine,
        sessions=sessions,
        http=http,
        router=router,
        index=index,
        masker=masker,
        cases=CaseService(sessions, index, limits),
        ingest=IngestService(
            sessions=sessions,
            reader=reader,
            masker=masker,
            extractor=Extractor(router),
            index=index,
            retrieval=retrieval,
            limits=limits,
        ),
        claims=ClaimService(sessions, graph, masker, agents.chat_history_turns),
        reads=CaseReads(masker, index),
    )
