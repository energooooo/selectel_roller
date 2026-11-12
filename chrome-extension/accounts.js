// Страница подключения: адрес панели + 10-символьный код → bearer-токен расширения.
"use strict";

const CODE_LENGTH = 10;
const $ = (s) => document.querySelector(s);

const views = {loading: $("#loading"), disconnected: $("#disconnected"), connected: $("#connected")};
const form = $("#pair-form");
const addressInput = $("#address");
const otp = $("#otp");
const connectBtn = $("#connect");
const statusEl = $("#status");
const connectedStatus = $("#connected-status");
const clearBtn = $("#clear-code");

TR.paint();
$("#version").textContent = "v" + chrome.runtime.getManifest().version;

// ── Вид ─────────────────────────────────────────────────────────────────────
function show(name) {
    for (const [key, el] of Object.entries(views)) el.hidden = key !== name;
    $("#disconnect-row").hidden = name !== "connected";
}

function chip(tone, text) {
    $("#chip").dataset.tone = tone;
    $("#chip-text").textContent = text;
}

function alertBox(el, tone, text) {
    if (!text) {
        el.hidden = true;
        return;
    }
    const icon = {ok: "check", danger: "warning", warn: "warning"}[tone] || "info";
    el.dataset.tone = tone;
    el.innerHTML = TR.ICONS[icon] + "<span></span>";
    el.querySelector("span").textContent = text;
    el.hidden = false;
}

// ── Ячейки кода ─────────────────────────────────────────────────────────────
const cells = [];
for (let i = 0; i < CODE_LENGTH; i++) {
    if (i === 5) {
        const dash = document.createElement("span");
        dash.className = "dash";
        dash.setAttribute("aria-hidden", "true");
        otp.appendChild(dash);
    }
    const cell = document.createElement("input");
    cell.type = "text";
    cell.inputMode = "text";
    cell.autocomplete = i === 0 ? "one-time-code" : "off";
    cell.spellcheck = false;
    cell.maxLength = CODE_LENGTH; // вставка целиком в любую ячейку раскладывается дальше
    cell.setAttribute("aria-label", `Символ ${i + 1} из ${CODE_LENGTH}`);
    otp.appendChild(cell);
    cells.push(cell);
}

const clean = (text) => String(text || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
const code = () => cells.map((c) => c.value).join("");

function markCell(cell) {
    cell.classList.toggle("filled", !!cell.value);
    if (cell.value) {
        cell.classList.add("pop");
        setTimeout(() => cell.classList.remove("pop"), 140);
    }
}

function spread(from, text) {
    const chars = clean(text).split("");
    let i = from;
    for (const ch of chars) {
        if (i >= CODE_LENGTH) break;
        cells[i].value = ch;
        markCell(cells[i]);
        i++;
    }
    cells[Math.min(i, CODE_LENGTH - 1)].focus();
    afterChange();
}

function afterChange() {
    otp.removeAttribute("aria-invalid");
    clearBtn.hidden = !code();
    if (!statusEl.hidden && statusEl.dataset.tone !== "ok") alertBox(statusEl, "", "");
    // Полный код и адрес на месте — подключаемся без лишнего нажатия.
    if (code().length === CODE_LENGTH && addressInput.value.trim()) form.requestSubmit();
}

cells.forEach((cell, i) => {
    cell.addEventListener("input", () => {
        const value = clean(cell.value);
        if (value.length > 1) {
            spread(i, value);
            return;
        }
        cell.value = value;
        markCell(cell);
        if (value && i < CODE_LENGTH - 1) cells[i + 1].focus();
        afterChange();
    });
    cell.addEventListener("paste", (event) => {
        event.preventDefault();
        const text = clean(event.clipboardData.getData("text"));
        // Вставили код целиком — кладём с первой ячейки, куда бы ни стоял курсор.
        spread(text.length >= CODE_LENGTH ? 0 : i, text);
    });
    cell.addEventListener("keydown", (event) => {
        if (event.key === "Backspace" && !cell.value && i > 0) {
            event.preventDefault();
            cells[i - 1].value = "";
            markCell(cells[i - 1]);
            cells[i - 1].focus();
            afterChange();
        } else if (event.key === "ArrowLeft" && i > 0) {
            event.preventDefault();
            cells[i - 1].focus();
        } else if (event.key === "ArrowRight" && i < CODE_LENGTH - 1) {
            event.preventDefault();
            cells[i + 1].focus();
        }
    });
    cell.addEventListener("focus", () => cell.select());
});

clearBtn.addEventListener("click", () => {
    cells.forEach((c) => {
        c.value = "";
        markCell(c);
    });
    clearBtn.hidden = true;
    cells[0].focus();
});

// Вставили в поле адреса «адрес + код» одной строкой — разложим сами.
addressInput.addEventListener("paste", (event) => {
    const text = event.clipboardData.getData("text").trim();
    const match = /^(\S+)\s+([0-9A-Za-z-]{10,11})$/.exec(text);
    if (!match || clean(match[2]).length !== CODE_LENGTH) return;
    event.preventDefault();
    addressInput.value = match[1];
    spread(0, match[2]);
});

addressInput.addEventListener("input", () => addressInput.removeAttribute("aria-invalid"));

// ── Подключение ─────────────────────────────────────────────────────────────
function originOf(address) {
    let raw = address.trim();
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
        raw = (/^(localhost|127\.0\.0\.1)(:\d+)?(\/|$)/i.test(raw) ? "http://" : "https://") + raw;
    }
    return new URL(raw).origin;
}

