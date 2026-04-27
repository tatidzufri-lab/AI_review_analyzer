#!/usr/bin/env python3
"""Универсальный скрипт управления связкой `site/` (FastAPI + лендинг) и `worker_ai/`.

Команды:
    init           — создаёт venv, ставит зависимости, инициализирует БД, сидит демо-отзывы.
    serve          — поднимает FastAPI-бэкенд на http://127.0.0.1:8000 (отзывы + ChatKit).
    static         — простой http.server для лендинга на http://127.0.0.1:8080.
    smoke-test     — проверяет API на совместимость с worker_ai (GET/POST/PATCH /api/reviews).
    worker         — запускает worker_ai/worker.py с настроенным TARGET_SITE_URL.
    snippet        — генерирует автономный HTML/CSS/JS-снипет секции отзывов.
    reset-review   — сбрасывает указанные отзывы обратно в status=new (полезно после smoke-test).
    deploy         — заливает site/ на удалённый сервер (rsync + ssh).

Скрипт ничего не делает «волшебно» — это удобная обёртка над уже существующими
артефактами, чтобы не вспоминать команды руками.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT / "site"
SERVER_DIR = SITE_DIR / "server"
WORKER_DIR = ROOT / "worker_ai"
SITE_VENV = SERVER_DIR / ".venv"
WORKER_VENV = WORKER_DIR / ".venv"
OUTPUT_DIR = ROOT / "output"
DEFAULT_API_BASE = "http://127.0.0.1:8000"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("update_site")


# ----------------------------- helpers -----------------------------

def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _ensure_venv(venv: Path) -> Path:
    if not venv.exists():
        log.info("Создаю venv: %s", venv)
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    return _venv_python(venv)


def _pip_install(python: Path, requirements: Path) -> None:
    log.info("Устанавливаю зависимости из %s", requirements)
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run(
        [str(python), "-m", "pip", "install", "-r", str(requirements)],
        check=True,
    )


def _http_json(method: str, url: str, payload: dict | None = None, headers: dict | None = None) -> tuple[int, dict | list | None]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8") or "null"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") or ""
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


# ----------------------------- commands -----------------------------

def cmd_init(_: argparse.Namespace) -> None:
    site_python = _ensure_venv(SITE_VENV)
    _pip_install(site_python, SERVER_DIR / "requirements.txt")

    if not (SERVER_DIR / ".env").exists():
        shutil.copy(SERVER_DIR / ".env.example", SERVER_DIR / ".env")
        log.info("Создал site/server/.env (отредактируйте OPENAI_API_KEY и токены).")

    log.info("Инициализирую БД и сею демо-отзывы…")
    subprocess.run(
        [str(site_python), "-c",
         "from reviews import init_db, seed_demo_reviews; init_db(); print('seeded:', seed_demo_reviews())"],
        cwd=SERVER_DIR,
        check=True,
    )

    if WORKER_DIR.exists():
        worker_python = _ensure_venv(WORKER_VENV)
        _pip_install(worker_python, WORKER_DIR / "requirements.txt")
        if not (WORKER_DIR / ".env").exists() and (WORKER_DIR / ".env.example").exists():
            shutil.copy(WORKER_DIR / ".env.example", WORKER_DIR / ".env")
            log.info("Создал worker_ai/.env.")

    log.info("Готово. Запустите бэкенд: python update_site.py serve")


def cmd_serve(args: argparse.Namespace) -> None:
    site_python = _venv_python(SITE_VENV) if SITE_VENV.exists() else Path(sys.executable)
    log.info("Запуск FastAPI на %s:%s (Ctrl+C — стоп)", args.host, args.port)
    subprocess.run(
        [
            str(site_python), "-m", "uvicorn", "main:app",
            "--host", args.host,
            "--port", str(args.port),
            *( ["--reload"] if args.reload else [] ),
        ],
        cwd=SERVER_DIR,
        check=False,
    )


def cmd_static(args: argparse.Namespace) -> None:
    log.info("Лендинг на http://%s:%s/index.html", args.host, args.port)
    subprocess.run(
        [sys.executable, "-m", "http.server", str(args.port), "--bind", args.host],
        cwd=SITE_DIR,
        check=False,
    )


def cmd_smoke_test(args: argparse.Namespace) -> None:
    base = args.api_base.rstrip("/")
    token = args.worker_token
    log.info("Smoke-test API %s", base)

    status, payload = _http_json("GET", f"{base}/api/reviews")
    assert status == 200 and isinstance(payload, list), f"GET reviews → {status} {payload}"
    log.info("GET /api/reviews → OK (%s записей)", len(payload))

    status, created = _http_json("POST", f"{base}/api/reviews", {
        "name": "Smoke-test",
        "text": "Автотест: проверяю интеграцию worker_ai.",
    })
    assert status == 201 and isinstance(created, dict) and "id" in created, f"POST reviews → {status} {created}"
    review_id = created["id"]
    log.info("POST /api/reviews → OK (id=%s)", review_id)

    status, updated = _http_json(
        "PATCH",
        f"{base}/api/reviews/{review_id}",
        {"status": "processed", "tone": "positive", "response": "Спасибо за отзыв!"},
        headers={"X-Worker-Token": token},
    )
    assert status == 200 and isinstance(updated, dict), f"PATCH reviews → {status} {updated}"
    assert updated.get("status") == "processed", updated
    assert updated.get("tone") == "positive", updated
    log.info("PATCH /api/reviews/%s → OK", review_id)

    status, _ = _http_json(
        "PATCH",
        f"{base}/api/reviews/{review_id}",
        {"status": "processed"},
        headers={"X-Worker-Token": "bad-token"},
    )
    assert status == 401, f"PATCH без токена должен быть 401, получили {status}"
    log.info("PATCH без валидного токена → 401 (как и ожидалось)")

    log.info("Smoke-test пройден.")


def cmd_worker(args: argparse.Namespace) -> None:
    if not WORKER_DIR.exists():
        log.error("Папка worker_ai/ не найдена.")
        sys.exit(1)
    python = _venv_python(WORKER_VENV) if WORKER_VENV.exists() else Path(sys.executable)
    env = os.environ.copy()
    env.setdefault("TARGET_SITE_URL", args.api_base)
    log.info("Запускаю worker_ai (TARGET_SITE_URL=%s). Ctrl+C — стоп.", env["TARGET_SITE_URL"])
    subprocess.run([str(python), "worker.py"], cwd=WORKER_DIR, env=env, check=False)


SNIPPET_TEMPLATE = """<!-- Reviews snippet for retreat «Познай себя».
     Подключите блок целиком в любую страницу (Tilda, статический HTML, CMS).
     Бэкенд: {api_base}/api/reviews -->
