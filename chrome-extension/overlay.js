// Карточка хода входа в углу страницы Selectel. Живёт в закрытом Shadow DOM:
// стили Selectel её не задевают, а её стили — страницу.
"use strict";

window.ToffeeOverlay = (() => {
    const STEPS = ["Данные", "Код из почты", "В панель", "Готово"];
    const STEP_OF = {
        filling: 0, login: 0, filled: 1, otp_sent: 1, otp_wait: 1, code: 2, verifying: 2, connecting: 2, done: 4,
    };
    const COLLAPSE_KEY = "toffee-roller.overlay-collapsed";

    // Шрифт панели — через FontFace: он становится доступен и внутри теневого DOM.
    function loadFonts() {
        const faces = [
            ["Toffee Geist", "fonts/geist-latin.woff2", "U+0000-00FF, U+0131, U+0152-0153, U+2000-206F, U+20AC, U+2122, U+2212"],
            ["Toffee Geist", "fonts/geist-cyrillic.woff2", "U+0301, U+0400-045F, U+0490-0491, U+04B0-04B1, U+2116"],
            ["Toffee Geist Mono", "fonts/geist-mono-latin.woff2", "U+0000-00FF, U+2000-206F"],
            ["Toffee Geist Mono", "fonts/geist-mono-cyrillic.woff2", "U+0400-045F, U+2116"],
        ];
        for (const [family, file, unicodeRange] of faces) {
            try {
                const face = new FontFace(family, `url(${chrome.runtime.getURL(file)})`, {weight: "100 900", unicodeRange});
                document.fonts.add(face);
                face.load().catch(() => {});
            } catch {
                // Нет FontFace — останется системный шрифт.
            }
        }
    }

    const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; }
    .wrap {
        --bg: oklch(0.1809 0.0054 39.3); --raised: oklch(0.2045 0.0062 40); --fg: oklch(0.9606 0.0086 67.7);
        --muted: oklch(0.7108 0.0127 59.5); --subtle: oklch(0.5405 0.0127 54.3);
        --border: oklch(0.2663 0.0098 39.3); --border-strong: oklch(0.3243 0.0135 48.3);
        --ok: oklch(0.78 0.15 155); --warn: oklch(0.82 0.14 85); --danger: oklch(0.68 0.19 27);
        --amber: oklch(0.7664 0.1682 59.7);
        --brand: linear-gradient(135deg, oklch(0.8561 0.126 76) 0%, var(--amber) 48%, oklch(0.6457 0.174 45.1) 100%);
        --ease: cubic-bezier(.2,.8,.2,1); --ease-enter: cubic-bezier(.22,1,.36,1);
        position: fixed; right: 16px; bottom: 16px; z-index: 2147483647;
        font: 14px/1.45 "Toffee Geist", ui-sans-serif, system-ui, "Segoe UI", sans-serif;
        color: var(--fg); -webkit-font-smoothing: antialiased; color-scheme: dark;
    }
    .card {
        width: min(352px, calc(100vw - 32px)); padding: 14px 14px 16px;
        border: 1px solid var(--border-strong); border-radius: 16px;
        background: color-mix(in oklch, var(--bg) 94%, transparent);
        backdrop-filter: blur(14px) saturate(1.2);
        box-shadow: 0 24px 48px -16px rgb(0 0 0 / .55), 0 2px 8px rgb(0 0 0 / .25);
        transition: border-color 200ms var(--ease);
    }
    /* Появляется один раз; смена шага — только текст и полоски. */
    .card[data-enter], .pill[data-enter] { animation: enter 320ms var(--ease-enter) both; }
    .card[data-kind="active"] { border-color: color-mix(in oklch, var(--amber) 38%, var(--border-strong)); }
    .card[data-kind="done"] { border-color: color-mix(in oklch, var(--ok) 38%, var(--border-strong)); }
    .card[data-kind="error"] { border-color: color-mix(in oklch, var(--danger) 40%, var(--border-strong)); }
    @keyframes enter { from { opacity: 0; transform: translateY(10px) scale(.98); } }

    .head { display: flex; align-items: center; gap: 9px; }
    .logo { width: 22px; height: 22px; flex-shrink: 0; }
    .name { font-weight: 600; font-size: 13.5px; letter-spacing: -0.01em; }
    .acc { margin-left: auto; font: 500 12px/1 "Toffee Geist Mono", ui-monospace, Consolas, monospace; color: var(--muted);
        padding: 5px 7px; border: 1px solid var(--border); border-radius: 7px; white-space: nowrap; }
    .icon-btn { all: unset; display: grid; place-items: center; width: 28px; height: 28px; border-radius: 8px;
        color: var(--subtle); cursor: pointer; transition: background-color 150ms var(--ease), color 150ms var(--ease); }
    .icon-btn:hover { background: var(--raised); color: var(--fg); }
    .icon-btn:focus-visible { outline: 2px solid var(--amber); outline-offset: 1px; }
    .icon-btn svg { width: 16px; height: 16px; }

    .body { display: flex; gap: 12px; margin-top: 14px; }
    .disc { display: grid; place-items: center; width: 34px; height: 34px; flex-shrink: 0; border-radius: 50%;
        border: 1px solid var(--border-strong); background: var(--raised); color: var(--muted); }
    .disc svg { width: 17px; height: 17px; }
    [data-kind="active"] .disc { border-color: color-mix(in oklch, var(--amber) 40%, transparent);
        background: color-mix(in oklch, var(--amber) 11%, transparent); color: var(--amber); }
    [data-kind="done"] .disc, [data-kind="ok"] .disc { border-color: color-mix(in oklch, var(--ok) 38%, transparent);
        background: color-mix(in oklch, var(--ok) 11%, transparent); color: var(--ok); }
    [data-kind="error"] .disc { border-color: color-mix(in oklch, var(--danger) 40%, transparent);
        background: color-mix(in oklch, var(--danger) 11%, transparent); color: var(--danger); }
    [data-kind="done"] .disc { animation: pop 420ms var(--ease-enter) both; }
    @keyframes pop { from { transform: scale(.5); opacity: 0; } }
    .spin { animation: spin .8s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }

    .text { min-width: 0; flex: 1; padding-top: 1px; }
    .title { font-weight: 600; font-size: 14.5px; letter-spacing: -0.01em; animation: fade 220ms var(--ease) both; }
    .detail { margin-top: 2px; color: var(--muted); font-size: 13.5px; animation: fade 220ms var(--ease) both; }
    @keyframes fade { from { opacity: 0; transform: translateY(2px); } }

    .steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; margin: 14px 0 0; padding: 0; list-style: none; }
    .steps li { min-width: 0; }
    .bar { position: relative; display: block; height: 4px; overflow: hidden; border-radius: 4px; background: var(--border-strong); }
    .bar i { position: absolute; inset: 0; border-radius: inherit; background: var(--amber);
        transform-origin: left; transform: scaleX(0); transition: transform 360ms var(--ease); }
    li.past .bar i { transform: scaleX(1); }
    li.now .bar i { transform: scaleX(.45); animation: flow 1.4s ease-in-out infinite; }
    @keyframes flow { 0% { transform: translateX(-100%) scaleX(.45); } 100% { transform: translateX(230%) scaleX(.45); } }
    [data-kind="done"] .bar i { background: var(--ok); }
    [data-kind="error"] li.now .bar i { animation: none; transform: scaleX(1); background: var(--danger); }
    .steps span { display: block; margin-top: 6px; overflow: hidden; color: var(--subtle); font-size: 11.5px;
        white-space: nowrap; text-overflow: ellipsis; }
    li.past span, li.now span { color: color-mix(in oklch, var(--fg) 85%, transparent); }

    .timer { display: flex; align-items: center; justify-content: space-between; margin-top: 12px;
        color: var(--muted); font-size: 12.5px; font-variant-numeric: tabular-nums; }
    .timer .track { flex: 1; height: 3px; margin-right: 10px; border-radius: 3px; background: var(--border); overflow: hidden; }
    .timer .track i { display: block; height: 100%; background: color-mix(in oklch, var(--amber) 70%, transparent);
        transform-origin: left; transition: transform 1s linear; }

    .code { display: flex; align-items: center; gap: 8px; margin-top: 12px; padding: 8px 8px 8px 12px;
        border: 1px solid var(--border-strong); border-radius: 12px; background: var(--raised); }
    .code b { flex: 1; font: 600 22px/1 "Toffee Geist Mono", ui-monospace, Consolas, monospace; letter-spacing: .18em; }
    .copy { all: unset; display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 10px;
        border: 1px solid var(--border-strong); border-radius: 9px; color: var(--muted); font-size: 12.5px; font-weight: 500;
        cursor: pointer; transition: transform 120ms var(--ease), background-color 150ms var(--ease), color 150ms var(--ease); }
    .copy:hover { background: var(--bg); color: var(--fg); transform: translateY(-1px); }
    .copy:active { transform: translateY(1px) scale(.985); }
    .copy[data-done] { color: var(--ok); border-color: color-mix(in oklch, var(--ok) 40%, transparent); }
    .copy svg { width: 14px; height: 14px; }

    .actions { display: flex; gap: 8px; margin-top: 14px; }
    .btn { all: unset; display: inline-flex; flex: 1; align-items: center; justify-content: center; height: 36px;
        border-radius: 10px; font-size: 13.5px; font-weight: 600; cursor: pointer; text-align: center;
        transition: transform 120ms var(--ease), filter 150ms var(--ease), background-color 150ms var(--ease); }
    .btn:hover { transform: translateY(-1px); }
    .btn:active { transform: translateY(1px) scale(.985); }
    .btn:focus-visible, .copy:focus-visible { outline: 2px solid var(--amber); outline-offset: 2px; }
    .btn.brand { background: var(--brand); color: #140d08; }
    .btn.brand:hover { filter: brightness(1.08); }
    .btn.quiet { border: 1px solid var(--border-strong); color: var(--fg); font-weight: 500; }
    .btn.quiet:hover { background: var(--raised); }

    /* Свёрнутая карточка — компактная пилюля. */
    .pill { all: unset; display: inline-flex; align-items: center; gap: 9px; height: 40px; padding: 0 14px 0 8px;
        border: 1px solid var(--border-strong); border-radius: 999px; background: color-mix(in oklch, var(--bg) 94%, transparent);
        backdrop-filter: blur(14px); box-shadow: 0 14px 30px -12px rgb(0 0 0 / .6); cursor: pointer;
        font-size: 13px; font-weight: 500;
        transition: transform 150ms var(--ease), border-color 150ms var(--ease); }
    .pill:hover { transform: translateY(-1px); border-color: var(--subtle); }
    .pill:focus-visible { outline: 2px solid var(--amber); outline-offset: 2px; }
    .pill .logo { width: 24px; height: 24px; }
    .pill .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--amber); }
    .pill[data-kind="done"] .dot, .pill[data-kind="ok"] .dot { background: var(--ok); }
    .pill[data-kind="error"] .dot { background: var(--danger); }
    .pill[data-kind="active"] .dot { animation: blink 1.4s ease-in-out infinite; }
    @keyframes blink { 50% { opacity: .35; } }

    .dbg { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px; }
    .dbg summary { cursor: pointer; color: var(--muted); font-size: 12px; list-style: none; }
    .dbg summary::-webkit-details-marker { display: none; }
    .dbg summary::before { content: "▸ "; }
    .dbg[open] summary::before { content: "▾ "; }
    .dbg pre { max-height: 180px; overflow: auto; margin: 8px 0; padding: 8px; border-radius: 8px;
        background: var(--bg); color: var(--muted); font: 11px/1.5 "Toffee Geist Mono", ui-monospace, Consolas, monospace;
        white-space: pre-wrap; word-break: break-word; }
    .dbg .copy { height: 28px; }

    @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after { animation-duration: 1ms !important; animation-iteration-count: 1 !important;
            transition-duration: 1ms !important; }
        li.now .bar i { transform: scaleX(1); }
    }`;

    const svg = (d, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${d}</svg>`;
    const ICON = {
        spinner: svg('<path d="M12 3a9 9 0 1 0 9 9"/>', 'class="spin" stroke-width="2.4"'),
        check: svg('<path d="M5 12.5 10 17.5 19 7"/>', 'stroke-width="2.6"'),
        warning: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5"/><path d="M12 16.5h.01"/>', 'stroke-width="2.2"'),
        mail: svg('<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m4 7 8 6 8-6"/>'),
        minus: svg('<path d="M6 12h12"/>', 'stroke-width="2.2"'),
        copy: svg('<rect x="9" y="9" width="11" height="11" rx="2.5"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>'),
    };
    const LOGO = `<svg class="logo" viewBox="0 0 32 32" aria-hidden="true">
        <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#FFC46B"/><stop offset="0.48" stop-color="#FF9528"/><stop offset="1" stop-color="#E0631A"/>
        </linearGradient></defs>
        <rect x="1" y="1" width="30" height="30" rx="9" fill="url(#g)"/>
        <path d="M16 9.5a6.5 6.5 0 1 1-6.5 6.5 4.6 4.6 0 0 1 4.6-4.6 3.3 3.3 0 0 1 3.3 3.3 2 2 0 0 1-2 2"
              fill="none" stroke="#1A0F08" stroke-width="2.4" stroke-linecap="round"/></svg>`;

    function el(tag, attrs = {}, html = "") {
        const node = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
        if (html) node.innerHTML = html;
        return node;
    }

    function create(accountId) {
        loadFonts();
        const host = el("toffee-roller-overlay");
        const root = host.attachShadow({mode: "closed"});
        const style = el("style");
        style.textContent = CSS;
        const wrap = el("div", {class: "wrap"});
        root.append(style, wrap);

        let state = {stage: "", title: "Готовим вход", detail: "", kind: "active", step: 0, code: "", deadline: 0, actions: []};
        let collapsed = sessionStorage.getItem(COLLAPSE_KEY) === "1";
        let timer = null;
        let shown = ""; // какой вид уже на экране: card | pill — анимируем только его появление
        let debugLines = []; // подробный журнал шагов — показываем свёрнутым блоком в карточке

        function mount() {
            if (!host.isConnected) document.documentElement.appendChild(host);
        }

        function setCollapsed(value) {
            collapsed = value;
            try {
                sessionStorage.setItem(COLLAPSE_KEY, value ? "1" : "0");
            } catch {}
            render();
        }

        function discIcon() {
            if (state.kind === "done" || state.kind === "ok") return ICON.check;
            if (state.kind === "error") return ICON.warning;
            if (state.stage === "otp_wait") return ICON.mail;
            return ICON.spinner;
        }

        function renderTimer(box) {
            const total = 115_000;
            const tick = () => {
                const left = Math.max(0, state.deadline - Date.now());
                const s = Math.ceil(left / 1000);
                box.querySelector(".track i").style.transform = `scaleX(${Math.min(1, left / total)})`;
                box.querySelector(".time").textContent = `ждём ещё ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
                if (!left) clearInterval(timer);
            };
            tick();
            timer = setInterval(tick, 1000);
        }

        function render() {
            clearInterval(timer);
            wrap.replaceChildren();
            if (collapsed) {
                const pill = el("button", {class: "pill", type: "button", "data-kind": state.kind,
                    "aria-label": "Развернуть Toffee Roller: " + state.title});
                if (shown !== "pill") pill.setAttribute("data-enter", "");
                shown = "pill";
                pill.innerHTML = LOGO + '<span class="dot"></span>';
                pill.append(document.createTextNode(state.title));
                pill.addEventListener("click", () => setCollapsed(false));
                wrap.appendChild(pill);
                return;
            }

            const card = el("section", {class: "card", "data-kind": state.kind, role: "status", "aria-live": "polite",
                "aria-label": "Toffee Roller"});
            if (shown !== "card") card.setAttribute("data-enter", "");
            shown = "card";
            const head = el("div", {class: "head"}, LOGO + '<span class="name">Toffee Roller</span>');
            head.appendChild(el("span", {class: "acc", title: "Номер аккаунта"}, "№ " + accountId));
            const min = el("button", {class: "icon-btn", type: "button", "aria-label": "Свернуть"}, ICON.minus);
            min.addEventListener("click", () => setCollapsed(true));
            head.appendChild(min);

            const body = el("div", {class: "body"});
            body.appendChild(el("span", {class: "disc"}, discIcon()));
            const text = el("div", {class: "text"});
            const title = el("div", {class: "title"});
            title.textContent = state.title;
            text.appendChild(title);
            if (state.detail) {
                const detail = el("div", {class: "detail"});
                detail.textContent = state.detail;
                text.appendChild(detail);
            }
            body.appendChild(text);

            const steps = el("ol", {class: "steps", "aria-label": "Шаги входа"});
            STEPS.forEach((label, i) => {
                const li = el("li", {class: i < state.step ? "past" : i === state.step ? "now" : ""},
                    '<span class="bar"><i></i></span>');
                const caption = el("span");
                caption.textContent = label;
                li.appendChild(caption);
                steps.appendChild(li);
            });

            card.append(head, body, steps);

            if (state.stage === "otp_wait" && state.deadline > Date.now()) {
                const box = el("div", {class: "timer"}, '<span class="track"><i></i></span><span class="time"></span>');
                card.appendChild(box);
                renderTimer(box);
            }

            if (state.code) {
                const code = el("div", {class: "code"});
                const b = el("b");
                b.textContent = state.code;
                const copy = el("button", {class: "copy", type: "button"}, ICON.copy + "<span>Копировать</span>");
                copy.addEventListener("click", async () => {
                    try {
                        await navigator.clipboard.writeText(state.code);
                        copy.dataset.done = "";
                        copy.innerHTML = ICON.check + "<span>Скопирован</span>";
                        setTimeout(() => {
                            delete copy.dataset.done;
                            copy.innerHTML = ICON.copy + "<span>Копировать</span>";
                        }, 1600);
                    } catch {}
                });
                code.append(b, copy);
                card.appendChild(code);
            }

            if (state.actions.length) {
                const actions = el("div", {class: "actions"});
                for (const action of state.actions) {
                    const btn = el("button", {class: "btn " + (action.primary ? "brand" : "quiet"), type: "button"});
                    btn.textContent = action.label;
                    btn.addEventListener("click", action.run);
                    actions.appendChild(btn);
                }
                card.appendChild(actions);
            }

            // Диагностический журнал: свёрнут, с кнопкой «копировать» — чтобы прислать лог.
            if (debugLines.length) {
                const det = el("details", {class: "dbg"});
                const sum = el("summary");
                sum.textContent = `Журнал · ${debugLines.length}`;
                const pre = el("pre");
                pre.textContent = debugLines.join("\n");
                const copy = el("button", {class: "copy", type: "button"}, ICON.copy + "<span>Копировать журнал</span>");
                copy.addEventListener("click", async () => {
                    try {
                        await navigator.clipboard.writeText(debugLines.join("\n"));
                        copy.innerHTML = ICON.check + "<span>Скопировано</span>";
                        setTimeout(() => { copy.innerHTML = ICON.copy + "<span>Копировать журнал</span>"; }, 1600);
                    } catch {}
                });
                det.append(sum, pre, copy);
                card.appendChild(det);
            }
            wrap.appendChild(card);
        }

        /**
         * stage — стадия для панели; kind: active | ok | done | error.
         * code — показать код крупно; deadline — таймер ожидания письма; actions — кнопки.
         */
        function set(next) {
            const stage = next.stage ?? state.stage;
            const step = stage in STEP_OF ? STEP_OF[stage] : state.step;
            state = {
                ...state, ...next, stage, step,
                code: next.code ?? (stage === "code" ? state.code : ""),
                actions: next.actions ?? [],
            };
            // Ошибка и успех важнее свёрнутости — разворачиваем карточку.
            if (state.kind === "error" || state.kind === "done") collapsed = false;
            render();
        }

        function debug(lines) {
            debugLines = Array.isArray(lines) ? lines.slice(-60) : [];
            render();
        }

        mount();
        new MutationObserver(mount).observe(document.documentElement, {childList: true});
        render();
        return {set, debug};
    }

    return {create};
})();
