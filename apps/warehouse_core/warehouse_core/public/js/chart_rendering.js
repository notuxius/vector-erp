// Frappe Charts temporarily swaps its SVG for a SMIL animation clone. A resize
// can redraw during that interval, before the library restores the original SVG.
(() => {
    const Chart = frappe.Chart;
    if (!Chart || Chart.vectorResizeSafe) return;

    frappe.Chart = class extends Chart {
        static vectorResizeSafe = true;

        constructor(...args) {
            // The public factory returns the concrete chart (axis, pie, etc.).
            super(...args);
            const makeChartArea = this.makeChartArea;
            this.makeChartArea = function (...areaArgs) {
                if (this.svg && this.svg.parentNode !== this.container) {
                    // Remove only this chart's temporary SVG. Its animation timer
                    // checks parentNode, so it cannot restore the obsolete drawing
                    // after the resize. Tooltips and the container stay intact.
                    this.container.querySelectorAll(':scope > svg.frappe-chart')
                        .forEach(svg => svg.remove());
                    this.svg = null;
                }
                return makeChartArea.apply(this, areaArgs);
            };
        }
    };
})();
