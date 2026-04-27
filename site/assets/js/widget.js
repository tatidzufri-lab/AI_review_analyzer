/**
 * Плавающий виджет: OpenAI ChatKit + workflow из Agent Builder (id вида wf_…).
 *
 * Секретный API-ключ не хранится в браузере: бэкенд выдаёт client_secret
 * через POST /v1/chatkit/sessions (см. developers.openai.com → ChatKit).
 *
 * Локально: поднимите server/main.py, задайте CHATKIT_WORKFLOW_ID в server/.env и sessionEndpoint ниже.
 */

const WIDGET_CONFIG = {
  /**
   * ID workflow из Agent Builder (wf_…). Если null или "" — сервер берёт CHATKIT_WORKFLOW_ID из server/.env.
   * Ненулевое значение удобно для временного переопределения без правки .env.
   */
  workflowId: null,
  /**
   * URL эндпоинта, который возвращает { client_secret } (см. retreat-case/server).
   * Должен совпадать с origin статики в CORS_ORIGINS на сервере.
   */
  sessionEndpoint: "http://127.0.0.1:8000/api/create-session",
  /** Имя ассистента в терракотовой полосе над чатом */
  assistantName: "Консультант ретрита",
  /** Локаль UI iframe ChatKit (приветствие, плейсхолдер, кнопки). */
  locale: "ru",
};