<section class="rsnip" data-rsnip-root>
  <style>
    .rsnip{{font-family:Nunito,system-ui,sans-serif;color:#2c1a0e;background:#f0ebe3;padding:48px 16px;border-radius:24px;}}
    .rsnip h2{{font-family:'Playfair Display',Georgia,serif;font-size:2rem;text-align:center;margin:0 0 8px;}}
    .rsnip p{{margin:0 0 16px;}}
    .rsnip-list{{display:grid;gap:16px;margin:24px 0;}}
    .rsnip-card{{background:#fff;border-radius:14px;padding:20px;box-shadow:0 12px 36px rgba(44,26,14,.08);border:1px solid rgba(44,26,14,.05);}}
    .rsnip-card__head{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px;}}
    .rsnip-card__author{{font-weight:700;}}
    .rsnip-card__date{{color:rgba(44,26,14,.65);font-size:.85rem;}}
    .rsnip-card__tone{{font-size:.7rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;padding:4px 10px;border-radius:999px;background:rgba(44,26,14,.08);color:rgba(44,26,14,.65);}}
    .rsnip-card__tone--positive{{background:rgba(122,158,126,.18);color:#5e7d62;}}
    .rsnip-card__tone--negative{{background:rgba(196,113,79,.18);color:#a85d3f;}}
    .rsnip-card__reply{{margin-top:12px;padding:10px 14px;border-left:3px solid #7a9e7e;background:#faf6f0;border-radius:8px;}}
    .rsnip-card__reply-label{{display:block;font-size:.7rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#7a9e7e;margin-bottom:4px;}}
    .rsnip-form{{background:#fff;border-radius:18px;padding:24px;box-shadow:0 14px 44px rgba(44,26,14,.08);}}
    .rsnip-form input,.rsnip-form textarea{{width:100%;padding:10px 14px;border:1px solid rgba(44,26,14,.12);border-radius:8px;background:#faf6f0;font-family:inherit;font-size:1rem;margin-bottom:12px;}}
    .rsnip-form textarea{{min-height:120px;resize:vertical;}}
    .rsnip-form button{{display:inline-flex;justify-content:center;width:100%;padding:12px 24px;border:none;border-radius:999px;background:#c4714f;color:#fff;font-weight:700;cursor:pointer;}}
    .rsnip-form button:hover{{background:#a85d3f;}}
    .rsnip-feedback{{margin-top:8px;font-weight:600;font-size:.9rem;}}
    .rsnip-feedback.is-error{{color:#c4714f;}}
    .rsnip-feedback.is-success{{color:#7a9e7e;}}
  </style>
  <div style="max-width:760px;margin:0 auto;">
    <h2>Отзывы участниц</h2>
    <p style="text-align:center;color:rgba(44,26,14,.65);">На отзывы отвечает AI-ассистент.</p>
    <div class="rsnip-list" data-rsnip-list><p>Загружаем отзывы…</p></div>
    <form class="rsnip-form" data-rsnip-form novalidate>
      <h3 style="margin:0 0 8px;font-family:'Playfair Display',Georgia,serif;">Оставить отзыв</h3>
      <input type="text" name="name" placeholder="Ваше имя (необязательно)" maxlength="120" />
      <textarea name="text" placeholder="Ваш отзыв" maxlength="2000" required></textarea>
      <button type="submit">Отправить</button>
      <p class="rsnip-feedback" data-rsnip-feedback></p>
    </form>
  </div>
  <script>
    (function () {{
      var API = "{api_base}";
      var AI_AUTHOR = "{ai_author}";
      var root = document.currentScript.closest("[data-rsnip-root]");
      var listEl = root.querySelector("[data-rsnip-list]");
      var formEl = root.querySelector("[data-rsnip-form]");
      var feedback = root.querySelector("[data-rsnip-feedback]");

      function fmtDate(iso) {{
        if (!iso) return "";
        try {{ return new Date(iso).toLocaleDateString("ru-RU", {{ day: "2-digit", month: "long", year: "numeric" }}); }}
        catch (_) {{ return iso; }}
      }}
      function tLabel(t) {{
        if (t === "positive") return "тёплый";
        if (t === "negative") return "требует внимания";
        if (t === "neutral") return "нейтральный";
        return "новый";
      }}
      function render(items) {{
        listEl.innerHTML = "";
        var roots = items.filter(function(r){{ return r.parent_id == null; }});
        if (!roots.length) {{
          listEl.innerHTML = "<p>Будьте первой, кто оставит отзыв.</p>";
          return;
        }}
        roots.sort(function(a,b){{ return (b.created_at||"").localeCompare(a.created_at||""); }});
        roots.forEach(function (r) {{
          var card = document.createElement("article");
          card.className = "rsnip-card";
          var head = document.createElement("div");
          head.className = "rsnip-card__head";
          head.innerHTML =
            '<span class="rsnip-card__author"></span>' +
            '<span class="rsnip-card__date"></span>' +
            '<span class="rsnip-card__tone rsnip-card__tone--' + (r.tone || "neutral") + '"></span>';
          head.children[0].textContent = r.name || "Аноним";
          head.children[1].textContent = fmtDate(r.created_at);
          head.children[2].textContent = tLabel(r.tone);
          card.appendChild(head);
          var body = document.createElement("p");
          body.style.whiteSpace = "pre-wrap";
          body.textContent = r.text;
          card.appendChild(body);
          if (r.response) {{
            var rep = document.createElement("div");
            rep.className = "rsnip-card__reply";
            var lbl = document.createElement("span");
            lbl.className = "rsnip-card__reply-label";
            lbl.textContent = AI_AUTHOR;
            var rt = document.createElement("p");
            rt.style.margin = "0";
            rt.style.whiteSpace = "pre-wrap";
            rt.textContent = r.response;
            rep.appendChild(lbl);
            rep.appendChild(rt);
            card.appendChild(rep);
          }}
          listEl.appendChild(card);
        }});
      }}
      function load() {{
        fetch(API + "/api/reviews")
          .then(function(r){{ if (!r.ok) throw new Error(r.status); return r.json(); }})
          .then(render)
          .catch(function(e){{ listEl.innerHTML = "<p>Не удалось загрузить отзывы (" + e.message + ").</p>"; }});
      }}
      formEl.addEventListener("submit", function (ev) {{
        ev.preventDefault();
        var fd = new FormData(formEl);
        var text = (fd.get("text") || "").toString().trim();
        if (!text) {{
          feedback.textContent = "Напишите текст отзыва.";
          feedback.className = "rsnip-feedback is-error";
          return;
        }}
        var payload = {{ text: text }};
        var name = (fd.get("name") || "").toString().trim();
        if (name) payload.name = name;
        feedback.textContent = "Отправляем…";
        feedback.className = "rsnip-feedback";
        fetch(API + "/api/reviews", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(payload),
        }})
          .then(function(r){{ return r.json().then(function(b){{ if (!r.ok) throw new Error(b.detail||r.status); return b; }}); }})
          .then(function(){{
            formEl.reset();
            feedback.textContent = "Спасибо! Отзыв отправлен.";
            feedback.className = "rsnip-feedback is-success";
            load();
          }})
          .catch(function(e){{
            feedback.textContent = e.message || "Ошибка отправки";
            feedback.className = "rsnip-feedback is-error";
          }});
      }});
      load();
    }})();
  </script>
</section>
"""


def cmd_snippet(args: argparse.Namespace) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / "reviews_snippet.html"
    target.write_text(
        SNIPPET_TEMPLATE.format(api_base=args.api_base.rstrip("/"), ai_author=args.ai_author),
        encoding="utf-8",
    )
    log.info("Снипет: %s", target)
    log.info("Вставьте содержимое файла в любую HTML-страницу (Tilda → блок 'HTML').")


def cmd_reset_review(args: argparse.Namespace) -> None:
    """Сбрасывает отзыв в `status=new`, обнуляя tone/response.

    Удобно, когда воркер уже отметил отзыв как processed (например, во время
    smoke-теста), а вы хотите, чтобы он обработал его заново.
    """
    site_python = _venv_python(SITE_VENV) if SITE_VENV.exists() else Path(sys.executable)
    ids = ",".join(str(i) for i in args.ids)
    code = textwrap.dedent(
        f"""
        from reviews import _SessionLocal, Review
        ids = [{ids}]
        with _SessionLocal() as s:
            n = 0
            for review_id in ids:
                r = s.get(Review, review_id)
                if r is None:
                    print(f'id={{review_id}}: not found')
                    continue
                r.status = 'new'
                r.tone = None
                r.response = None
                n += 1
                print(f'id={{review_id}}: reset to new')
            s.commit()
            print(f'updated {{n}} review(s)')
        """
    )
    subprocess.run([str(site_python), "-c", code], cwd=SERVER_DIR, check=True)


def cmd_deploy(args: argparse.Namespace) -> None:
    if not shutil.which("rsync"):
        log.error("rsync не найден. Установите его (brew install rsync).")
        sys.exit(1)
    if not shutil.which("ssh"):
        log.error("ssh не найден.")
        sys.exit(1)

    target = f"{args.user}@{args.host}:{args.path.rstrip('/')}/"
    log.info("Заливаю site/ → %s", target)
    subprocess.run(
        [
            "rsync", "-az", "--delete",
            "--exclude", ".git",
            "--exclude", ".venv",
            "--exclude", "__pycache__",
            "--exclude", "*.pyc",
            "--exclude", "data/",
            "--exclude", ".env",
            "--exclude", ".DS_Store",
            f"{SITE_DIR}/", target,
        ],
        check=True,
    )
    if args.restart:
        log.info("Перезапускаю сервис на сервере: %s", args.restart)
        subprocess.run(["ssh", f"{args.user}@{args.host}", args.restart], check=True)
    log.info("Деплой завершён.")


# ----------------------------- argparse -----------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="venv + зависимости + БД + демо-данные").set_defaults(func=cmd_init)

    p_serve = sub.add_parser("serve", help="запустить FastAPI-бэкенд")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_static = sub.add_parser("static", help="простой http.server для лендинга")
    p_static.add_argument("--host", default="127.0.0.1")
    p_static.add_argument("--port", type=int, default=8080)
    p_static.set_defaults(func=cmd_static)

    p_smoke = sub.add_parser("smoke-test", help="проверить совместимость API с worker_ai")
    p_smoke.add_argument("--api-base", default=DEFAULT_API_BASE)
    p_smoke.add_argument("--worker-token", default=os.getenv("WORKER_API_TOKEN", "change-me"))
    p_smoke.set_defaults(func=cmd_smoke_test)

    p_worker = sub.add_parser("worker", help="запустить worker_ai/worker.py")
    p_worker.add_argument("--api-base", default=DEFAULT_API_BASE)
    p_worker.set_defaults(func=cmd_worker)

    p_snip = sub.add_parser("snippet", help="сгенерировать автономный HTML-блок")
    p_snip.add_argument("--api-base", default="https://retreat.tatidzufri.com")
    p_snip.add_argument("--ai-author", default="AI-ассистент ретрита")
    p_snip.set_defaults(func=cmd_snippet)

    p_reset = sub.add_parser("reset-review", help="сбросить отзыв(ы) в status=new")
    p_reset.add_argument("ids", nargs="+", type=int, help="id отзывов через пробел")
    p_reset.set_defaults(func=cmd_reset_review)

    p_dep = sub.add_parser("deploy", help="rsync site/ на сервер")
    p_dep.add_argument("--host", required=True)
    p_dep.add_argument("--user", required=True)
    p_dep.add_argument("--path", required=True, help="абсолютный путь на сервере")
    p_dep.add_argument("--restart", default="", help="команда для запуска по ssh после rsync (опционально)")
    p_dep.set_defaults(func=cmd_deploy)

    ns = parser.parse_args(argv)
    ns.func(ns)


if __name__ == "__main__":
    main()
