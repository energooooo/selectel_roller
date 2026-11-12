// Общее для попапа и страницы подключения: знак, значки, обмен с background.
"use strict";

const TR = (() => {
    const LOGO = `<svg viewBox="0 0 32 32" aria-hidden="true">
        <defs><linearGradient id="tr-g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#FFC46B"/><stop offset="0.48" stop-color="#FF9528"/><stop offset="1" stop-color="#E0631A"/>
        </linearGradient></defs>
        <rect x="1" y="1" width="30" height="30" rx="9" fill="url(#tr-g)"/>
        <path d="M16 9.5a6.5 6.5 0 1 1-6.5 6.5 4.6 4.6 0 0 1 4.6-4.6 3.3 3.3 0 0 1 3.3 3.3 2 2 0 0 1-2 2"
              fill="none" stroke="#1A0F08" stroke-width="2.3" stroke-linecap="round"/>
    </svg>`;

    const path = (d, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${d}</svg>`;

    const ICONS = {
        check: path('<path d="M5 12.5 10 17.5 19 7"/>', 'stroke-width="2.4"'),
        warning: path('<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5"/><path d="M12 16.5h.01"/>'),
        info: path('<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><path d="M12 7.5h.01"/>'),
        plug: path('<path d="M9 7V3"/><path d="M15 7V3"/><path d="M6 7h12v4a6 6 0 0 1-12 0z"/><path d="M12 17v4"/>'),
        external: path('<path d="M7 17 17 7"/><path d="M8 7h9v9"/>'),
        search: path('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
        refresh: path('<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/>'),
        gear: path('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1.1 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1z"/>'),
    };

    function send(type, payload = {}) {
        return new Promise((resolve) => {
            chrome.runtime.sendMessage({type, ...payload}, (reply) => {
                if (chrome.runtime.lastError) resolve({ok: false, error: chrome.runtime.lastError.message});
                else resolve(reply || {ok: false, error: "Нет ответа расширения."});
            });
        });
    }

    function host(base) {
        try {
            return new URL(base).host;
        } catch {
            return base || "";
        }
    }

    function plural(n, one, few, many) {
        const m10 = n % 10, m100 = n % 100;
        if (m10 === 1 && m100 !== 11) return one;
        if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
        return many;
    }

    /** Подставляет знак и значки в [data-logo] / [data-icon="…"]. */
    function paint(root = document) {
        root.querySelectorAll("[data-logo]").forEach((el) => {
            el.innerHTML = LOGO;
        });
        root.querySelectorAll("[data-icon]").forEach((el) => {
            el.innerHTML = ICONS[el.dataset.icon] || "";
        });
    }

    return {LOGO, ICONS, send, host, plural, paint};
})();
