// Derive navigation accents from the displayed app logo, without saving preferences.
(() => {
    const colors = new Map();
    const headers = new WeakMap();
    function logoColor(source) {
        if (colors.has(source)) return colors.get(source);
        const result = new Promise(resolve => {
            const image = new Image();
            image.crossOrigin = "anonymous";
            image.onerror = () => resolve(null);
            image.onload = () => {
                try {
                    const canvas = document.createElement("canvas");
                    const scale = 64 / Math.max(image.naturalWidth, image.naturalHeight);
                    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
                    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
                    const context = canvas.getContext("2d", { willReadFrequently: true });
                    context.drawImage(image, 0, 0, canvas.width, canvas.height);
                    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
                    const buckets = new Map();
                    for (let i = 0; i < pixels.length; i += 4) {
                        const [r, g, b, alpha] = pixels.subarray(i, i + 4);
                        const high = Math.max(r, g, b);
                        const low = Math.min(r, g, b);
                        // Ignore transparent edges and neutral backgrounds; retain bright yellows.
                        if (alpha < 128 || high < 24 || (high - low) / high < 0.15) continue;
                        const key = [r >> 4, g >> 4, b >> 4].join(",");
                        const bucket = buckets.get(key) || { weight: 0, r: 0, g: 0, b: 0 };
                        const weight = alpha / 255;
                        bucket.weight += weight;
                        bucket.r += r * weight;
                        bucket.g += g * weight;
                        bucket.b += b * weight;
                        buckets.set(key, bucket);
                    }
                    const dominant = [...buckets.values()].sort((a, b) => b.weight - a.weight)[0];
                    resolve(dominant ? `rgb(${[dominant.r, dominant.g, dominant.b]
                        .map(value => Math.round(value / dominant.weight)).join(", ")})` : null);
                } catch (_) {
                    // Cross-origin or unsupported artwork must not break navigation.
                    resolve(null);
                }
            };
            image.src = source;
        });
        // Keep repeat route changes cheap, without retaining unlimited logo versions.
        if (colors.size >= 16) colors.delete(colors.keys().next().value);
        colors.set(source, result);
        return result;
    }

    function sync(home) {
        const dock = home?.closest("#desk-dock");
        if (!dock) return;
        let state = headers.get(home);
        if (!state) {
            state = {};
            headers.set(home, state);
            // Native Dock.refresh replaces the image; also follow direct source changes.
            new MutationObserver(() => sync(home)).observe(home, {
                childList: true, subtree: true, attributes: true, attributeFilter: ["src", "srcset"],
            });
        }
        const image = home.querySelector(".header-logo img");
        const source = image?.src || "";
        home.classList.toggle("vector-logo-mask", !!source);
        if (state.source === source) return;
        state.source = source;
        dock.style.removeProperty("--vector-logo-color");
        if (!source) {
            home.style.removeProperty("--vector-logo-mask");
            return;
        }
        home.style.setProperty("--vector-logo-mask", `url(${JSON.stringify(source)})`);
        logoColor(source).then(color => {
            // A slower previous logo must never overwrite the current app's accent.
            if (state.source === source && color) {
                dock.style.setProperty("--vector-logo-color", color);
            }
        });
    }

    frappe.vector_navigation_logo = { sync };
})();
