"""
Risk Report Generator
------------------------
Assembles the outputs of scoring, classification, pattern recognition,
trend analysis, risk-factor identification, and recommendations into a
single patient risk report -- available as a JSON object (for API/system
integration) and rendered to an HTML report (for a clinician-facing view /
download).
"""

import datetime
import json
from string import Template

from engine.risk_scoring import score_reading
from engine.risk_classification import classify_dict
from engine.pattern_recognition import detect_multi_metric_pattern
from engine.risk_factors import identify_risk_factors
from engine.recommendations import generate_recommendations
from engine.trend_analysis import compute_trend


def build_patient_report(patient_id, patient_name, reading: dict,
                          history_scores: list = None, history_timestamps: list = None,
                          model_probability: float = None, baseline_row: dict = None) -> dict:
    """
    reading: latest vitals dict
    history_scores: list of past rule-based total scores (chronological, current excluded or included)
    history_timestamps: matching hours-elapsed / timestamps for history_scores
    model_probability: optional predictive-model probability of adverse event
    baseline_row: optional row with `<metric>_zscore`/`<metric>_anomaly` (see pattern_recognition)
    """
    scoring_result = score_reading(reading)
    metric_points = [ms.points for ms in scoring_result.metric_scores]

    level = classify_dict(scoring_result.total_score, metric_points, model_probability)

    trend = None
    if history_scores:
        all_scores = list(history_scores) + [scoring_result.total_score]
        if history_timestamps is not None and len(history_timestamps) != len(all_scores):
            raise ValueError(
                f"history_timestamps length ({len(history_timestamps)}) must equal "
                f"history_scores length + 1 for the current reading ({len(all_scores)})"
            )
        trend = compute_trend(all_scores, timestamps=history_timestamps)

    risk_factors = identify_risk_factors(reading, patient_history_row=baseline_row)
    recs = generate_recommendations(risk_factors, level["label"],
                                     trend_direction=trend["direction"] if trend else None)

    report = {
        "patient_id": patient_id,
        "patient_name": patient_name,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "latest_reading": reading,
        "risk_score": {
            "total_score": scoring_result.total_score,
            "max_possible": scoring_result.max_possible,
            "metric_breakdown": [ms.__dict__ for ms in scoring_result.metric_scores],
        },
        "risk_level": level,
        "predictive_model_probability_72h": round(model_probability, 4) if model_probability is not None else None,
        "trend": trend,
        "risk_factors": risk_factors,
        "recommendations": recs,
    }
    return report


HTML_TEMPLATE = Template("""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Risk Report - $patient_name</title>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0; padding: 24px; background: #f4f6f8; color: #1a1a1a; }
  .card { background: #fff; border-radius: 10px; padding: 20px 24px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .header { display: flex; justify-content: space-between; align-items: center; }
  h1 { font-size: 20px; margin: 0; }
  .level-badge { display: inline-block; padding: 6px 16px; border-radius: 999px; color: #fff; font-weight: 600; font-size: 14px; background: $color_hex; }
  .meta { color: #666; font-size: 13px; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; font-size: 14px; }
  th { color: #666; font-weight: 600; }
  .badge-pts { display:inline-block; min-width: 20px; text-align:center; padding:2px 6px; border-radius:4px; background:#eee; font-size:12px; }
  .factor { margin-bottom: 6px; font-size: 14px; }
  .disclaimer { font-size: 12px; color: #a05a00; background: #fff8e1; border: 1px solid #ffe0a0; border-radius: 6px; padding: 10px 12px; margin-bottom: 12px; }
  .rec { font-size: 14px; margin-bottom: 6px; padding-left: 10px; border-left: 3px solid #ccc; }
  .trend-worsening { color: #C62828; font-weight: 600; }
  .trend-improving { color: #2E7D32; font-weight: 600; }
  .trend-stable { color: #666; }
  .section-title { font-size: 15px; font-weight: 700; margin-bottom: 8px; color: #333; }
</style>
</head>
<body>

<div class="card header">
  <div>
    <h1>$patient_name</h1>
    <div class="meta">Patient ID: $patient_id &nbsp;|&nbsp; Generated: $generated_at</div>
  </div>
  <div class="level-badge">$level_label RISK</div>
</div>

<div class="card">
  <div class="section-title">Risk Score</div>
  <div class="meta">$total_score of $max_possible points &nbsp;|&nbsp; Urgency: $urgency</div>
  $model_prob_html
  <table>
    <tr><th>Metric</th><th>Value</th><th>Band</th><th>Points</th></tr>
    $metric_rows
  </table>
</div>

<div class="card">
  <div class="section-title">Risk Trend</div>
  $trend_html
</div>

<div class="card">
  <div class="section-title">Contributing Risk Factors</div>
  $factors_html
  $compound_html
  $baseline_html
</div>

<div class="card">
  <div class="disclaimer">$disclaimer</div>
  <div class="section-title">Recommendations</div>
  $recs_html
</div>

</body>
</html>
""")


