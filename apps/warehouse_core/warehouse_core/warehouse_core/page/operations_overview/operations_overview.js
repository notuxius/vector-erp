frappe.pages["operations-overview"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Dashboard"), single_column: true });
    const $root = $('<div class="vector-overview">').appendTo(page.main);
    const escape = value => frappe.utils.escape_html(String(value ?? ""));
    let requestId = 0;
    let selectedMonths = 6;
    let chartInstances = [];
    $(wrapper).addClass("vector-overview-page");
    const periodLabel = months => months === 3 ? "3 місяці" : `${months} місяців`;
    const periodMenu = new frappe.ui.Dropdown({
        button: { label: "Період графіків", icon: "calendar", css_class: "vo-period-button" },
        align: "end",
        options: () => [{ group: "Період графіків", options: [3, 6, 12].map(months => ({
            label: periodLabel(months), selected: selectedMonths === months,
            icon_right: selectedMonths === months ? "check" : undefined,
            css_class: "vo-period-choice",
            onclick: () => {
                if (selectedMonths === months) return;
                selectedMonths = months;
                updatePeriodButton();
                refresh();
            },
        })) }],
        on_open() {
            document.querySelectorAll(".es-menu .vo-period-choice").forEach(row => {
                row.setAttribute("role", "menuitemradio");
                row.setAttribute("aria-checked", String(row.getAttribute("data-state") === "checked"));
            });
        },
    });
    periodMenu.$trigger.insertAfter(page.vector_appearance.$trigger);
    function updatePeriodButton() {
        periodMenu.$trigger.find(".es-button__label").html(
            `<span class="vo-period-caption">Період графіків: </span>${selectedMonths} міс.`);
        periodMenu.$trigger.attr("aria-label", `Період графіків: ${periodLabel(selectedMonths)}`);
    }
    updatePeriodButton();
    const chartKeys = { sales: "sales", accounting: "payments", purchasing: "purchases", stock: "stock" };
    const destroyCharts = () => { chartInstances.forEach(chart => chart.destroy()); chartInstances = []; };
    const money = (value, currency) => new Intl.NumberFormat("uk-UA", {
        style: "currency", currency, maximumFractionDigits: 2,
    }).format(value);
    const chartLabels = chart => chart.key === "stock" ? chart.labels : chart.labels.map(value => {
        const [year, month] = value.split("-").map(Number);
        return new Intl.DateTimeFormat("uk-UA", { month: "short", year: "2-digit" }).format(new Date(year, month - 1, 1));
    });
    function chartMarkup(chart, currency) {
        if (!chart) return "";
        const labels = chartLabels(chart);
        return `<figure class="vo-chart" data-chart="${escape(chart.key)}">
            <figcaption><strong>${escape(chart.title)}</strong><span>${escape(currency)}</span></figcaption>
            <p class="vo-chart-description">${escape(chart.description)}</p>
            ${chart.has_data ? `<div class="vo-chart-canvas" role="img" aria-label="${escape(chart.title)}. ${escape(currency)}. Точні значення — у таблиці нижче."></div>`
                : `<div class="vo-chart-empty" role="status">${chart.key === "stock" ? "Немає запасів із ненульовою вартістю." : "За цей період немає проведених документів."}</div>`}
            <div class="vo-chart-totals">${chart.datasets.map((dataset, i) => `<span><i style="background:${escape(chart.colors[i])}" aria-hidden="true"></i>${escape(dataset.name)}: <b>${escape(money(dataset.values.reduce((sum, value) => sum + value, 0), currency))}</b></span>`).join("")}</div>
            <details class="vo-chart-data vector-disclosure"><summary>Дані графіка</summary><div class="vo-chart-table"><table>
                <thead><tr><th scope="col">${chart.key === "stock" ? "Склад" : "Місяць"}</th>${chart.datasets.map(dataset => `<th scope="col">${escape(dataset.name)} (${escape(currency)})</th>`).join("")}</tr></thead>
                <tbody>${labels.map((label, index) => `<tr><th scope="row">${escape(label)}</th>${chart.datasets.map(dataset => `<td>${escape(money(dataset.values[index], currency))}</td>`).join("")}</tr>`).join("")}</tbody>
            </table></div></details></figure>`;
    }
    const listUrl = (doctype, filters = {}) => {
        const query = new URLSearchParams(Object.entries(filters).map(([key, value]) =>
            [key, Array.isArray(value) ? JSON.stringify(value) : value]));
        return `/desk/${frappe.router.slug(doctype)}?${query}`;
    };
    function render(data) {
        const view = frappe.vector_overview_navigation.capture();
        destroyCharts();
        const chartData = data.chart_data;
        $root.html(`
            ${data.tables.length ? `<section id="vo-attention" tabindex="-1" aria-labelledby="vo-attention-heading">
                <h3 class="vo-section-heading" id="vo-attention-heading">Потребує уваги</h3>
                <p class="vo-status">Прострочені замовлення клієнтів і постачальникам. У кожному списку — до 6 найдавніших записів.</p>
                <div class="vo-tables">
                ${data.tables.map(table => `<article class="vo-panel">
                    <div class="vo-panel-heading"><h3>${escape(table.label)}</h3>
                    <a href="${escape(listUrl(table.doctype, table.filters))}">Усі записи →</a></div>
                    ${table.rows.length ? `<div class="vo-records">${table.rows.map(row => {
                        const dateValue = row.delivery_date || row.schedule_date;
                        return `<a class="vo-record" href="/desk/${frappe.router.slug(table.doctype)}/${encodeURIComponent(row.name)}">
                            <div><strong>${escape(row.customer_name || row.supplier_name)}</strong>
                            <span>${escape(row.name)}</span></div>
                            <time>${dateValue ? escape(frappe.datetime.str_to_user(dateValue.split(" ")[0])) : "Без дати"}</time>
                        </a>`;
                    }).join("")}</div>` : `<div class="vo-empty"><span class="vo-empty-icon" aria-hidden="true">✓</span>
                        ${table.doctype === "Sales Order" ? "Прострочених відвантажень немає" : "Прострочених надходжень немає"}</div>`}
                </article>`).join("")}
                </div>
            </section>` : ""}
            <div class="vo-status" role="status">Оновлено: ${escape(frappe.datetime.str_to_user(data.updated_at))} · Дані в межах вашого доступу</div>
            <p class="vo-chart-range">Графіки: ${escape(frappe.datetime.str_to_user(chartData.start))} — ${escape(frappe.datetime.str_to_user(chartData.end))}. Запаси — поточний стан.</p>
            ${chartData.message ? `<p class="vo-chart-notice" role="status">${escape(chartData.message)}</p>` : ""}
            <section class="vo-domains" aria-label="Огляд напрямів">
                ${data.sections.map(section => `<article class="vo-panel vo-domain vo-${escape(section.color)}" data-section="${escape(section.key)}" id="vo-${escape(section.key)}" tabindex="-1" aria-label="${escape(section.label)}">
                    <div class="vo-panel-heading"><h3>${escape(section.label)}</h3>
                        <a href="/desk/${escape(section.workspace)}">Відкрити розділ →</a></div>
                    <div class="vo-metrics" aria-label="${escape(section.label)}">
                ${data.cards.filter(card => card.section === section.key).map(card => `<a class="vo-metric" href="${escape(listUrl(card.doctype, card.filters))}">
                    <span class="vo-metric-label">${escape(card.label)} <span aria-hidden="true">↗</span></span>
                    <strong>${escape(Number(card.value).toLocaleString("uk-UA"))}</strong>
                    <span class="vo-description">${escape(card.description)}</span>
                </a>`).join("")}</div>${chartMarkup(chartData.charts.find(chart => chart.key === chartKeys[section.key]), chartData.currency)}</article>`).join("")}
            </section>
            ${!data.cards.length ? '<p class="vo-empty">Для вашої ролі немає доступних операційних показників.</p>' : ""}
            ${data.links.length ? `<section class="vo-panel vo-shortcuts" id="vo-shortcuts" tabindex="-1" aria-label="Швидкий доступ"><div class="vo-panel-heading"><h3>Швидкий доступ</h3></div>
                <div class="vo-links">${data.links.map(link => {
                    const href = link.kind === "DocType" ? listUrl(link.target)
                        : `/desk/query-report/${encodeURIComponent(link.target)}`;
                    return `<a href="${escape(href)}"><strong>${escape(link.label)} <span aria-hidden="true">↗</span></strong>
                        <span>${escape(link.description)}</span></a>`;
                }).join("")}</div>
            </section>` : ""}`);
        for (const chart of chartData.charts) {
            const host = $root.find(`[data-chart="${chart.key}"] .vo-chart-canvas`)[0];
            if (!host) continue;
            const labels = chartLabels(chart);
            // Frappe Charts writes SVG labels as HTML; escape names from business records.
            const displayLabels = labels.map(label => escape(label.length > 22 ? label.slice(0, 20) + "…" : label));
            chartInstances.push(frappe.utils.make_chart(host, {
                type: chart.type, height: 240, animate: false, disableEntryAnimation: true, colors: chart.colors,
                data: { labels: displayLabels, datasets: chart.datasets },
                barOptions: { spaceRatio: 0.35 },
                axisOptions: { xIsSeries: chart.key !== "stock", shortenYAxisNumbers: 1 },
                tooltipOptions: {
                    formatTooltipX: label => escape(labels[displayLabels.indexOf(label)] || label),
                    formatTooltipY: value => escape(money(value, chartData.currency)),
                },
            }));
        }
        frappe.vector_overview_navigation.mount($root[0], data, view);
    }
    async function refresh() {
        const current = ++requestId;
        $root.attr("aria-busy", "true");
        if (!$root.children().length) $root.html('<p class="vo-empty" role="status">Завантаження огляду…</p>');
        else $root.find('.vo-status[role="status"]').text("Оновлення даних…");
        try {
            const result = await frappe.call({
                method: "warehouse_core.dashboard.get_overview",
                args: { months: selectedMonths },
            });
            if (current === requestId) render(result.message);
        } catch (error) {
            if (current === requestId) {
                destroyCharts();
                $root.empty().append($("<div>", { class: "vo-empty", role: "alert",
                    text: "Не вдалося завантажити огляд. Спробуйте оновити дані." }));
                frappe.vector_overview_navigation.mount($root[0], null);
            }
        } finally {
            if (current === requestId) $root.attr("aria-busy", "false");
        }
    }
    page.set_primary_action("Оновити", refresh, "refresh");
    wrapper.refresh_overview = refresh;
    $(wrapper).on("hide.vectorCharts", () => {
        periodMenu.close("owner");
        ++requestId;
        destroyCharts();
        frappe.vector_overview_navigation.detach();
    });
};

frappe.pages["operations-overview"].on_page_show = function (wrapper) {
    if (!frappe.get_route()[0]) {
        frappe.set_route("operations-overview");
        return;
    }
    wrapper.refresh_overview();
};
