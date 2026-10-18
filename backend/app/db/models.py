"""Tables. Text from documents is stored masked; the PII vault is stored encrypted."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Json = JSON().with_variant(JSONB(), "postgresql")


def _id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this mapping at class creation
        dict[str, Any]: Json,
        list[Any]: Json,
        datetime: DateTime(timezone=True),
    }


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    facts: Mapped[dict[str, Any]] = mapped_column(default=dict)
    terms_reading: Mapped[dict[str, Any] | None]
    labelled_lines: Mapped[list[Any] | None]
    pii_vault: Mapped[bytes | None] = mapped_column(LargeBinary)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="Document.created_at"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    filename: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="processing")
    error: Mapped[str | None] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(64))
    sha256: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    text_sources: Mapped[list[Any] | None]
    masked_text: Mapped[str | None] = mapped_column(Text)
    extracted: Mapped[dict[str, Any] | None]
    clause_count: Mapped[int | None] = mapped_column(Integer)

    case: Mapped[Case] = relationship(back_populates="documents")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    intent: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict[str, Any]]
    verified: Mapped[bool] = mapped_column(Boolean)
    issues: Mapped[list[Any]] = mapped_column(default=list)
    steps: Mapped[list[Any]] = mapped_column(default=list)


class Letter(Base):
    __tablename__ = "letters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    kind: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    clause_ids: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    verified: Mapped[bool] = mapped_column(Boolean)
    issues: Mapped[list[Any]] = mapped_column(default=list)
    approved_at: Mapped[datetime | None]


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    clause_ids: Mapped[list[Any]] = mapped_column(default=list)
    verified: Mapped[bool | None] = mapped_column(Boolean)


class ModelCall(Base):
    """One attempt against one model; feeds the model comparison report."""

    __tablename__ = "model_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), index=True)
    agent: Mapped[str | None] = mapped_column(String(32))
    task: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(120))
    outcome: Mapped[str] = mapped_column(String(16))
    latency_ms: Mapped[int] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(500))


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(36))
    helpful: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(1000))