def render_html_report(report: dict) -> str:
    metric_rows = "".join(
        f"<tr><td>{m['metric']}</td><td>{m['value']}</td><td>{m['band']}</td>"
        f"<td><span class='badge-pts'>{m['points']}</span></td></tr>"
        for m in report["risk_score"]["metric_breakdown"]
    )

    model_prob_html = ""
    if report["predictive_model_probability_72h"] is not None:
        pct = round(report["predictive_model_probability_72h"] * 100, 1)
        model_prob_html = f"<div class='meta'>Predictive model: {pct}% estimated probability of an adverse event within 72 hours</div>"

    trend = report["trend"]
    if trend and trend["direction"] != "insufficient_data":
        trend_html = (
            f"<div class='trend-{trend['direction']}'>{trend['direction'].upper()}</div>"
            f"<div class='meta'>Slope: {trend['slope']} pts/reading &nbsp;|&nbsp; "
            f"Latest: {trend['latest_score']} &nbsp;|&nbsp; Mean: {trend['mean_score']} "
            f"&nbsp;|&nbsp; Based on {trend['n_points']} readings</div>"
        )
    else:
        trend_html = "<div class='meta'>Insufficient history to compute a trend yet.</div>"

    factors = report["risk_factors"]["contributing_factors"]
    factors_html = "".join(f"<div class='factor'>&bull; {f['explanation']}</div>" for f in factors) or \
        "<div class='meta'>No individual vital-sign metric scored above 0.</div>"

    compound = report["risk_factors"]["compound_patterns"]
    compound_html = "".join(f"<div class='factor'><b>Pattern:</b> {c['explanation']}</div>" for c in compound)

    baseline = report["risk_factors"]["baseline_deviations"]
    baseline_html = "".join(f"<div class='factor'><b>Personal baseline:</b> {b['explanation']}</div>" for b in baseline)

    recs_html = "".join(f"<div class='rec'>{r['recommendation']} <span class='meta'>(basis: {r['basis']})</span></div>"
                         for r in report["recommendations"]["recommendations"])

    return HTML_TEMPLATE.substitute(
        patient_name=report["patient_name"],
        patient_id=report["patient_id"],
        generated_at=report["generated_at"],
        level_label=report["risk_level"]["label"],
        color_hex=report["risk_level"]["color_hex"],
        total_score=report["risk_score"]["total_score"],
        max_possible=report["risk_score"]["max_possible"],
        urgency=report["risk_level"]["urgency"],
        model_prob_html=model_prob_html,
        metric_rows=metric_rows,
        trend_html=trend_html,
        factors_html=factors_html,
        compound_html=compound_html,
        baseline_html=baseline_html,
        disclaimer=report["recommendations"]["disclaimer"],
        recs_html=recs_html,
    )


def report_to_json(report: dict) -> str:
    return json.dumps(report, indent=2, default=str)