function busy(on) {
    connectBtn.disabled = on;
    connectBtn.innerHTML = on ? '<span class="spinner"></span>Подключаем…' : "Подключить к панели";
    cells.forEach((c) => (c.disabled = on));
    addressInput.disabled = on;
}

let submitting = false;
form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting) return;
    let origin;
    try {
        origin = originOf(addressInput.value);
    } catch {
        addressInput.setAttribute("aria-invalid", "true");
        addressInput.focus();
        alertBox(statusEl, "danger", "Укажите адрес панели — например, https://panel.example.ru.");
        return;
    }
    if (code().length !== CODE_LENGTH) {
        otp.setAttribute("aria-invalid", "true");
        otp.classList.remove("shake");
        void otp.offsetWidth;
        otp.classList.add("shake");
        cells[Math.min(code().length, CODE_LENGTH - 1)].focus();
        alertBox(statusEl, "danger", "Введите все 10 символов кода.");
        return;
    }

    submitting = true;
    busy(true);
    alertBox(statusEl, "", "");
    // Доступ к адресу панели спрашиваем здесь, пока нажатие ещё «свежее».
    const granted = await chrome.permissions.request({origins: [origin + "/*"]}).catch(() => false);
    if (!granted) {
        submitting = false;
        busy(false);
        alertBox(statusEl, "warn", "Без доступа к адресу панели расширение не сможет к ней обращаться. Разрешите его в окне браузера.");
        return;
    }
    const reply = await send("claim", {address: addressInput.value, code: code()});
    submitting = false;
    busy(false);
    if (!reply.ok) {
        otp.setAttribute("aria-invalid", "true");
        alertBox(statusEl, "danger", reply.error || "Не удалось подключить расширение.");
        return;
    }
    cells.forEach((c) => {
        c.value = "";
        markCell(c);
    });
    await refresh(true);
});

function send(type, payload) {
    return TR.send(type, payload);
}

// ── Состояние ───────────────────────────────────────────────────────────────
let panelBase = "";

async function refresh(justConnected = false) {
    const reply = await send("status");
    const data = reply.data || {};
    if (data.connected) {
        panelBase = data.base;
        $("#base").textContent = TR.host(data.base);
        $("#base").title = data.base;
        $("#count").textContent = String(data.count ?? "—");
        $("#onboarded").textContent = String(data.onboarded ?? "—");
        chip("ok", "Подключено");
        alertBox(connectedStatus, justConnected ? "ok" : "", justConnected ? "Готово. Откройте панель и нажмите «Войти» у аккаунта." : "");
        show("connected");
        return;
    }
    chip("warn", "Не подключено");
    if (!addressInput.value) addressInput.value = data.lastBase || data.base || "";
    if (data.base && data.error) {
        alertBox(statusEl, data.revoked ? "warn" : "danger", data.revoked
            ? "Панель отключила это расширение. Создайте в ней новый код и введите его здесь."
            : "Панель недоступна: " + data.error);
    }
    show("disconnected");
    (addressInput.value ? cells[0] : addressInput).focus();
}

$("#refresh").addEventListener("click", async () => {
    const btn = $("#refresh");
    btn.disabled = true;
    await refresh();
    btn.disabled = false;
});

$("#open-panel").addEventListener("click", () => {
    if (panelBase) chrome.tabs.create({url: panelBase + "/keys"});
});

$("#disconnect").addEventListener("click", async () => {
    await send("disconnect");
    alertBox(statusEl, "", "");
    await refresh();
    alertBox(statusEl, "ok", "Расширение отключено. Чтобы подключить снова, нужен новый код из панели.");
});

refresh();
