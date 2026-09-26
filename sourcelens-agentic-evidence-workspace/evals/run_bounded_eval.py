from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sourcelens.config import Settings
from sourcelens.investigator import Investigator, new_investigation
from sourcelens.query import SQLiteWarehouse
from sourcelens.reference_data import generate
from sourcelens.retrieval import LocalEvidenceIndex
from sourcelens.store import AppStore

ROOT = Path(__file__).resolve().parents[1]


async def evaluate() -> dict:
    cases = json.loads((ROOT / "evals" / "cases.json").read_text())["cases"]
    results = []
    total_cost = 0.0
    for case in cases:
        case_dir = Path(tempfile.mkdtemp(prefix=f"sourcelens-{case['id']}-"))
        database = case_dir / "eval.db"
        generate(database, reset=True)
        settings = Settings(sourcelens_data_dir=case_dir, sourcelens_live_agent=True)
        store = AppStore(database)
        service = Investigator(
            settings=settings,
            store=store,
            warehouse=SQLiteWarehouse(database),
            evidence_index=LocalEvidenceIndex(database),
        )
        investigation = new_investigation(case["brief"])
        store.save_investigation(investigation)
        await service.run(investigation.investigation_id)
        completed = store.get_investigation(investigation.investigation_id)
        evidence_ids = {item.evidence_id for item in completed.evidence}
        material_refs = {
            evidence_id
            for finding in completed.findings
            for evidence_id in finding.evidence_ids
            if evidence_id.startswith("FB-")
        }
        checks = {
            "completed": completed.status == "ready",
            "quality_hypothesis": "quality" in completed.executive_summary.lower(),
            "causal_caveat": any(
                token in completed.executive_summary.lower()
                for token in (
                    "not yet",
                    "not confirmed",
                    "hypothesis",
                    "not causation",
                    "confirmed root cause",
                )
            ),
            "resolvable_feedback_citations": material_refs <= evidence_ids,
            "visible_tool_path": {"query", "evidence", "brief"}
            <= {event.event_type for event in completed.events},
            "bounded_findings": 1 <= len(completed.findings) <= 5,
        }
        cost = store.usage_cost()
        total_cost += cost
        results.append(
            {
                "case_id": case["id"],
                "passed": all(checks.values()),
                "checks": checks,
                "estimated_model_cost_usd": round(cost, 4),
                "summary": completed.executive_summary,
            }
        )
        print(f"{case['id']}: {'PASS' if all(checks.values()) else 'FAIL'} (${cost:.4f})")
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "model": Settings().sourcelens_complex_model,
        "cases": results,
        "passed": sum(result["passed"] for result in results),
        "total": len(results),
        "estimated_model_cost_usd": round(total_cost, 4),
    }
    output_dir = ROOT / "artifacts" / "evals"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "bounded-eval.json"
    output.write_text(json.dumps(report, indent=2))
    print(f"Summary: {report['passed']}/{report['total']} passed; ${total_cost:.4f}")
    print(f"Report: {output}")
    return report


def main() -> None:
    report = asyncio.run(evaluate())
    raise SystemExit(0 if report["passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()
