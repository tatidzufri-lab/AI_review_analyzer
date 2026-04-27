/**
 * Подключает секцию отзывов лендинга к API `server/main.py`.
 *
 * Контракт API совпадает с `worker_ai/client.py`:
 *   GET  /api/reviews            -> [Review, ...]
 *   POST /api/reviews            -> Review (создание отзыва или ответа на него)
 *
 * Обновления (status / tone / response) приходят от воркера через PATCH с
 * заголовком X-Worker-Token; сюда отдаются уже готовые поля.
 */
const REVIEWS_CONFIG = {
  /** Базовый URL FastAPI-бэкенда. На проде — origin продакшна. */
  apiBase: "",
  /** Имя AI-ассистента в подписи к ответу. */
  aiAuthor: "AI-ассистент ретрита",
  /** Сколько миллисекунд держать сообщение об отправке. */
  feedbackTimeoutMs: 6000,
};

(function () {
  "use strict";

  function $(selector, root) {
    return (root || document).querySelector(selector);
  }
  function $all(selector, root) {
    return Array.from((root || document).querySelectorAll(selector));
  }

  function init() {
    var root = $("[data-reviews-root]");
    if (!root) return;

    var listEl = $("[data-reviews-list]", root);
    var formEl = $("[data-reviews-form]", root);
    var feedbackEl = $("[data-reviews-feedback]", root);

    var stats = {
      total: $("[data-reviews-total]", root),
      positive: $("[data-reviews-positive]", root),
      neutral: $("[data-reviews-neutral]", root),
      negative: $("[data-reviews-negative]", root),
    };

    function setFeedback(message, kind) {
      if (!feedbackEl) return;
      feedbackEl.textContent = message || "";
      feedbackEl.classList.remove(
        "reviews-form__feedback--success",
        "reviews-form__feedback--error"
      );
      if (kind === "success") feedbackEl.classList.add("reviews-form__feedback--success");
      if (kind === "error") feedbackEl.classList.add("reviews-form__feedback--error");
      if (message && kind === "success") {
        setTimeout(function () {
          if (feedbackEl.textContent === message) setFeedback("");
        }, REVIEWS_CONFIG.feedbackTimeoutMs);
      }
    }

    function fmtDate(iso) {
      if (!iso) return "";
      try {
        var d = new Date(iso);
        return d.toLocaleString("ru-RU", {
          day: "2-digit",
          month: "long",
          year: "numeric",
        });
      } catch (_e) {
        return iso;
      }
    }

    function toneLabel(tone) {
      if (tone === "positive") return "тёплый";
      if (tone === "negative") return "требует внимания";
      if (tone === "neutral") return "нейтральный";
      return "новый";
    }

    function buildCard(review, replies) {
      var card = document.createElement("article");
      card.className = "review-card";

      var header = document.createElement("div");
      header.className = "review-card__header";

      var author = document.createElement("span");
      author.className = "review-card__author";
      author.textContent = review.name || "Аноним";
      header.appendChild(author);

      var date = document.createElement("span");
      date.className = "review-card__date";
      date.textContent = fmtDate(review.created_at);
      header.appendChild(date);

      var tone = document.createElement("span");
      tone.className =
        "review-card__tone review-card__tone--" + (review.tone || "neutral");
      tone.textContent = toneLabel(review.tone);
      header.appendChild(tone);

      var status = document.createElement("span");
      status.className = "review-card__status";
      status.textContent = review.status === "processed" ? "обработан" : "в очереди";
      header.appendChild(status);

      card.appendChild(header);

      var text = document.createElement("p");
      text.className = "review-card__text";
      text.textContent = review.text;
      card.appendChild(text);

      if (review.response) {
        card.appendChild(buildReply(REVIEWS_CONFIG.aiAuthor, review.response));
      }

      (replies || []).forEach(function (child) {
        card.appendChild(buildReply(child.name || REVIEWS_CONFIG.aiAuthor, child.text));
      });

      return card;
    }

    function buildReply(author, body) {
      var wrap = document.createElement("div");
      wrap.className = "review-card__reply";
      var label = document.createElement("span");
      label.className = "review-card__reply-label";
      label.textContent = author;
      var p = document.createElement("p");
      p.className = "review-card__reply-text";
      p.textContent = body;
      wrap.appendChild(label);
      wrap.appendChild(p);
      return wrap;
    }

    function renderReviews(items) {
      listEl.innerHTML = "";
      var roots = items.filter(function (r) {
        return r.parent_id == null;
      });
      var byParent = items.reduce(function (acc, r) {
        if (r.parent_id != null) {
          (acc[r.parent_id] = acc[r.parent_id] || []).push(r);
        }
        return acc;
      }, {});

      if (roots.length === 0) {
        var empty = document.createElement("p");
        empty.className = "reviews-list__empty";
        empty.textContent = "Будьте первой, кто поделится впечатлением.";
        listEl.appendChild(empty);
        return;
      }

      var sorted = roots.slice().sort(function (a, b) {
        return (b.created_at || "").localeCompare(a.created_at || "");
      });
      sorted.forEach(function (root) {
        listEl.appendChild(buildCard(root, byParent[root.id] || []));
      });
    }

    function renderStats(items) {
      var counts = { total: 0, positive: 0, neutral: 0, negative: 0 };
      items
        .filter(function (r) {
          return r.parent_id == null;
        })
        .forEach(function (r) {
          counts.total += 1;
          if (r.tone === "positive") counts.positive += 1;
          else if (r.tone === "negative") counts.negative += 1;
          else if (r.tone === "neutral") counts.neutral += 1;
        });
      if (stats.total) stats.total.textContent = String(counts.total);
      if (stats.positive) stats.positive.textContent = String(counts.positive);
      if (stats.neutral) stats.neutral.textContent = String(counts.neutral);
      if (stats.negative) stats.negative.textContent = String(counts.negative);
    }

    function loadReviews() {
      return fetch(REVIEWS_CONFIG.apiBase + "/api/reviews", {
        headers: { Accept: "application/json" },
      })
        .then(function (res) {
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.json();
        })
        .then(function (items) {
          if (!Array.isArray(items)) items = [];
          renderStats(items);
          renderReviews(items);
        })
        .catch(function (err) {
          listEl.innerHTML = "";
          var p = document.createElement("p");
          p.className = "reviews-list__placeholder";
          p.textContent =
            "Не удалось загрузить отзывы (" + (err.message || "ошибка") + ").";
          listEl.appendChild(p);
        });
    }

    if (formEl) {
      formEl.addEventListener("submit", function (event) {
        event.preventDefault();
        var nameInput = $("#review-name", formEl);
        var textInput = $("#review-text", formEl);
        var text = (textInput && textInput.value || "").trim();
        if (!text) {
          setFeedback("Напишите, пожалуйста, текст отзыва.", "error");
          return;
        }
        var payload = { text: text };
        var name = (nameInput && nameInput.value || "").trim();
        if (name) payload.name = name;

        var submitBtn = formEl.querySelector('button[type="submit"]');
        if (submitBtn) submitBtn.disabled = true;
        setFeedback("Отправляем…");

        fetch(REVIEWS_CONFIG.apiBase + "/api/reviews", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify(payload),
        })
          .then(function (res) {
            return res.json().then(function (body) {
              if (!res.ok) {
                throw new Error(
                  (body && body.detail) ||
                    "Не удалось сохранить отзыв (" + res.status + ")"
                );
              }
              return body;
            });
          })
          .then(function () {
            formEl.reset();
            setFeedback(
              "Спасибо! Отзыв отправлен. AI-ассистент ответит в ближайшее время.",
              "success"
            );
            loadReviews();
          })
          .catch(function (err) {
            setFeedback(err.message || "Ошибка отправки", "error");
          })
          .finally(function () {
            if (submitBtn) submitBtn.disabled = false;
          });
      });
    }

    loadReviews();
    setInterval(loadReviews, 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