(function () {
  "use strict";

  var CHATKIT_SRC =
    "https://cdn.platform.openai.com/deployments/chatkit/chatkit.js";

  function injectStyles() {
    var style = document.createElement("style");
    style.textContent =
      "" +
      ".retreat-widget-root{font-family:Nunito,system-ui,sans-serif;}" +
      ".retreat-widget-toggle{position:fixed;right:20px;bottom:20px;z-index:9998;width:56px;height:56px;border-radius:999px;border:none;cursor:pointer;background-color:#c4714f;color:#fff;font-size:1.35rem;line-height:1;box-shadow:0 8px 28px rgba(196,113,79,0.35);display:flex;align-items:center;justify-content:center;transition:background-color 0.2s ease,transform 0.2s ease,box-shadow 0.2s ease;}" +
      ".retreat-widget-toggle:hover{background-color:#a85d3f;transform:translateY(-2px);box-shadow:0 10px 32px rgba(196,113,79,0.4);}" +
      ".retreat-widget-toggle:focus-visible{outline:2px solid #c4714f;outline-offset:3px;}" +
      ".retreat-widget-panel{position:fixed;right:20px;bottom:88px;z-index:9999;width:360px;height:520px;max-height:calc(100vh - 120px);background:#fff;border-radius:14px;box-shadow:0 12px 40px rgba(44,26,14,0.08);display:flex;flex-direction:column;overflow:hidden;border:1px solid rgba(44,26,14,0.05);opacity:0;transform:translateY(16px);pointer-events:none;transition:opacity 0.35s ease,transform 0.35s ease,box-shadow 0.35s ease;}" +
      ".retreat-widget-panel.is-open{opacity:1;transform:translateY(0);pointer-events:auto;box-shadow:0 18px 48px rgba(44,26,14,0.12);}" +
      ".retreat-widget-header{display:flex;align-items:center;justify-content:space-between;padding:0.65rem 1rem;flex-shrink:0;background-color:#c4714f;border-bottom:1px solid rgba(44,26,14,0.12);}" +
      ".retreat-widget-title{font-family:'Playfair Display',Georgia,serif;font-weight:600;font-size:1.05rem;line-height:1.25;margin:0;color:#fff;letter-spacing:0.02em;text-align:left;text-shadow:0 1px 0 rgba(44,26,14,0.15);}" +
      ".retreat-widget-close{background:transparent;border:none;color:rgba(255,255,255,0.92);font-size:1.2rem;line-height:1;cursor:pointer;padding:0.35rem 0.45rem;border-radius:8px;transition:color 0.2s ease,background-color 0.2s ease;}" +
      ".retreat-widget-close:hover{color:#fff;background-color:rgba(44,26,14,0.15);}" +
      ".retreat-widget-chat-host{flex:1 1 0%;min-height:0;position:relative;background:#fff;}" +
      ".retreat-widget-chat-host openai-chatkit{position:absolute;inset:0;border:0;}" +
      ".retreat-widget-status{padding:0.5rem 0.75rem;font-size:0.82rem;font-weight:600;color:#2c1a0e;background:rgba(122,158,126,0.15);border-bottom:1px solid rgba(44,26,14,0.06);display:none;}" +
      ".retreat-widget-status.is-visible{display:block;}" +
      "@media(max-width:400px){.retreat-widget-panel{right:12px;left:12px;width:auto;bottom:84px;}}";
    document.head.appendChild(style);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function loadChatKitScript() {
    return new Promise(function (resolve, reject) {
      if (customElements.get("openai-chatkit")) {
        resolve();
        return;
      }
      var s = document.querySelector('script[data-retreat-chatkit="1"]');
      if (s) {
        s.addEventListener("load", function () {
          resolve();
        });
        s.addEventListener("error", reject);
        return;
      }
      var script = document.createElement("script");
      script.src = CHATKIT_SRC;
      script.async = true;
      script.dataset.retreatChatkit = "1";
      script.onload = function () {
        resolve();
      };
      script.onerror = function () {
        reject(new Error("Не удалось загрузить ChatKit"));
      };
      document.head.appendChild(script);
    });
  }

  function resolvedWorkflowId() {
    var w = WIDGET_CONFIG.workflowId;
    if (w == null) return "";
    return String(w).trim();
  }

  function validateConfig() {
    var wid = resolvedWorkflowId();
    if (wid && !/^wf_/.test(wid)) {
      return "Неверный workflowId: ожидается id вида wf_… или оставьте null — тогда используется CHATKIT_WORKFLOW_ID в server/.env.";
    }
    if (
      !WIDGET_CONFIG.sessionEndpoint ||
      WIDGET_CONFIG.sessionEndpoint === "CHATKIT_SESSION_ENDPOINT_HERE"
    ) {
      return "Укажите sessionEndpoint (URL бэкенда /api/create-session).";
    }
    return null;
  }

  function createSessionRequestBody() {
    var wid = resolvedWorkflowId();
    if (!wid) return {};
    return { workflow: { id: wid } };
  }

  function buildWidget() {
    injectStyles();

    var root = el("div", "retreat-widget-root");
    var toggle = el("button", "retreat-widget-toggle", "💬");
    toggle.type = "button";
    toggle.setAttribute("aria-label", "Открыть чат");

    var panel = el("div", "retreat-widget-panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "false");
    panel.setAttribute("aria-label", WIDGET_CONFIG.assistantName);

    var header = el("div", "retreat-widget-header");
    var title = el("h3", "retreat-widget-title", WIDGET_CONFIG.assistantName);
    var closeBtn = el("button", "retreat-widget-close", "✕");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Закрыть чат");
    header.appendChild(title);
    header.appendChild(closeBtn);

    var status = el("div", "retreat-widget-status");
    var chatHost = el("div", "retreat-widget-chat-host");

    panel.appendChild(header);
    panel.appendChild(status);
    panel.appendChild(chatHost);

    root.appendChild(toggle);
    root.appendChild(panel);
    document.body.appendChild(root);

    var isOpen = false;
    var chatkitMounted = false;

    function setStatus(msg) {
      if (!msg) {
        status.textContent = "";
        status.classList.remove("is-visible");
        return;
      }
      status.textContent = msg;
      status.classList.add("is-visible");
    }

    function setOpen(open) {
      isOpen = open;
      panel.classList.toggle("is-open", open);
      toggle.setAttribute(
        "aria-label",
        open ? "Закрыть чат" : "Открыть чат"
      );
      if (open) mountChatKitIfNeeded();
    }

    function mountChatKitIfNeeded() {
      var cfgErr = validateConfig();
      if (cfgErr) {
        setStatus(cfgErr);
        return;
      }
      if (chatkitMounted) return;
      chatkitMounted = true;

      loadChatKitScript()
        .then(function () {
          return customElements.whenDefined("openai-chatkit");
        })
        .then(function () {
          var chatkit = document.createElement("openai-chatkit");
          chatHost.appendChild(chatkit);

          chatkit.addEventListener("chatkit.error", function (ev) {
            var err = ev.detail && ev.detail.error;
            setStatus(
              err && err.message
                ? err.message
                : "Ошибка ChatKit. Проверьте бэкенд и workflow."
            );
          });

          var _debugThreadId = null;
          chatkit.addEventListener("chatkit.thread.change", function (ev) {
            _debugThreadId = ev.detail && ev.detail.threadId;
            console.log("[ChatKit] thread.change →", _debugThreadId);
          });
          chatkit.addEventListener("chatkit.response.end", function () {
            console.log("[ChatKit] response.end, threadId =", _debugThreadId);
            if (!_debugThreadId) return;
            fetch(
              WIDGET_CONFIG.sessionEndpoint.replace(
                "/api/create-session",
                "/api/thread-items/" + encodeURIComponent(_debugThreadId)
              )
            )
              .then(function (r) { return r.json(); })
              .then(function (data) {
                console.log("[ChatKit] thread items:", JSON.stringify(data, null, 2));
              })
              .catch(function (e) { console.warn("[ChatKit] thread-items error", e); });
          });

          var kitOptions = {
            theme: {
              colorScheme: "light",
              radius: "soft",
              typography: {
                fontFamily: '"Nunito", system-ui, sans-serif',
                baseSize: 16,
              },
              color: {
                accent: { primary: "#c4714f", level: 2 },
              },
            },
            header: { enabled: false },
            api: {
              getClientSecret: function (currentClientSecret) {
                if (currentClientSecret) {
                  return Promise.resolve(currentClientSecret);
                }
                return fetch(WIDGET_CONFIG.sessionEndpoint, {
                  method: "POST",
                  credentials: "include",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify(createSessionRequestBody()),
                })
                  .then(function (res) {
                    return res.json().then(function (body) {
                      if (!res.ok) {
                        throw new Error(
                          (body && body.error) ||
                            "Сессия ChatKit не создана (" +
                              res.status +
                              ")"
                        );
                      }
                      if (!body.client_secret) {
                        throw new Error("Ответ без client_secret");
                      }
                      setStatus("");
                      return body.client_secret;
                    });
                  })
                  .catch(function (err) {
                    setStatus(
                      err && err.message
                        ? err.message
                        : "Не удалось получить сессию"
                    );
                    throw err;
                  });
              },
            },
          };
          var loc = WIDGET_CONFIG.locale;
          if (loc != null && String(loc).trim() !== "") {
            kitOptions.locale = String(loc).trim();
          }
          chatkit.setOptions(kitOptions);
        })
        .catch(function (err) {
          chatkitMounted = false;
          while (chatHost.firstChild) chatHost.removeChild(chatHost.firstChild);
          setStatus(
            err && err.message ? err.message : "Не удалось инициализировать чат"
          );
        });
    }

    toggle.addEventListener("click", function () {
      setOpen(!isOpen);
    });

    closeBtn.addEventListener("click", function () {
      setOpen(false);
    });

    return root;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildWidget);
  } else {
    buildWidget();
  }
})();
