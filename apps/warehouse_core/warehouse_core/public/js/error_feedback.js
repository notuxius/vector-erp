// Frappe appends each msgprint to the same dialog until it is dismissed.
(() => {
    const original = frappe.msgprint;
    const feedback = "/api/method/warehouse_core.warehouse_core.doctype.vectoria_feedback.vectoria_feedback.";
    const label = (en, uk) => frappe.boot.lang === "uk" ? uk : en;
    const section = () => location.pathname.split("/").slice(0, 3).join("/");
    let failedRequest;

    function feedbackRow(dialog) {
        const row = $('<div class="vector-error-feedback mt-3">').appendTo(dialog.msg_area)[0];
        const button = $('<button type="button" class="btn btn-default btn-sm">').appendTo(row)[0];
        const status = $('<span class="ml-2" role="status">').appendTo(row)[0];
        function showReceived(receipt) {
            button.textContent = label("Reload", "Оновити");
            button.onclick = () => location.reload();
            button.disabled = false;
            status.classList.add("text-success");
            status.textContent = receipt
                ? label("✓ The administrator received your report. ", "✓ Адміністратор отримав ваше звернення. ") + receipt
                : label("✓ The administrator already received this report.", "✓ Адміністратор уже отримав це звернення.");
        }
        return {row, button, status, showReceived};
    }

    frappe.vector_feedback_received = (receipt, message = "") => {
        const dialog = original({message: frappe.utils.escape_html(message), indicator: "green"});
        feedbackRow(dialog).showReceived(receipt);
    };

    // Capture only the method and correlation ID, never the request body or query string.
    $.ajaxPrefilter((options, _, xhr) => {
        const method = options.url.match(/^\/api\/(?:v[12]\/)?method\/([\w.]+)(?:\?|$)/)?.[1];
        if (!method) return;
        const page = section();
        xhr.fail(() => {
            failedRequest = {page, http: xhr.status, method, trace: xhr.getResponseHeader("X-Frappe-Request-Id")};
            queueMicrotask(() => { failedRequest = null; });
        });
    });

    window.msgprint = frappe.msgprint = function (msg, title) {
        const data = typeof msg === "string"
            ? (msg.startsWith("{") ? JSON.parse(msg) : {message: msg, title})
            : msg;
        if (typeof data?.message !== "string" || data.alert || data.toast ||
            data.primary_action || data.secondary_action ||
            !(failedRequest || data.raise_exception || data.indicator === "red" ||
              data.message === __("Internal Server Error") || data.message === __("Request Timed Out"))) {
            return original.apply(this, arguments);
        }

        const request = failedRequest;
        const page = request?.page || section();
        const operation = request ? `HTTP ${request.http} · ${request.method}` : "";
        const key = JSON.stringify([data.title || "", data.message.trim(), page, operation]);
        const existing = !data.clear && frappe.msg_dialog?.msg_area?.children(".vector-error-feedback")
            .toArray().find(row => row.errorKey === key);
        if (existing) {
            existing.occurrences++;
            return frappe.msg_dialog;
        }
        const dialog = original.apply(this, arguments);
        const {row, button, status, showReceived} = feedbackRow(dialog);
        row.errorKey = key;
        row.occurrences = 1;
        const template = document.createElement("template");
        template.innerHTML = data.message;
        const message = template.content.textContent.trim().slice(0, 4000) || __("Error");
        const context = [new Date().toISOString(), page, operation, request?.trace].filter(Boolean);
        const requestId = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");
        button.hidden = true;
        button.textContent = label("Report to administrator", "Повідомити адміністратора");
        status.textContent = label("Checking report status…", "Перевіряємо стан звернення…");
        fetch(feedback + "error_received", {method: "POST", signal: AbortSignal.timeout(3000),
            headers: {"X-Frappe-CSRF-Token": frappe.csrf_token}, body: new URLSearchParams({message, page, request: operation})})
            .then(async response => response.ok && (await response.json()).message === true)
            .catch(() => false) // Submission checks the queue again if this lookup fails.
            .then(received => {
                if (received) showReceived();
                else status.textContent = "";
                button.hidden = false;
            });
        button.onclick = async () => {
            if (button.disabled) return;
            button.disabled = true;
            status.textContent = label("Sending…", "Надсилання…");
            const body = new URLSearchParams({category: "Помилка", message, request_id: requestId,
                context: [...context, `× ${row.occurrences}`].join("\n").slice(0, 8000)});
            let delay = 2000;
            while (true) {
                let wait = delay;
                try {
                    // Bypass Desk's error dialog if the feedback request itself fails.
                    const response = await fetch(feedback + "submit", {method: "POST", body, signal: AbortSignal.timeout(15000),
                        headers: {"X-Frappe-CSRF-Token": frappe.csrf_token}});
                    if (response.redirected || (response.status >= 400 && response.status < 500 &&
                        ![408, 429].includes(response.status))) {
                        status.textContent = label("ERP declined the report.", "ERP відхилила звернення.");
                        button.disabled = false;
                        return;
                    }
                    if (response.ok) {
                        const {message: receipt} = await response.json();
                        if (typeof receipt === "string" && receipt.trim()) {
                            showReceived(receipt);
                            return;
                        }
                    } else if (response.status === 429 || response.status === 503) {
                        const seconds = Number(response.headers.get("Retry-After"));
                        if (Number.isFinite(seconds)) wait = Math.max(wait, seconds * 1000);
                    }
                } catch {
                    // A lost response may follow a successful insert. Reuse the same request ID.
                }
                status.textContent = label("Retrying automatically…", "Повторюємо надсилання автоматично…");
                await new Promise(resolve => setTimeout(resolve, wait));
                delay = Math.min(delay * 2, 60000);
            }
        };
        return dialog;
    };
})();
