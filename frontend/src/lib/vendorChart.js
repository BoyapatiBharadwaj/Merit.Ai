/**
 * Chart.js, published as `window.Chart`.
 *
 * A separate module from lib/vendor.js so that Rollup places it in whichever
 * route chunk imports it, rather than in the shared entry bundle. Only the
 * admin and examiner dashboards draw charts; a candidate sitting an exam has no
 * reason to download a charting library, and before the split they did.
 *
 * Imported for its side effect at the top of those page modules, which run
 * before their components render -- so `window.Chart` is populated by the time
 * any chart effect looks for it, exactly as the old CDN <script> guaranteed.
 */
import Chart from "chart.js/auto";

window.Chart = Chart;

export default Chart;
