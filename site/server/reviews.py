"""SQLite-хранилище отзывов с API, совместимым с worker_ai/client.py.

Модель полей повторяет worker_ai/models.py::RemoteReview:
    id, parent_id, name, text, status (new/processed),
    response, tone (positive/neutral/negative), created_at.

PATCH защищён заголовком X-Worker-Token.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    select,
    text as sql_text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


logger = logging.getLogger("retreat.reviews")


REVIEW_STATUSES = ("new", "processed")
REVIEW_TONES = ("positive", "negative", "neutral")
REVIEW_LANGS = ("ru", "en")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def detect_lang(text: str) -> str:
    """Язык отзыва по тексту: кириллица — русский, иначе английский."""
    return "ru" if _CYRILLIC.search(text or "") else "en"


SERVER_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = SERVER_DIR / "data" / "reviews.db"


class _Base(DeclarativeBase):
    pass


class Review(_Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_id = Column(
        Integer,
        ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name = Column(String(120), nullable=True)
    text = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="new", index=True)
    response = Column(Text, nullable=True)
    tone = Column(String(20), nullable=True)
    # Язык страницы, на которой оставлен отзыв: ru (/) или en (/en/).
    lang = Column(String(5), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "name": self.name,
            "text": self.text,
            "status": self.status,
            "response": self.response,
            "tone": self.tone,
            "lang": self.lang or detect_lang(self.text),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _build_engine(db_url: str | None = None):
    url = db_url or os.getenv("REVIEWS_DATABASE_URL")
    if not url:
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{DEFAULT_DB_PATH}"
    return create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})


_engine = _build_engine()
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    _Base.metadata.create_all(bind=_engine)
    _migrate_lang_column()


def _migrate_lang_column() -> None:
    """База на сервере создана до появления колонки lang: добавляем и заполняем по тексту."""
    columns = {c["name"] for c in inspect(_engine).get_columns("reviews")}
    if "lang" in columns:
        return
    with _engine.begin() as conn:
        conn.execute(sql_text("ALTER TABLE reviews ADD COLUMN lang VARCHAR(5)"))
    with _SessionLocal() as session:
        rows = session.execute(select(Review)).scalars().all()
        parents = {r.id: r for r in rows}
        for r in rows:
            if r.parent_id is None:
                r.lang = detect_lang(r.text)
        for r in rows:
            if r.parent_id is not None:
                parent = parents.get(r.parent_id)
                r.lang = parent.lang if parent is not None and parent.lang else detect_lang(r.text)
        session.commit()
        logger.info("Added lang column to %s reviews", len(rows))


def seed_demo_reviews() -> int:
    init_db()
    demo = [
        ("Марина", "Вернулась с ретрита другим человеком. Тишина, горы и бережное "
                  "сопровождение Анны — то, чего мне не хватало. Спасибо!"),
        ("Екатерина", "Долго сомневалась, ехать или нет. В итоге это была лучшая инвестиция "
                       "в себя за последние годы. Камерная группа, всё по делу."),
        ("Ольга", "Программа очень насыщенная, иногда хотелось больше свободного времени. "
                  "Но в целом впечатления тёплые."),
    ]
    demo_en = [
        ("Emily", "I came home a different person. The silence, the mountains and Anna's gentle "
                  "guidance were exactly what I needed. Thank you!"),
        ("Sophie", "I hesitated for a long time about going. In the end it was the best investment "
                   "in myself in years. A small group, and everything was to the point."),
        ("Laura", "The program is very full, and sometimes I wanted more free time. "
                  "But overall my impressions are warm."),
    ]
    added = 0
    with _SessionLocal() as session:
        existing = session.execute(select(Review).limit(1)).scalar_one_or_none()
        if existing is None:
            for name, text in demo:
                session.add(Review(name=name, text=text, status="new", lang="ru"))
            added += len(demo)
        # Английская страница появилась позже: свои стартовые отзывы, если их еще нет.
        has_en = session.execute(
            select(Review).where(Review.lang == "en", Review.parent_id.is_(None)).limit(1)
        ).scalar_one_or_none()
        if has_en is None:
            for name, text in demo_en:
                session.add(Review(name=name, text=text, status="new", lang="en"))
            added += len(demo_en)
        session.commit()
    if added:
        logger.info("Seeded %s demo reviews", added)
    return added


def _worker_token() -> str:
    return os.getenv("WORKER_API_TOKEN", "change-me")


router = APIRouter(prefix="/api", tags=["reviews"])


@router.get("/reviews")
async def list_reviews(lang: str | None = Query(default=None)) -> list[dict[str, Any]]:
    """Без lang — все отзывы (так их читает воркер). С lang — только этого языка и ответы на них."""
    with _SessionLocal() as session:
        rows = session.execute(select(Review).order_by(Review.created_at.asc())).scalars().all()
        items = [r.to_dict() for r in rows]
    if lang not in REVIEW_LANGS:
        return items
    roots = {r["id"] for r in items if r["parent_id"] is None and r["lang"] == lang}
    return [r for r in items if r["id"] in roots or r["parent_id"] in roots]


@router.get("/reviews/{review_id}")
async def get_review(review_id: int) -> dict[str, Any]:
    with _SessionLocal() as session:
        review = session.get(Review, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        return review.to_dict()


@router.post("/reviews")
async def create_review(request: Request) -> JSONResponse:
    """Принимает payload, совместимый с worker_ai.ReviewCreatePayload.

    Поля: parent_id (int|None), name (str|None), text (str, обязательно).

    Идемпотентность: при наличии parent_id и уже существующего ответа на этот
    родительский отзыв возвращается существующая запись со статусом 200,
    а не создаётся новая. Это защищает от дубликатов при ретраях воркера.
    """
    payload = await _safe_json(request)

    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Field 'text' is required")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Field 'text' must be 2000 characters or less")

    name = (payload.get("name") or "").strip() or None
    if name and len(name) > 120:
        raise HTTPException(status_code=400, detail="Field 'name' must be 120 characters or less")

    parent_id = payload.get("parent_id")
    if parent_id is not None:
        try:
            parent_id = int(parent_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Field 'parent_id' must be int")

    lang = payload.get("lang")
    with _SessionLocal() as session:
        if parent_id is not None:
            parent = session.get(Review, parent_id)
            if parent is None:
                raise HTTPException(status_code=404, detail="Parent review not found")
            lang = parent.lang  # ответ живет на той же странице, что и отзыв

            existing_reply = session.execute(
                select(Review)
                .where(Review.parent_id == parent_id)
                .order_by(Review.id.asc())
            ).scalars().first()
            if existing_reply is not None:
                logger.info(
                    "Idempotent: returning existing reply id=%s for parent_id=%s",
                    existing_reply.id, parent_id,
                )
                return JSONResponse(status_code=200, content=existing_reply.to_dict())

        if lang not in REVIEW_LANGS:
            lang = detect_lang(text)
        review = Review(parent_id=parent_id, name=name, text=text, status="new", lang=lang)
        session.add(review)
        session.commit()
        session.refresh(review)
        logger.info("Review created id=%s parent_id=%s", review.id, review.parent_id)
        return JSONResponse(status_code=201, content=review.to_dict())


@router.patch("/reviews/{review_id}")
async def update_review(
    review_id: int,
    request: Request,
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
) -> dict[str, Any]:
    """Обновление воркером: status, tone, response. Требует X-Worker-Token."""
    if not x_worker_token or x_worker_token != _worker_token():
        logger.warning("Unauthorized worker access from %s", request.client.host if request.client else "?")
        raise HTTPException(status_code=401, detail="Invalid or missing worker token")

    payload = await _safe_json(request)

    with _SessionLocal() as session:
        review = session.get(Review, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")

        if "status" in payload and payload["status"] is not None:
            if payload["status"] not in REVIEW_STATUSES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status; allowed: {REVIEW_STATUSES}",
                )
            review.status = payload["status"]

        if "tone" in payload and payload["tone"] is not None:
            if payload["tone"] not in REVIEW_TONES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid tone; allowed: {REVIEW_TONES}",
                )
            review.tone = payload["tone"]

        if "response" in payload:
            text = payload["response"]
            review.response = text.strip() if text else None

        session.commit()
        session.refresh(review)
        logger.info(
            "Review id=%s updated: status=%s tone=%s response=%s",
            review_id, review.status, review.tone, bool(review.response),
        )
        return review.to_dict()


async def _safe_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}
