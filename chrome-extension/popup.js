// Попап: статус подключения и аккаунты с кнопкой «Войти» — как в панели, только ближе.
"use strict";

const $ = (s) => document.querySelector(s);
const SELECTEL_LOGIN = "https://my.selectel.ru/login";
const loginUrl = (id) => SELECTEL_LOGIN + "?toffee-account=" + id + "#blank";

TR.paint();
$("#version").textContent = "v" + chrome.runtime.getManifest().version;

let accounts = [];
let base = "";

function show(name) {
    for (const id of ["loading", "disconnected", "connected"]) $("#" + id).hidden = id !== name;
}

function chip(tone, text) {
    $("#chip").dataset.tone = tone;
    $("#chip-text").textContent = text;
}

function openTab(url) {
    chrome.tabs.create({url});
    window.close();
}

function render() {
    const list = $("#list");
    const q = $("#search").value.trim().toLowerCase();
    // Сначала те, кто ещё ждёт входа: их и открывают из попапа.
    const rows = accounts
        .filter((a) => !q || a.account_id.includes(q) || (a.email || "").toLowerCase().includes(q))
        .sort((a, b) => Number(a.onboarded) - Number(b.onboarded));
    list.replaceChildren();
    if (!rows.length) {
        const empty = document.createElement("li");
        empty.className = "empty-list";
        empty.textContent = accounts.length ? "Ничего не найдено" : "В панели пока нет аккаунтов. Загрузите TXT в панели.";
        list.appendChild(empty);
        return;
    }
    for (const a of rows) {
        const li = document.createElement("li");
        li.className = "row";

        const state = document.createElement("span");
        state.className = "state";
        state.dataset.tone = a.onboarded ? "ok" : "";
        state.title = a.onboarded ? "Подключён" : "Ждёт входа";

        const who = document.createElement("div");
        who.className = "who";
        const email = document.createElement("div");
        email.className = "email";
        email.textContent = a.email || "Без почты";
        const id = document.createElement("div");
        id.className = "id mono";
        id.textContent = "№ " + a.account_id + (a.onboarded ? " · подключён" : "");
        who.append(email, id);

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn " + (a.onboarded ? "btn-quiet" : "btn-outline");
        btn.textContent = "Войти";
        btn.disabled = !a.has_password;
        btn.title = a.has_password ? "Откроет вход Selectel и подставит данные" : "Нет пароля — импортируйте аккаунт заново";
        btn.addEventListener("click", () => openTab(loginUrl(a.account_id)));

        li.append(state, who, btn);
        list.appendChild(li);
    }
    updateFade();
}

// Затухание у края списка — только там, куда ещё можно прокрутить.
function updateFade() {
    const list = $("#list");
    const fade = [];
    if (list.scrollTop > 2) fade.push("top");
    if (list.scrollTop + list.clientHeight < list.scrollHeight - 2) fade.push("bottom");
    list.dataset.fade = fade.join(" ");
}

$("#list").addEventListener("scroll", updateFade, {passive: true});

$("#search").addEventListener("input", render);
$("#open-connect").addEventListener("click", () => {
    chrome.runtime.openOptionsPage();
    window.close();
});
$("#open-settings").addEventListener("click", () => {
    chrome.runtime.openOptionsPage();
    window.close();
});
$("#open-panel").addEventListener("click", () => base && openTab(base + "/keys"));
$("#open-selectel").addEventListener("click", () => openTab(SELECTEL_LOGIN));

(async () => {
    const reply = await TR.send("status");
    const data = reply.data || {};
    if (data.connected) {
        base = data.base;
        accounts = data.accounts || [];
        $("#base").textContent = TR.host(base);
        $("#base").title = base;
        $("#search-wrap").hidden = accounts.length <= 5;
        chip("ok", accounts.length ? `${data.onboarded} из ${accounts.length}` : "Подключено");
        const waiting = accounts.length - (data.onboarded || 0);
        $("#list-count").textContent = !accounts.length ? "" : waiting
            ? `ждут входа: ${waiting}` : `все ${accounts.length} подключены`;
        $("#chip").title = "Подключено аккаунтов к панели";
        render();
        show("connected");
        requestAnimationFrame(updateFade);  // размеры списка известны только после показа
        return;
    }
    chip("warn", "Не подключено");
    if (data.base && data.error) {
        $("#empty-title").textContent = data.revoked ? "Панель отключила расширение" : "Панель недоступна";
        $("#empty-text").textContent = data.revoked
            ? "Создайте в панели новый код и введите его заново."
            : data.error;
        $("#open-connect").textContent = data.revoked ? "Ввести новый код" : "Проверить подключение";
    }
    show("disconnected");
})();
