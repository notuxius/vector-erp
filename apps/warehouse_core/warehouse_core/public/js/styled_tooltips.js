// Adapt legacy Desk title attributes to the same component as native styled tips.
(() => {
    if (frappe.vector_tooltip) return;
    const attribute = "data-vector-tooltip";
    const silent = ".vector-top-actions .navbar-modal-search-mobile, .vector-top-actions .sidebar-notification";
    // Measure the text boxes, not just the trigger: a breadcrumb/link can fit
    // while its nested label is ellipsized. Keep icon-only controls named.
    function needsTooltip(element) {
        if (!element?.isConnected || element.closest(silent)) return false;
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        let hasVisibleText = false;
        for (let text = walker.nextNode(); text; text = walker.nextNode()) {
            if (!text.textContent.trim()) continue;
            const label = text.parentElement;
            if (label.closest("svg, .sr-only, .visually-hidden, [aria-hidden=true]")) continue;
            if (!label.getClientRects().length || getComputedStyle(label).visibility !== "visible") continue;
            hasVisibleText = true;
            for (let box = label; box; box = box.parentElement) {
                const style = getComputedStyle(box);
                if (style.display !== "inline" && (
                    (style.overflowX !== "visible" && box.scrollWidth > box.clientWidth + 1)
                    || (style.overflowY !== "visible" && box.scrollHeight > box.clientHeight + 1)
                )) return true;
                if (box === element) break;
            }
        }
        return !hasVisibleText;
    }

    // Apply the same rule to native styled tips and converted legacy titles.
    // Checking when the delay expires also handles labels resized during hover.
    const tooltip = frappe.ui.Tooltip.prototype;
    const show = tooltip.show;
    const hide = tooltip.hide;
    tooltip.show = function (...args) {
        if (!needsTooltip(this.trigger_el)) {
            this.hide();
            return;
        }
        const result = show.apply(this, args);
        if (this.bubble && !this.vectorResizeObserver) {
            this.vectorResizeObserver = new ResizeObserver(() => {
                if (!needsTooltip(this.trigger_el)) this.hide();
            });
            for (const box of [this.trigger_el, ...this.trigger_el.querySelectorAll("*")]) {
                this.vectorResizeObserver.observe(box);
            }
        }
        return result;
    };
    tooltip.hide = function (...args) {
        this.vectorResizeObserver?.disconnect();
        this.vectorResizeObserver = null;
        return hide.apply(this, args);
    };
    let active;
    const release = () => {
        active?.destroy();
        active = null;
    };

    frappe.vector_tooltip = function (element, text) {
        element.removeAttribute("title");
        text = element.closest(silent) ? "" : String(text || "").trim();
        // Leave controls already using Frappe's tooltip in charge of their own tips.
        if ($(element).data("es-tooltip")) text = "";
        if (text) {
            if (element.getAttribute(attribute) !== text) element.setAttribute(attribute, text);
        } else {
            element.removeAttribute(attribute);
        }
        if (active?.trigger_el === element) {
            if (text) active.set_text(text);
            else release();
        }
    };

    function convert(element) {
        if (!element.hasAttribute("title")) return;
        const text = element.getAttribute("title");
        // A title may be the only accessible name of an icon button or frame.
        if (text && !element.hasAttribute("aria-label") && !element.hasAttribute("aria-labelledby")
            && (element.matches("iframe") || (element.matches("button, a, input, [role=button]")
                && !element.textContent.trim()))) {
            element.setAttribute("aria-label", text);
        }
        frappe.vector_tooltip(element, text);
    }
    function scan(root) {
        if (root.nodeType !== Node.ELEMENT_NODE) return;
        convert(root);
        root.querySelectorAll("[title]").forEach(convert);
    }

    function claim(event, keyboard) {
        const target = event.target.closest?.(`[${attribute}]`);
        if (!target || target.closest(silent)) return;
        // Do not compete with a styled tooltip on a nested icon or button.
        for (let node = event.target; node; node = node.parentElement) {
            if ($(node).data("es-tooltip")) return;
            if (node === target) break;
        }
        if (keyboard && !event.target.matches(":focus-visible")) return;
        if (active?.trigger_el === target) return;
        release();
        active = new frappe.ui.Tooltip(target, { text: target.getAttribute(attribute) });
        if (keyboard) active.schedule(0);
        else active.onenter();
    }

    function setup() {
        scan(document.body);
        // Observe only titles and inserted subtrees, never our own data attributes.
        new MutationObserver(records => {
            for (const record of records) {
                if (record.type === "attributes") convert(record.target);
                else record.addedNodes.forEach(scan);
            }
            if (active && !active.trigger_el.isConnected) release();
        }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["title"] });
        document.addEventListener("pointerover", event => claim(event, false));
        document.addEventListener("pointerout", event => {
            if (active && !active.trigger_el.contains(event.relatedTarget)) release();
        });
        document.addEventListener("focusin", event => claim(event, true));
        document.addEventListener("focusout", release);
        window.addEventListener("resize", release);
        $(document).on("page-change.vectorTooltips", release);
        // Upstream About uses its title as the copy value. Keep that action
        // working after moving the same text into the styled-tooltip attribute.
        $(document).on("click.vectorTooltips", ".about-app-row[data-vector-tooltip]", function () {
            frappe.utils.copy_to_clipboard(this.getAttribute(attribute));
        });
    }
    $(setup);
})();
