// Тонкий мост между панелью Toffee Roller и вкладкой Selectel.
// Кокпит — в панели: там список аккаунтов, кнопка «Войти» и живой статус.
// Здесь расширение лишь исполняет: панель открывает вкладку с
// #toffee-account=<номер>, мост подставляет данные выбранного аккаунта, берёт
// код из почты, получает IAM-токен и шлёт каждый шаг обратно в панель
// (через backend). На странице Selectel — карточка хода входа (overlay.js).

(async () => {
    if (window.__toffeeRollerLoaded) return;
    window.__toffeeRollerLoaded = true;

    const SIGNAL = "toffee-roller";
    const TOKEN_URL = "https://api.selectel.ru/iam/v1/tokens";
    const CODE_DEADLINE_MS = 115_000;
    const ACCOUNT_KEY = "toffee-roller.account";
    const MAIL_KEY = "toffee-roller.mail";
    const NAVIGATION_KEY = "toffee-roller.navigation";

    // Сохраняем цель до того, как роутер Selectel уберёт её из адреса.
    const previousAccount = sessionStorage.getItem(ACCOUNT_KEY);
    const accountId = new URLSearchParams(location.search).get("toffee-account")
        || (/[#&?]toffee-account=([0-9]{3,12})/.exec(location.hash) || [])[1]
        || previousAccount;
    if (!accountId || !/^[0-9]{3,12}$/.test(accountId)) return; // вкладка открыта не из панели — молчим
    if (accountId !== previousAccount) {
        sessionStorage.removeItem(NAVIGATION_KEY);
        sessionStorage.removeItem(MAIL_KEY);
    }
    sessionStorage.setItem(ACCOUNT_KEY, accountId);
    if (document.readyState === "loading") {
        await new Promise((resolve) => document.addEventListener("DOMContentLoaded", resolve, {once: true}));
    }

    let selected = null;        // {account_id, email, password}
    let mail = null;            // {cursor, deadline, filled}
    let onboarding = false;
    let done = false;

    // ── Карточка хода входа ────────────────────────────────────────────────────
    const ui = window.ToffeeOverlay.create(accountId);

    // ── Диагностический журнал ─────────────────────────────────────────────────
    // Пишем каждый шаг: куда попали, какой аккаунт активен, какой токен пришёл.
    // Виден в карточке (блок «Журнал» с кнопкой «копировать») и в консоли вкладки.
    const debugLines = [];
    function dbg(...parts) {
        const text = parts.map((p) => (typeof p === "string" ? p : JSON.stringify(p))).join(" ");
        const line = new Date().toISOString().slice(11, 19) + " " + text;
        debugLines.push(line);
        if (debugLines.length > 60) debugLines.shift();
        try { console.log("[ToffeeRoller]", text); } catch {}
        try { ui.debug(debugLines); } catch {}
    }
    dbg("start", "target", accountId, "path", location.pathname, "search", location.search, "hash", location.hash);

    /** Шаг входа: карточка на странице + строка в панели (заголовок и пояснение одной фразой). */
    function report(stage, title, detail = "", extra = {}) {
        const kind = stage === "error" ? "error" : extra.kind || "active";
        ui.set({stage, title, detail, kind, ...extra});
        send("progress", {accountId, stage, message: detail ? title + ". " + detail : title});
    }

    const openSettings = {label: "Открыть подключение", primary: true, run: () => send("openOptions")};

    // ── Общение с background ───────────────────────────────────────────────────
    function send(type, payload = {}) {
        return new Promise((resolve) => {
            chrome.runtime.sendMessage({type, ...payload}, (reply) => {
                resolve(chrome.runtime.lastError ? {ok: false, error: chrome.runtime.lastError.message}
                    : (reply || {ok: false, error: "Нет ответа расширения."}));
            });
        });
    }

    // ── Автоподстановка ────────────────────────────────────────────────────────
    function setValue(input, value) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
        setter.call(input, value);
        input.dispatchEvent(new Event("input", {bubbles: true}));
        input.dispatchEvent(new Event("change", {bubbles: true}));
    }

    function editable(selector, root = document) {
        return Array.from(root.querySelectorAll(selector)).find((input) =>
            !input.disabled && !input.readOnly && input.getClientRects().length > 0
            && getComputedStyle(input).visibility !== "hidden");
    }

    function fillLogin() {
        const password = editable('#login-form input[autocomplete="current-password"]');
        if (!password) return false;
        const form = password.form || document;
        const account = editable('input#username[autocomplete="username"], input#username', form)
            || editable('input[autocomplete="username"]:not([type="password"])', form);
        const emailField = editable('input[type="email"], input[autocomplete="email"]', form);
        if (!account) return false;
        setValue(account, selected.account_id);
        if (emailField && selected.email) setValue(emailField, selected.email);
        setValue(password, selected.password);
        return true;
    }

    const TRANSITION_TIMEOUT_MS = 20_000;
    const MENU_LOAD_MS = 2_000;
    const navigation = {step: "", startedAt: 0};
    const savedNavigation = sessionStorage.getItem(NAVIGATION_KEY);
    if (savedNavigation) {
        const [step, startedAt] = savedNavigation.split(":");
        navigation.step = step;
        navigation.startedAt = Number(startedAt);
    }

    function startTransition(step, element, title, detail) {
        navigation.step = step;
        navigation.startedAt = Date.now();
        sessionStorage.setItem(NAVIGATION_KEY, step + ":" + navigation.startedAt);
        dbg("переход Selectel:", step, "для аккаунта", accountId);
        report("login", title, detail);
        element.click();
    }

    function visibleElement(root, selector) {
        return Array.from(root.querySelectorAll(selector)).find((element) =>
            element.getClientRects().length > 0 && getComputedStyle(element).visibility === "visible");
    }

    function activeAccountId() {
        const label = document.querySelector("panel-profile-button .panel-profile-button-label-account");
        return label?.textContent.match(/\d{3,12}/)?.[0] || null;
    }

    let menuRequested = false;
    let menuVisibleSince = 0;
    let pickerVisibleSince = 0;
    let navigationErrorShown = false;

    function reportNavigationError() {
        if (navigationErrorShown) return;
        navigationErrorShown = true;
        report("error", "Не удалось открыть нужный аккаунт",
            `В меню аккаунтов Selectel выберите № ${accountId} или нажмите «Добавить аккаунт».`);
    }

    function openTargetFromMenu() {
        const menu = visibleElement(document, ".panel-profile-dropdown-menu");
        if (!menu) {
            const profile = visibleElement(document, "panel-profile-button");
            if (!profile || menuRequested) return;
            menuRequested = true;
            profile.click();
            return;
        }
        menuVisibleSince ||= Date.now();
        const label = Array.from(menu.querySelectorAll(".panel-profile-dropdown-account-id")).find((item) =>
            item.textContent.trim().split(/\D/, 1)[0] === accountId);
        const row = label?.closest("li");
        if (row?.getClientRects().length) {
            startTransition("switch", row, "Переключаем аккаунт", `Открываем сессию № ${accountId}.`);
            return;
        }
        // Selectel загружает строки сессий асинхронно при открытии меню.
        const add = visibleElement(menu, 'a[href="/login-sessions#new"]');
        if (add && Date.now() - menuVisibleSince >= MENU_LOAD_MS) {
            startTransition("add", add, "Добавляем аккаунт", "Selectel открывает вход в новую сессию.");
        }
    }

    function openLoginFromPicker() {
        if (navigation.step && navigation.step !== "add") return;
        const other = visibleElement(document, 'my-login-sessions button[routerlink="/login"]');
        if (!other || other.disabled) return;
        pickerVisibleSince ||= Date.now();
        // При #new Selectel сначала временно деактивирует текущую сессию
        // и заново загружает экран выбора. Дожидаемся стабильного экрана.
        if (Date.now() - pickerVisibleSince >= 700) {
            startTransition("form", other, "Открываем форму входа", `Входим в аккаунт № ${accountId}.`);
        }
    }

    let filledLogin = false;
    let tickCount = 0;
    let tokenRequested = false;

    function tick() {
        if (done || !selected) return;
        const path = location.pathname;
        const active = activeAccountId();
        if (path === "/login_tsa") {
            resumeMail();
        } else if (path === "/login") {
            if (!filledLogin && fillLogin()) {
                filledLogin = true;
                report("filled", "Данные подставлены", "Нажмите «Войти» и пройдите капчу, если Selectel её запросит.", {kind: "ok"});
                startMail();
            }
        } else if (path !== "/login-sessions" && active === accountId) {
            if (!tokenRequested) requestAccountToken();
        } else if (navigation.step && Date.now() - navigation.startedAt >= TRANSITION_TIMEOUT_MS) {
            reportNavigationError();
        } else if (path === "/login-sessions") {
            openLoginFromPicker();
        } else if (active && !navigation.step && !filledLogin) {
            openTargetFromMenu();
        }
        if (path !== "/login-sessions") pickerVisibleSince = 0;

        if (tickCount < 150) setTimeout(tick, tickCount++ < 8 ? 350 : 700);
    }

    // ── Код из MailyGet ────────────────────────────────────────────────────────
    function otpInputs() {
        const visible = (i) => i.getClientRects().length > 0 && !i.disabled && !i.readOnly;
        const explicit = Array.from(document.querySelectorAll('input[autocomplete="one-time-code"]')).filter(visible);
        if (explicit.length) return explicit;
        const numeric = Array.from(document.querySelectorAll('input[inputmode="numeric"], input[type="tel"]'))
            .filter((i) => visible(i) && i.type !== "password");
        if (numeric.length) return numeric;
        // Экран кода у Selectel — одно обычное текстовое поле без inputmode.
        // Берём его, только если это единственное видимое поле (не логин/почта/пароль).
        const texts = Array.from(document.querySelectorAll("input")).filter((i) =>
            visible(i) && ["text", "tel", "number", ""].includes(i.type)
            && i.autocomplete !== "username" && i.autocomplete !== "email" && i.type !== "password");
        return texts.length === 1 ? texts : [];
    }

    function fillCode(code) {
        const inputs = otpInputs();
        if (!inputs.length) return false;
        if (inputs.length === 1 || inputs[0].maxLength >= code.length) {
            inputs[0].focus();
            setValue(inputs[0], code);
        } else {
            code.split("").forEach((digit, index) => {
                const box = inputs[index];
                if (box) { box.focus(); setValue(box, digit); }
            });
        }
        return true;
    }

    function persistMail() {
        try {
            if (mail && !mail.filled) {
                sessionStorage.setItem(MAIL_KEY, JSON.stringify({cursor: mail.cursor, email: mail.email, deadline: mail.deadline}));
            } else {
                sessionStorage.removeItem(MAIL_KEY);
            }
        } catch {}
    }

    async function startMail() {
        if (!selected || !selected.email || mail) return;
        report("otp_wait", "Ждём код из почты", "Нажмите «Войти» в Selectel — код введётся сам.",
            {deadline: Date.now() + CODE_DEADLINE_MS});
        const base = await send("mailBaseline", {email: selected.email});
        if (!base.ok) {
            report("error", "Почта недоступна", base.error || "Не удалось обратиться к MailyGet. Введите код вручную.");
            return;
        }
        mail = {cursor: base.data.cursor, email: selected.email, deadline: Date.now() + CODE_DEADLINE_MS, filled: false};
        persistMail();
        pollMail();
    }

    // Экран ввода кода (/login_tsa) — отдельная страница: подхватываем сохранённый
    // снимок почты и продолжаем опрос, даже если baseline снимался на /login.
    function resumeMail() {
        if (mail) return;
        let saved = null;
        try {
            saved = JSON.parse(sessionStorage.getItem(MAIL_KEY) || "null");
        } catch {}
        if (!saved || !saved.cursor || Date.now() >= saved.deadline) {
            try { sessionStorage.removeItem(MAIL_KEY); } catch {}
            return;
        }
        mail = {cursor: saved.cursor, email: saved.email, deadline: saved.deadline, filled: false};
        ui.set({stage: "otp_wait", title: "Ждём код из почты", detail: "Письмо от Selectel придёт через несколько секунд.",
            kind: "active", deadline: saved.deadline});
        pollMail();
    }

    let fillCodeTries = 0;
    function tryFillCode(code) {
        if (!mail || mail.filled) return;
        if (fillCode(code)) {
            mail.filled = true;
            persistMail();
            report("code", "Код из почты подставлен", "Подтвердите вход в Selectel.", {kind: "ok", code});
        } else if (fillCodeTries++ < 30) {
            setTimeout(() => tryFillCode(code), 400); // поле кода ещё не отрисовано
        } else {
            mail.filled = true;
            persistMail();
            report("code", "Код пришёл", "Поле кода не нашлось — введите его вручную.", {kind: "ok", code});
        }
    }

    async function pollMail() {
        if (!mail || mail.filled) return;
        if (Date.now() >= mail.deadline) {
            report("error", "Код не пришёл", "Запросите код заново в Selectel — расширение снова его подхватит.");
            mail = null;
            persistMail();
            return;
        }
        const reply = await send("mailPoll", {email: mail.email, cursor: mail.cursor});
        if (!mail) return;
        if (!reply.ok) {
            report("error", "Не удалось прочитать почту", reply.error || "MailyGet не ответил. Введите код вручную.");
            mail = null;
            persistMail();
            return;
        }
        mail.cursor = reply.data.cursor || mail.cursor;
        persistMail();
        if (reply.data.code) {
            tryFillCode(reply.data.code);
            return;
        }
        setTimeout(pollMail, 3000);
    }

    // ── Завершение: IAM-токен → онбординг ──────────────────────────────────────
    async function finishOnboarding(token) {
        if (onboarding || done || !selected) return;
        onboarding = true;
        dbg("onboard ->", "аккаунт", selected.account_id, "активен", activeAccountId() || "?");
        report("connecting", "Подключаем аккаунт", "Вход выполнен — передаём ключ в панель Toffee Roller.");
        const reply = await send("onboard", {accountId, iamToken: token});
        onboarding = false;
        dbg("onboard <-", reply.ok ? "ok" : "FAIL", reply.ok ? "" : (reply.error || ""));
        if (!reply.ok) {
            if (/не совпада/i.test(reply.error || "")) {
                reportNavigationError();
                return;
            }
            report("error", "Не удалось подключить", reply.error || "Панель не приняла аккаунт. Попробуйте ещё раз.",
                {actions: [{label: "Повторить", primary: true, run: requestAccountToken}]});
            return;
        }
        done = true;
        sessionStorage.removeItem(ACCOUNT_KEY);
        sessionStorage.removeItem(NAVIGATION_KEY);
        sessionStorage.removeItem(MAIL_KEY);
        const keyName = reply.data && reply.data.key && reply.data.key.name;
        ui.set({stage: "done", kind: "done", title: "Аккаунт подключён",
            detail: keyName ? `Ключ «${keyName}» в панели. Вкладку можно закрыть.` : "Ключ в панели. Вкладку можно закрыть.",
            actions: [{label: "Закрыть вкладку", primary: true, run: () => send("closeTab")}]});
        // Итоговую стадию «done» панели уже прислал backend из /onboard.
    }

    function reportTokenError(detail) {
        report("error", "Не удалось получить IAM-токен", detail,
            {actions: [{label: "Повторить", primary: true, run: requestAccountToken}]});
    }

    let tokenInFlight = false;
    async function requestAccountToken() {
        if (!selected || done || tokenInFlight || onboarding) return;
        if (location.pathname.startsWith("/login") || activeAccountId() !== accountId) {
            reportNavigationError();
            return;
        }
        tokenRequested = true;
        tokenInFlight = true;
        report("login", "Получаем IAM-токен", `Вход в аккаунт № ${accountId} выполнен.`);
        try {
            const res = await fetch(TOKEN_URL, {
                method: "POST", credentials: "include",
                headers: {"Accept": "application/json", "Content-Type": "application/json"},
                body: JSON.stringify({token: {domain_name: accountId}}),
                redirect: "error", signal: AbortSignal.timeout(12_000),
            });
            const token = res.headers.get("x-subject-token");
            const type = res.headers.get("content-type") || "";
            dbg("IAM API", res.status, type, "токен получен", !!token);
            if (!res.ok) {
                reportTokenError(`Selectel вернул HTTP ${res.status}. Проверьте доступ к аккаунту и повторите.`);
                return;
            }
            if (!token || !type.toLowerCase().startsWith("application/json")) {
                reportTokenError("В ответе Selectel отсутствует IAM-токен или данные аккаунта.");
                return;
            }
            const {token: info} = await res.json();
            const scopeAccount = info?.domain?.name ?? info?.account?.name ?? info?.account_name;
            if (info?.project || String(scopeAccount) !== accountId || activeAccountId() !== accountId) {
                reportTokenError(`Selectel не подтвердил область доступа «Аккаунт № ${accountId}».`);
                return;
            }
            await finishOnboarding(token);
        } catch (e) {
            dbg("IAM API: ошибка", e.name);
            reportTokenError("Запрос к IAM API Selectel не завершился. Повторите попытку.");
        } finally {
            tokenInFlight = false;
        }
    }

    // ── События входа от inject.js ─────────────────────────────────────────────
    window.addEventListener(SIGNAL, (event) => {
        const detail = event.detail || {};
        if (detail.stage === "login-submitted" || detail.stage === "otp-sent") {
            dbg("inject:", detail.stage, "— снимаем baseline почты");
            startMail();
        }
    });

    // ── Старт ──────────────────────────────────────────────────────────────────
    (async () => {
        ui.set({stage: "login", kind: "active", title: "Готовим вход", detail: "Берём данные аккаунта из панели."});
        const reply = await send("accounts");
        if (!reply.ok) {
            // Без подключения к панели прогресс туда не отправить — показываем только здесь.
            if (reply.code === "not-connected") {
                ui.set({stage: "error", kind: "error", title: "Расширение не подключено",
                    detail: "Введите адрес панели и код — и откройте вход заново.", actions: [openSettings]});
            } else {
                ui.set({stage: "error", kind: "error", title: "Панель недоступна",
                    detail: reply.error || "Не удалось получить список аккаунтов.", actions: [openSettings]});
            }
            return;
        }
        selected = (reply.data || []).find((a) => a.account_id === accountId) || null;
        if (!selected) {
            report("error", "Аккаунта нет в панели", `№ ${accountId} не найден — импортируйте его заново.`);
            return;
        }
        if (!selected.password) {
            report("error", "Нет пароля", "Импортируйте аккаунт заново вместе с паролем.");
            return;
        }
        dbg("аккаунт найден", "есть пароль", !!selected.password, "активен сейчас", activeAccountId() || "?");
        tick();
    })();
})();
