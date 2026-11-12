// Service worker: единственная точка, которая ходит в backend Toffee Roller.
// Content script шлёт сюда сообщения — у воркера есть host_permissions на backend,
// поэтому запросы не подпадают под CORS. Bearer-токен наружу (на страницы) не отдаём.

const STORE_KEY = "toffee-roller";

async function getConfig() {
    const data = await chrome.storage.local.get(STORE_KEY);
    return data[STORE_KEY] || null;
}

async function setConfig(config) {
    await chrome.storage.local.set({[STORE_KEY]: config});
}

const LAST_KEY = "toffee-roller.last-base";
const CODE_LENGTH = 10;

/** «panel.example.ru», «localhost:3200/keys» → «https://panel.example.ru», «http://localhost:3200». */
function normalizeBase(address) {
    let raw = String(address || "").trim();
    if (!raw) throw new Error("Укажите адрес панели.");
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
        raw = (/^(localhost|127\.0\.0\.1)(:\d+)?(\/|$)/i.test(raw) ? "http://" : "https://") + raw;
    }
    let url;
    try {
        url = new URL(raw);
    } catch {
        throw new Error("Адрес панели не похож на ссылку.");
    }
    const local = /^(localhost|127\.0\.0\.1)$/.test(url.hostname);
    if (url.protocol !== "https:" && !(url.protocol === "http:" && local)) {
        throw new Error("Панель должна работать по HTTPS (или на localhost).");
    }
    return url.origin;
}

/** «k7m2q-x9p4r» → «K7M2QX9P4R». */
function normalizeCode(code) {
    const clean = String(code || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
    if (clean.length !== CODE_LENGTH) throw new Error("Код — это 10 букв и цифр из панели.");
    return clean;
}

/** Старый формат сопряжения (base64 JSON {u, c}) — на случай, если вставят его. */
function decodeLegacy(pairing) {
    const json = atob(pairing.trim().replace(/-/g, "+").replace(/_/g, "/"));
    const parsed = JSON.parse(json);
    return {base: normalizeBase(parsed.u), code: String(parsed.c || "").trim()};
}

async function api(config, path, {method = "GET", body} = {}) {
    const res = await fetch(config.base + "/api/extension" + path, {
        method,
        headers: {
            "Authorization": "Bearer " + config.token,
            ...(body !== undefined ? {"Content-Type": "application/json"} : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let data;
    try {
        data = text ? JSON.parse(text) : undefined;
    } catch {
        data = undefined;
    }
    if (!res.ok) {
        const detail = data && typeof data === "object" && typeof data.detail === "string"
            ? data.detail : "Ошибка " + res.status;
        const error = new Error(detail);
        error.status = res.status;
        throw error;
    }
    return data;
}

async function ensureAccess(base) {
    const origins = [base + "/*"];
    if (await chrome.permissions.contains({origins}).catch(() => false)) return;
    const granted = await chrome.permissions.request({origins}).catch(() => false);
    if (!granted) throw new Error("Нет доступа к адресу панели. Разрешите его в окне браузера.");
}

async function claim({address, code, pairing}) {
    const target = pairing ? decodeLegacy(pairing) : {base: normalizeBase(address), code: normalizeCode(code)};
    await ensureAccess(target.base);
    let res;
    try {
        res = await fetch(target.base + "/api/extension/claim", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({code: target.code, label: "Chrome · Toffee Roller"}),
        });
    } catch {
        throw new Error("Панель не отвечает по этому адресу. Проверьте адрес и что панель запущена.");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.token) {
        throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось подключить расширение.");
    }
    await setConfig({base: target.base, token: data.token});
    await chrome.storage.local.set({[LAST_KEY]: target.base});
    return {base: target.base};
}

async function withConfig(fn) {
    const config = await getConfig();
    if (!config || !config.token) {
        const error = new Error("Расширение не подключено к панели.");
        error.code = "not-connected";
        throw error;
    }
    return fn(config);
}

const handlers = {
    async status() {
        const config = await getConfig();
        const last = (await chrome.storage.local.get(LAST_KEY))[LAST_KEY] || "";
        if (!config) return {connected: false, lastBase: last};
        try {
            const accounts = await api(config, "/accounts");
            return {
                connected: true, base: config.base, count: accounts.length,
                onboarded: accounts.filter((a) => a.onboarded).length,
                // Паролей в ответе для страниц расширения нет — только то, что видно в списке.
                accounts: accounts.map((a) => ({account_id: a.account_id, email: a.email, onboarded: a.onboarded,
                    has_password: !!a.password})),
            };
        } catch (error) {
            return {connected: false, base: config.base, lastBase: config.base, error: error.message,
                revoked: error.status === 401};
        }
    },
    async claim(message) {
        return claim(message);
    },
    async openOptions() {
        await chrome.runtime.openOptionsPage();
        return {ok: true};
    },
    async closeTab(_message, sender) {
        if (sender?.tab?.id != null) await chrome.tabs.remove(sender.tab.id);
        return {ok: true};
    },
    async disconnect() {
        await chrome.storage.local.remove(STORE_KEY);
        return {ok: true};
    },
    async accounts() {
        return withConfig((config) => api(config, "/accounts"));
    },
    async mailBaseline({email}) {
        return withConfig((config) => api(config, "/mail-code", {method: "POST", body: {email}}));
    },
    async mailPoll({email, cursor}) {
        return withConfig((config) => api(config, "/mail-code", {method: "POST", body: {email, cursor}}));
    },
    async onboard({accountId, iamToken, projectName}) {
        return withConfig((config) => api(config, "/onboard", {
            method: "POST",
            body: {account_id: accountId, iam_token: iamToken, project_name: projectName || ""},
        }));
    },
    async progress({accountId, stage, message}) {
        return withConfig((config) => api(config, "/progress", {
            method: "POST",
            body: {account_id: accountId, stage, message: message || ""},
        }));
    },
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    const handler = handlers[message?.type];
    if (!handler) return false;
    handler(message, sender)
        .then((data) => sendResponse({ok: true, data}))
        .catch((error) => sendResponse({ok: false, error: error.message, code: error.code, status: error.status}));
    return true; // ответ асинхронный
});
