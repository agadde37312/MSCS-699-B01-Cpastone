/* ============================================================
   HealthTrack Dashboard JavaScript
   ============================================================
   This is the "before" version: normal, human-readable JS with
   comments, not yet minified, and every chart is rendered
   eagerly regardless of whether it is visible on screen.
   ============================================================ */

// Generates a plausible-looking synthetic time series so the
// demo has something to draw without needing a live backend.
function generateSeries(points, baseValue, variance) {
    var series = [];
    var value = baseValue;
    for (var i = 0; i < points; i++) {
        value = value + (Math.random() - 0.5) * variance;
        series.push(value);
    }
    return series;
}

// A intentionally non-trivial rendering routine: draws axes,
// gridlines, and a data path onto a canvas element. Real chart
// libraries (Chart.js, D3, Plotly) do considerably more work
// than this per chart, so this stands in as a representative
// "this costs real CPU time" placeholder.
function renderChart(canvasId, type) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) {
        return;
    }
    var ctx = canvas.getContext("2d");
    var width = canvas.width;
    var height = canvas.height;

    ctx.clearRect(0, 0, width, height);

    // Draw a border around the plotting area
    ctx.strokeStyle = "#dddddd";
    ctx.strokeRect(0, 0, width, height);

    // Draw gridlines
    ctx.strokeStyle = "#eeeeee";
    for (var gx = 0; gx < width; gx += 50) {
        ctx.beginPath();
        ctx.moveTo(gx, 0);
        ctx.lineTo(gx, height);
        ctx.stroke();
    }
    for (var gy = 0; gy < height; gy += 30) {
        ctx.beginPath();
        ctx.moveTo(0, gy);
        ctx.lineTo(width, gy);
        ctx.stroke();
    }

    if (type === "line") {
        var series = generateSeries(60, height / 2, 40);
        ctx.strokeStyle = "#1f4e78";
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (var i = 0; i < series.length; i++) {
            var x = (i / series.length) * width;
            var y = Math.max(10, Math.min(height - 10, series[i]));
            if (i === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        }
        ctx.stroke();
    } else if (type === "bar") {
        var bars = generateSeries(12, height / 2, 60);
        var barWidth = width / bars.length;
        ctx.fillStyle = "#2e7d32";
        for (var b = 0; b < bars.length; b++) {
            var barHeight = Math.max(5, Math.min(height - 10, bars[b]));
            ctx.fillRect(b * barWidth + 4, height - barHeight, barWidth - 8, barHeight);
        }
    } else if (type === "pie") {
        var slices = [0.42, 0.28, 0.18, 0.12];
        var colors = ["#2e7d32", "#f9a825", "#ef6c00", "#c62828"];
        var cx = width / 2;
        var cy = height / 2;
        var radius = Math.min(width, height) / 2 - 20;
        var startAngle = 0;
        for (var s = 0; s < slices.length; s++) {
            var sliceAngle = slices[s] * Math.PI * 2;
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.arc(cx, cy, radius, startAngle, startAngle + sliceAngle);
            ctx.closePath();
            ctx.fillStyle = colors[s];
            ctx.fill();
            startAngle += sliceAngle;
        }
    }

    canvas.dataset.rendered = "true";
}

// Simple helper used by the "after" (lazy-loaded) version of this
// dashboard: observes chart sections and only calls renderChart
// once a section actually scrolls into the viewport.
function setupLazyChart(sectionId, canvasId, type) {
    var section = document.getElementById(sectionId);
    if (!section) {
        return;
    }
    var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (entry.isIntersecting) {
                renderChart(canvasId, type);
                observer.unobserve(section);
            }
        });
    }, { rootMargin: "100px" });
    observer.observe(section);
}
