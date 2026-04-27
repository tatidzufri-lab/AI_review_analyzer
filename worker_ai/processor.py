import json
import logging
import re

from openai import AsyncOpenAI

from config import get_settings
from models import ReviewTone


logger = logging.getLogger("worker.processor")
settings = get_settings()


# --- Fallback-классификатор: используется только если нет OPENAI_API_KEY ---
# Намеренно консервативный: без многозначных слов вроде "долго" или "проблем",
# чтобы не ставить негативный тон на «долго думала, ехать или нет».
POSITIVE_MARKERS = (
    "спасибо", "благодар", "отлично", "супер", "класс", "хорош", "понрав",
    "рекоменд", "тёпл", "тепл", "прекрас", "идеально", "восхит", "люб",
    "чудес", "вдохнов", "счастлив", "лучш", "love", "great", "awesome",
)
NEGATIVE_MARKERS = (
    "плохо", "ужас", "отврат", "разочар", "недоволен", "ненави", "обма",
    "не понрав", "испорти", "грязн", "хам", "груб", "bad", "terrible", "awful",
)


def detect_tone_keywords(review_text: str) -> ReviewTone:
    """Кейворд-классификатор как fallback на случай отсутствия OpenAI."""
    text = review_text.lower()
    positive_score = sum(1 for marker in POSITIVE_MARKERS if marker in text)
    negative_score = sum(1 for marker in NEGATIVE_MARKERS if marker in text)

    if negative_score > positive_score:
        return ReviewTone.NEGATIVE
    if positive_score > negative_score:
        return ReviewTone.POSITIVE
    return ReviewTone.NEUTRAL


def build_fallback_response(review_text: str) -> str:
    tone = detect_tone_keywords(review_text)

    if tone == ReviewTone.NEGATIVE:
        return (
            "Нам жаль, что у вас остались негативные впечатления. "
            "Спасибо, что сообщили об этом. Пожалуйста, свяжитесь с нашей поддержкой, "
            "и мы постараемся помочь как можно быстрее."
        )

    if tone == ReviewTone.POSITIVE:
        return (
            "Спасибо за ваш отзыв и добрые слова. Нам очень приятно, "
            "что у вас остались положительные впечатления."
        )

    return (
        "Спасибо за ваш отзыв. Мы внимательно его изучили и учтем ваши замечания. "
        "Если захотите, можете поделиться деталями, чтобы мы смогли отреагировать точнее."
    )


_SYSTEM_PROMPT = (
    "Ты помощник поддержки женского ретрита «Познай себя». "
    "Тебе на вход приходит отзыв или комментарий участницы — позитивный, "
    "негативный, нейтральный, короткий или эмоциональный.\n\n"
    "Твоя задача — вернуть ровно один JSON-объект (без обрамления code-блоками) с двумя полями:\n"
    "  \"tone\": \"positive\" | \"neutral\" | \"negative\" — общий тон отзыва;\n"
    "  \"reply\": строка — естественный, вежливый ответ от лица команды ретрита, не длиннее 3 предложений.\n\n"
    "Правила оценки тона:\n"
    "  - positive: благодарность, восторг, рекомендация, тёплые впечатления (даже если есть мелкие замечания).\n"
    "  - negative: тон ставится ТОЛЬКО если в тексте есть конкретная претензия, "
    "жалоба на опыт, описание проблемы, гневная оценка («ужас», «отвратительно», "
    "«не понравилось», «обманули», «грубо», «грязно» и т. п.).\n"
    "  - neutral: констатация фактов, вопрос, нейтральный комментарий, смешанные впечатления, "
    "слишком короткий или двусмысленный текст без явного перевеса.\n\n"
    "Слова вроде «долго», «много», «не хватало», «жаль» сами по себе не делают отзыв негативным — "
    "часто это сожаление автора о себе, а не претензия к нам. Оценивай смысл целиком.\n\n"
    "Если отзыв можно прочесть и как позитивный, и как негативный (опечатки, "
    "оборванные фразы, двусмысленность) — всегда выбирай neutral. "
    "Никогда не извиняйся и не предполагай негативный опыт, если в тексте нет конкретной претензии.\n\n"
    "Примеры:\n"
    "  Отзыв: «Ужасно» → tone=negative («ужасно» — прямая негативная оценка).\n"
    "  Отзыв: «Нормальный ретрит» → tone=neutral (ровная нейтральная оценка).\n"
    "  Отзыв: «Очень жаль, что не попала сюда раньше» → tone=positive "
    "(автор сожалеет о себе, что не познакомилась раньше — это похвала).\n"
    "  Отзыв: «Жаль, что не пригла раньше» → tone=neutral "
    "(текст оборван, неясно, кто кого не пригласил, — без явной претензии трактуй как neutral).\n"
    "  Отзыв: «Долго сомневалась, ехать или нет, но это была лучшая инвестиция в себя» → tone=positive.\n\n"
    "Правила ответа:\n"
    "  - Пиши на русском языке.\n"
    "  - Если тон позитивный — поблагодари искренне, без шаблонов.\n"
    "  - Если негативный — мягко извинись и предложи разобраться.\n"
    "  - Если нейтральный — отреагируй по существу, без навязчивых уточнений.\n"
    "  - Без markdown, канцелярита, подписей и дословного повторения отзыва.\n"
    "  - Никогда не отвечай на английском или другом языке."
)


def _parse_analysis(raw: str) -> tuple[ReviewTone | None, str | None]:
    """Извлекаем JSON-объект {tone, reply} из ответа модели."""
    if not raw:
        return None, None

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).rstrip("`").strip()

    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        return None, None

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, None

    tone_raw = (data.get("tone") or "").strip().lower()
    reply = (data.get("reply") or "").strip()
    tone: ReviewTone | None = None
    if tone_raw in {"positive", "negative", "neutral"}:
        tone = ReviewTone(tone_raw)
    return tone, reply or None


async def analyze_review(review_text: str) -> tuple[ReviewTone, str]:
    """Возвращает (тон, текст ответа) — за один LLM-вызов или через fallback."""
    if not settings.openai_api_key:
        logger.info("OPENAI_API_KEY is not set, using keyword fallback")
        return detect_tone_keywords(review_text), build_fallback_response(review_text)

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    prompt = f"Отзыв: {review_text}"

    try:
        response = await client.responses.create(
            model=settings.openai_model,
            instructions=_SYSTEM_PROMPT,
            input=prompt,
        )
        raw = (response.output_text or "").strip()
        tone, reply = _parse_analysis(raw)
        if tone is not None and reply:
            return tone, reply
        logger.warning("OpenAI returned unparsable analysis %r, using fallback", raw[:200])
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenAI request failed, using fallback: %s", exc)

    return detect_tone_keywords(review_text), build_fallback_response(review_text)


# --- Обратная совместимость со старым worker.py: тон + ответ по отдельности.
# Новый worker.py зовёт `analyze_review`, эти функции остаются как fallback-API.

def detect_tone(review_text: str) -> ReviewTone:
    return detect_tone_keywords(review_text)


async def generate_response(review_text: str) -> str:
    _tone, reply = await analyze_review(review_text)
    return reply
