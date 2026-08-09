const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell,
  WidthType, ShadingType, AlignmentType, ImageRun,
} = require("docx");
const fs = require("fs");

const PAGE_WIDTH = 12240, PAGE_HEIGHT = 15840;

function h1(text) { return new Paragraph({ text, heading: HeadingLevel.HEADING_1, spacing: { before: 220, after: 100 } }); }
function p(text, opts = {}) {
  return new Paragraph({ children: [new TextRun({ text, size: 20, ...opts })], spacing: { after: 110 } });
}
function img(path, width, height) {
  return new Paragraph({
    children: [new ImageRun({ type: "png", data: fs.readFileSync(path), transformation: { width, height } })],
    alignment: AlignmentType.CENTER,
    spacing: { before: 100, after: 100 },
  });
}
function caption(text) {
  return new Paragraph({
    children: [new TextRun({ text, italics: true, size: 18 })],
    alignment: AlignmentType.CENTER,
    spacing: { after: 160 },
  });
}
function table(rows, colWidths) {
  const total = colWidths.reduce((a, b) => a + b, 0);
  const mkRow = (cells, isHeader) => new TableRow({
    children: cells.map((text, ci) => new TableCell({
      width: { size: colWidths[ci], type: WidthType.DXA },
      shading: isHeader ? { type: ShadingType.CLEAR, fill: "1F4E78" } : undefined,
      margins: { top: 60, bottom: 60, left: 90, right: 90 },
      children: [new Paragraph({ children: [new TextRun({ text, size: 18, bold: isHeader, color: isHeader ? "FFFFFF" : "000000" })] })],
    })),
  });
  return new Table({
    width: { size: total, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [mkRow(rows[0], true), ...rows.slice(1).map((r) => mkRow(r, false))],
  });
}

const doc = new Document({
  sections: [{
    properties: { page: { size: { width: PAGE_WIDTH, height: PAGE_HEIGHT }, margin: { top: 800, bottom: 800, left: 900, right: 900 } } },
    children: [
      new Paragraph({ children: [new TextRun({ text: "HealthTrack System", bold: true, size: 30 })], spacing: { after: 30 } }),
      new Paragraph({ children: [new TextRun({ text: "Benchmark Results", bold: true, size: 26, color: "1F4E78" })], spacing: { after: 60 } }),
      p("All numbers below are real, measured results (not estimates), produced by the scripts in benchmarks/ against a seeded dataset of 2,000 patients and 182,400 vitals readings. Each benchmark's raw JSON output is included alongside this document (baseline_results.json, optimized_results.json, cache_results.json, load_test_results.json, frontend_asset_results.json) so every figure here can be independently re-checked.", { italics: true, size: 19 }),

      h1("1. Sequential Latency: Before vs. After (TestClient, n=20 requests per endpoint)"),
      table([
        ["Endpoint", "Median before", "Median after", "P95 before", "P95 after", "SQL queries before → after"],
        ["List patients", "50.66 ms", "4.27 ms", "99.98 ms", "4.72 ms", "1 → 2"],
        ["Patient readings (heavy patient)", "53.05 ms", "4.47 ms", "100.37 ms", "4.93 ms", "1 → 2"],
        ["Dashboard summary", "22,352.19 ms", "121.06 ms", "23,758.76 ms", "171.18 ms", "2,001 → 2"],
        ["Risk report", "33.16 ms", "2.44 ms", "90.66 ms", "2.73 ms", "1 → 1"],
      ], [2800, 1500, 1500, 1400, 1400, 1840]),
      p(" "),
      img("charts/dashboard_latency_stages.png", 480, 308),
      caption("Figure 1. Dashboard summary latency across each optimization stage (log scale)."),

      h1("2. Isolating Each Optimization's Contribution (Dashboard Summary Endpoint)"),
      p("Because the dashboard endpoint had two independent problems (a missing index AND an N+1 query pattern), it was benchmarked in three stages to show what each fix actually contributed, rather than reporting one combined number that would hide which change mattered:"),
      table([
        ["Stage", "Median latency", "SQL queries", "What changed"],
        ["1. Baseline", "22,352 ms", "2,001", "No index, N+1 pattern (1 query per patient in a loop)"],
        ["2. Index added only", "2,018 ms", "2,001", "Same N+1 pattern, but each of the 2,001 queries now hits an index instead of a full scan"],
        ["3. Fully optimized", "121 ms", "2", "N+1 replaced with one GROUP BY aggregate query, on top of the index"],
      ], [2400, 1800, 1600, 3640]),
      p("Adding the index alone gave an ~11x improvement (22,352 → 2,018 ms) by making each of the 2,001 individual queries faster. Eliminating the N+1 pattern on top of that gave a further ~17x improvement (2,018 → 121 ms) by cutting the query count itself from 2,001 to 2. Combined, the two fixes together produced the full ~184x improvement -- neither fix alone would have gotten close to that result."),

      h1("3. Caching: Sequential vs. Concurrent Impact"),
      table([
        ["Scenario", "Result"],
        ["Single sequential request, cache MISS (mean)", "4.48 ms"],
        ["Single sequential request, cache HIT (mean)", "2.27 ms"],
        ["Sequential speedup from caching", "~2.0x"],
        ["Throughput @ 20 concurrent requests, no cache (v1)", "30.7 req/s"],
        ["Throughput @ 20 concurrent requests, cached (v2)", "238.2 req/s"],
        ["Throughput @ 100 concurrent requests, cached (v2)", "337.6 req/s"],
        ["Concurrent-load speedup from caching", "~7.8x (at 20 concurrent)"],
      ], [5400, 3040]),
      p("This is the most important nuance in this benchmark set: caching looks unimpressive (~2x) when tested with single, sequential requests, but becomes dramatically more valuable (~7.8x) under realistic concurrent load, because many simultaneous viewers of the same patient's dashboard can now share one cached computation instead of each triggering their own. A benchmark methodology that only tested sequential requests would have significantly understated caching's real-world value."),

      h1("4. High-Load / Scalability Test (Live Server, Concurrent Requests)"),
      p("Run against a real uvicorn server process (not the in-process TestClient), with CPU/memory sampled via psutil during each run:"),
      table([
        ["Test", "Concurrency", "Throughput", "Median latency", "Peak CPU", "Peak RSS"],
        ["v1 dashboard/summary (N+1)", "5", "0.45 req/s", "11,032.7 ms", "119.2%", "146.1 MB"],
        ["v2 dashboard/summary", "5", "7.77 req/s", "561.5 ms", "119.0%", "146.4 MB"],
        ["v2 dashboard/summary", "50", "7.59 req/s", "4,283.8 ms", "125.0%", "170.3 MB"],
        ["v1 risk-report (no cache)", "20", "30.67 req/s", "446.3 ms", "99.3%", "167.2 MB"],
        ["v2 risk-report (cached)", "20", "238.19 req/s", "50.1 ms", "69.2%", "167.2 MB"],
        ["v2 risk-report (cached)", "100", "337.61 req/s", "204.8 ms", "87.1%", "167.5 MB"],
      ], [2600, 1300, 1500, 1600, 1200, 1240]),
      p("Note the honest finding here: the optimized dashboard endpoint's throughput did NOT improve from 5 to 50 concurrent requests (7.77 → 7.59 req/s) -- latency simply grew instead. This shows the aggregation query itself (~121ms) is now the throughput ceiling for that endpoint on a single worker process, which query optimization alone cannot remove further. This is exactly the kind of finding the Recommendations document addresses (multi-worker deployment, connection pooling, read replicas)."),
      img("charts/throughput_load_test.png", 480, 267),
      caption("Figure 2. Measured throughput under concurrent load, before vs. after optimization."),

      h1("5. Frontend Asset Optimization"),
      table([
        ["Asset", "Raw (unminified)", "Minified", "Minified + gzip", "Total reduction"],
        ["dashboard.js", "4,120 B", "2,055 B (-50.1%)", "870 B", "-78.9%"],
        ["dashboard.css", "2,883 B", "1,339 B (-53.6%)", "636 B", "-77.9%"],
        ["Combined", "7,003 B", "3,394 B", "1,506 B", "-78.5%"],
      ], [2400, 2000, 2000, 1600, 1440]),
      img("charts/frontend_asset_sizes.png", 400, 300),
      caption("Figure 3. Frontend asset size: raw vs. minified + gzip-compressed."),
      p("In addition to the byte-size reduction, the dashboard's lazy-loading change means only 1 of 6 chart widgets (the one above the fold) executes its render routine on initial page load; the remaining 5 defer their rendering cost until the user actually scrolls to them, via IntersectionObserver. On a typical visit where a user does not scroll through the full dashboard, this removes real CPU work from the initial page load, not just bytes over the wire."),
    ],
  }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync("/home/claude/perf_lab/docs/Benchmark_Results.docx", buf);
  console.log("written");
});
