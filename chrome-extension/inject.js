// Выполняется в MAIN-мире страницы my.selectel.ru (world: MAIN в манифесте).
// Сообщает content.js о запросах входа и почтового кода Selectel.
(() => {
    const SIGNAL = "toffee-roller";

    function emit(stage) {
        window.dispatchEvent(new CustomEvent(SIGNAL, {detail: {stage}}));
    }

    function look(url, method) {
        try {
            if (method.toUpperCase() !== "POST") return;
            const {origin, pathname} = new URL(url, location.origin);
            if (origin !== location.origin && origin !== "https://api.selectel.ru") return;
            if (pathname.endsWith("/v2/login")) emit("login-submitted");
            else if (pathname.endsWith("/v1/otp/email")) emit("otp-sent");
            else if (pathname.endsWith("/v1/otp/email/verify")) emit("otp-verified");
        } catch {
            // Разбор URL/ответа не должен ломать запросы страницы.
        }
    }

    const origFetch = window.fetch;
    window.fetch = function (input, init) {
        const url = typeof input === "string" ? input : (input && input.url) || "";
        const method = (init && init.method) || (input && input.method) || "GET";
        return origFetch.apply(this, arguments).then((response) => {
            look(url, method);
            return response;
        });
    };

    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
        this.__toffee = {method, url};
        return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
        const meta = this.__toffee;
        if (meta) {
            this.addEventListener("load", () => look(meta.url, meta.method), {once: true});
        }
        return origSend.apply(this, arguments);
    };
})();
