// Module labels stay visible; tooltips reveal only clipped labels.
(() => {
    // Native dock tooltips include the user button and can predate this asset.
    // Check at show time so hover and keyboard focus follow the current labels.
    const tooltip = frappe.ui.Tooltip?.prototype;
    const showTooltip = tooltip?.show;
    if (showTooltip && !showTooltip.vectorDockLabels) {
        tooltip.show = function (...args) {
            const button = this.trigger_el;
            const label = button?.matches("#desk-dock .dock-item")
                && button.querySelector(".dock-item-label, .vector-user-label");
            if (label) {
                const fullName = label.getAttribute("data-full-name");
                if (fullName) this.set_text(fullName);
                const clipped = label.scrollWidth > label.clientWidth + 1
                    || label.scrollHeight > label.clientHeight + 1;
                if (label.getClientRects().length && getComputedStyle(label).visibility !== "hidden"
                    && !clipped) {
                    this.hide();
                    return;
                }
            }
            return showTooltip.apply(this, args);
        };
        tooltip.show.vectorDockLabels = true;
    }

})();
