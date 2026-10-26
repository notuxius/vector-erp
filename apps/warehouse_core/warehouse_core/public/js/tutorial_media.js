/* Tutorial videos are disabled throughout Desk, including lazily loaded help.
 * Keep onboarding actions, progress, and written documentation available.
 */
(() => {
    function isVideoLink(value) {
        try {
            const url = new URL(value, window.location.origin);
            return /(^|\.)(youtube\.com|youtube-nocookie\.com|youtu\.be|vimeo\.com)$/i.test(url.hostname)
                || /\/videos?(\/|$)/i.test(url.pathname);
        } catch {
            return false;
        }
    }

    const onboarding = frappe.widget?.widget_factory?.onboarding?.prototype;
    const openStep = onboarding?.set_open_step;
    if (openStep && !openStep.vectorNoVideos) {
        onboarding.set_open_step = function (step) {
            if (step) {
                step.intro_video_url = "";
                step.video_url = "";
            }
            return openStep.call(this, step);
        };
        onboarding.set_open_step.vectorNoVideos = true;
        onboarding.make_video = () => null;
    }

    // Item and BOM list scripts register video IDs after app scripts have loaded.
    // Disable the shared help entry points instead of just clearing those IDs.
    frappe.help.has_help = () => false;
    frappe.help.show = () => {};
    frappe.help.show_video = () => {};

    function filterHelpLinks() {
        for (const [route, links] of Object.entries(frappe.help.help_links || {})) {
            frappe.help.help_links[route] = links.filter((link) => !isVideoLink(link.url));
        }
    }
    filterHelpLinks();
    for (const owner of [frappe.utils, frappe.ui.SidebarHeader?.prototype]) {
        const original = owner?.get_custom_help_links;
        if (!original || original.vectorNoVideos) continue;
        owner.get_custom_help_links = function (...args) {
            filterHelpLinks();
            return original.apply(this, args);
        };
        owner.get_custom_help_links.vectorNoVideos = true;
    }

    // Older workspace cards can carry video-only links outside onboarding.
    const links = frappe.widget?.widget_factory?.links?.prototype;
    const setBody = links?.set_body;
    if (setBody && !setBody.vectorNoVideos) {
        links.set_body = function (...args) {
            this.links = this.links.filter((link) => !link.youtube_id && !isVideoLink(link.url));
            return setBody.apply(this, args);
        };
        links.set_body.vectorNoVideos = true;
    }
})();
