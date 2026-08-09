const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell,
  WidthType, ShadingType,
} = require("docx");
const fs = require("fs");

const PAGE_WIDTH = 12240, PAGE_HEIGHT = 15840;

function h1(text) { return new Paragraph({ text, heading: HeadingLevel.HEADING_1, spacing: { before: 200, after: 90 } }); }
function h2(text) { return new Paragraph({ text, heading: HeadingLevel.HEADING_2, spacing: { before: 160, after: 70 } }); }
function p(text, opts = {}) {
  return new Paragraph({ children: [new TextRun({ text, size: 20, ...opts })], spacing: { after: 100 } });
}
function bullet(text, opts = {}) {
  return new Paragraph({
    children: [new TextRun({ text, size: 20, ...opts })],
    bullet: { level: 0 },
    spacing: { after: 50 },
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
      new Paragraph({ children: [new TextRun({ text: "Recommendations for Future Improvements and Scaling", bold: true, size: 26, color: "1F4E78" })], spacing: { after: 160 } }),
      p("The optimizations implemented (indexing, N+1 elimination, pagination, in-memory caching, asset minification, lazy loading) are all code-level and configuration-level changes that required no new infrastructure. The items below were deliberately NOT implemented in this phase, either because they require infrastructure changes beyond this environment's scope, or because the current data volume does not yet justify their added operational complexity. Each is prioritized by expected impact vs. effort.", { italics: true, size: 19 }),

      h1("1. High Priority (address before the next major scale-up)"),

      h2("Move from an in-memory cache to a shared cache (Redis)"),
      p("The current TTLCache lives in one process's memory. The moment HealthTrack runs more than one API worker or process (which is the very next scaling step after this one), each process would have its own separate cache, meaning cache hit rates drop and, worse, different replicas could briefly serve different cached answers for the same patient. Redis (or a managed equivalent) makes the cache shared and consistent across every process, and is a well-understood, low-risk change since the cache access pattern here (get/set/TTL) maps directly onto Redis's basic commands."),

      h2("Migrate from SQLite to PostgreSQL for production"),
      p("SQLite was used in this evaluation for zero-setup portability, and it performed well once properly indexed. However, SQLite serializes writes at the file level and is not designed for many concurrent connections from multiple application processes -- both real constraints for a multi-patient, multi-provider monitoring system. PostgreSQL supports proper connection pooling, concurrent writers, and the same indexing strategy validated in this report carries over directly (the query patterns and index design do not need to change, only the connection string and driver)."),

      h2("Run multiple worker processes behind a process manager"),
      p("The load test showed the optimized dashboard endpoint plateau at ~7.6 requests/second regardless of concurrency (5 vs. 50 simultaneous requests), because a single uvicorn worker process serializes CPU-bound work. Running multiple worker processes (e.g., via gunicorn with uvicorn workers, sized to available CPU cores) would let independent requests execute in parallel instead of queueing behind one process -- this directly targets the scalability ceiling measured in Section 3 of the Benchmark Results document, and combined with the Redis migration above, is the most direct fix for that specific finding."),

      h1("2. Medium Priority (plan for, implement as usage grows)"),

      table([
        ["Recommendation", "Why"],
        ["Database connection pooling tuned for concurrent load", "Currently uses SQLAlchemy defaults; under multi-worker deployment, pool size should be explicitly sized to (workers x expected concurrent DB calls) to avoid connection exhaustion or excessive idle connections."],
        ["Read replicas for reporting/dashboard queries", "Aggregate queries like dashboard/summary are read-heavy and read-tolerant of slight staleness; routing them to a replica keeps that load off the primary database serving real-time vitals ingestion."],
        ["Background job queue for heavy computations", "The predictive-model component of the risk engine (not benchmarked here, since this phase focused on the rule-based scoring path) is more expensive than rule scoring; moving it off the request path into a queue (e.g., Celery/RQ) with the API returning the rule-based result immediately and the ML result shortly after would keep API latency predictable regardless of model cost."],
        ["Table partitioning or archiving strategy for vitals_readings", "This table grows without bound as more readings are ingested. Partitioning by time (e.g., monthly) or archiving readings older than a defined retention window keeps the \"hot\" table small and query performance stable as total data volume grows well past what was tested here."],
        ["CDN for static frontend assets", "The minified/gzip-compressed assets in this report were measured for byte size, but were not tested for network delivery; serving them from a CDN edge location reduces latency for geographically distributed users beyond what compression alone achieves."],
        ["APM / observability tooling in production", "This report's benchmarks were run deliberately, on demand. A production deployment needs continuous latency/error-rate/query-count monitoring (e.g., OpenTelemetry, Sentry, or a hosted APM) so a regression like the N+1 pattern found here is caught automatically, not only when someone happens to benchmark that endpoint again."],
      ], [3400, 6040]),

      h1("3. Lower Priority / Situational"),
      bullet("Async database driver (e.g., asyncpg with PostgreSQL): worth adopting once on Postgres, to let FastAPI's async request handling extend all the way to the database call instead of blocking a thread per request -- most valuable once request volume is high enough that thread-pool exhaustion, not query time, becomes the bottleneck."),
      bullet("HTTP/2 or HTTP/3 for the API and dashboard: reduces connection overhead for clients making many small requests (e.g., a dashboard polling several endpoints), but only matters once the per-request latency work in this report is already solved -- doing this first would have optimized the wrong layer."),
      bullet("Client-side data caching / stale-while-revalidate on the dashboard frontend: complements, but does not replace, the server-side caching implemented here; most useful once the dashboard is a full SPA rather than the demo page built for this evaluation."),
      bullet("Rate limiting per API client: not a performance optimization per se, but becomes important once the system scales to multiple provider organizations sharing the same infrastructure, to prevent one heavy user from degrading response times for others."),

      h1("4. What Should NOT Be Done Yet"),
      p("It is worth explicitly naming what this report does not recommend at the current scale: sharding the database, adopting a microservices split, or introducing a message broker for every write. All three are common \"future-proofing\" moves that add real operational complexity, and none of them were shown to be necessary by the benchmarks in this report -- the measured bottlenecks (N+1 queries, missing indexes, no caching, no pagination) were all fixable in application code. Introducing infrastructure complexity ahead of a demonstrated need is itself a scaling risk, not a scaling solution."),
    ],
  }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync("/home/claude/perf_lab/docs/Recommendations.docx", buf);
  console.log("written");
});
