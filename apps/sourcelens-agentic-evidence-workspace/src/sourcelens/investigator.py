from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from .agent_roles import AgentTeam, EvidenceAssessment, InvestigationPlan
from .config import Settings
from .models import (
    Artifact,
    DataSource,
    Evidence,
    Finding,
    Investigation,
    InvestigationEvent,
    InvestigationStatus,
    utc_now,
)
from .query import QueryResult
from .store import AppStore


class Investigator:
    def __init__(self, *, settings: Settings, store: AppStore, warehouse, evidence_index):
        self.settings = settings
        self.store = store
        self.warehouse = warehouse
        self.evidence_index = evidence_index
        self.agent_team = AgentTeam(settings, store)

    def _event(
        self,
        investigation: Investigation,
        event_type: str,
        title: str,
        detail: str,
        payload: dict | None = None,
    ) -> None:
        investigation.events.append(
            InvestigationEvent(
                sequence=len(investigation.events) + 1,
                event_type=event_type,
                title=title,
                detail=detail,
                payload=payload or {},
            )
        )
        investigation.updated_at = utc_now()
        self.store.save_investigation(investigation)

    def _resolve_product(self, brief: str) -> tuple[str, str] | None:
        products = self.warehouse.run("SELECT product_id, product_name FROM products").rows
        normalized = re.sub(r"[^a-z0-9]", "", brief.lower())
        for product in products:
            keys = (product["product_id"], product["product_name"])
            if any(re.sub(r"[^a-z0-9]", "", key.lower()) in normalized for key in keys):
                return product["product_id"], product["product_name"]
        return None

    def _resolve_uploaded_source(self, brief: str) -> DataSource | None:
        sources = [
            source
            for source in self.store.list_sources()
            if source.source_id != "warehouse-primary" and source.kind.startswith("file")
        ]
        normalized = re.sub(r"[^a-z0-9]", "", brief.lower())
        for source in sources:
            name = re.sub(r"[^a-z0-9]", "", source.name.lower())
            stem = re.sub(r"[^a-z0-9]", "", source.name.rsplit(".", 1)[0].lower())
            if name in normalized or (len(stem) >= 4 and stem in normalized):
                return source
        return sources[0] if sources else None

    @staticmethod
    def _record_excerpt(record: dict) -> str:
        text = record.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()[:600]
        parts = [f"{key}: {value}" for key, value in record.items() if value not in (None, "")]
        return "; ".join(parts)[:600]

    @staticmethod
    def _profile_records(records: list[dict]) -> dict:
        columns = sorted({str(key) for record in records for key in record})
        missing = {
            column: sum(record.get(column) in (None, "") for record in records)
            for column in columns
        }
        numeric: dict[str, list[float]] = {}
        categories: dict[str, list[dict]] = {}
        for column in columns:
            values = [record.get(column) for record in records if record.get(column) not in (None, "")]
            parsed: list[float] = []
            for value in values:
                try:
                    parsed.append(float(Decimal(str(value).replace(",", "").strip())))
                except (InvalidOperation, ValueError):
                    parsed = []
                    break
            if parsed and len(parsed) >= max(2, len(values) * 0.8):
                numeric[column] = parsed
                continue
            counts = Counter(str(value).strip() for value in values)
            if 1 < len(counts) <= 20:
                categories[column] = [
                    {"value": value, "count": count} for value, count in counts.most_common(5)
                ]
        return {
            "record_count": len(records),
            "columns": columns,
            "missing": missing,
            "numeric": {
                column: {
                    "minimum": min(values),
                    "maximum": max(values),
                    "average": sum(values) / len(values),
                }
                for column, values in numeric.items()
            },
            "categories": categories,
        }

    @staticmethod
    def _query_artifact(query_id: str, title: str, result: QueryResult) -> Artifact:
        return Artifact(
            artifact_id=query_id,
            kind="query_result",
            title=title,
            data={"columns": result.columns, "rows": result.rows, "sql": result.sql},
            source_query_id=query_id,
        )

    async def run(self, investigation_id: str) -> None:
        investigation = self.store.get_investigation(investigation_id)
        if not investigation:
            return
        try:
            plan: InvestigationPlan | None = None
            assessment: EvidenceAssessment | None = None
            selected_source = investigation.scope.get("source_id")
            if selected_source and selected_source != "warehouse-primary":
                source = self.store.get_source(selected_source)
                if not source or source.status != "connected":
                    raise ValueError("Select an available connected source")
                await self._run_uploaded_source(investigation, source)
                return
            resolved_product = self._resolve_product(investigation.brief)
            if not resolved_product:
                uploaded_source = (
                    self._resolve_uploaded_source(investigation.brief) if not selected_source else None
                )
                if uploaded_source:
                    await self._run_uploaded_source(investigation, uploaded_source)
                    return
                resolved_product = ("NOVA-X300", "Nova X300")
            product_id, product_name = resolved_product
            investigation.title = f"{product_name}: performance investigation"
            investigation.scope = {
                "product_id": product_id,
                "product_name": product_name,
                "current_period": "2025-10-01 to 2025-12-31",
                "comparison_period": "2025-07-01 to 2025-09-30",
            }
            self._event(
                investigation,
                "scope",
                "Scope established",
                f"Matched {product_name} and selected comparable consecutive quarters.",
                investigation.scope,
            )
            if self.settings.sourcelens_live_agent and self.settings.openai_api_key:
                plan = await self.agent_team.plan(
                    investigation.investigation_id,
                    {
                        "brief": investigation.brief,
                        "scope": investigation.scope,
                        "sources": [source.model_dump() for source in self.store.list_sources()],
                        "available_analyses": [
                            "revenue decomposition",
                            "returns and quality signals",
                            "availability comparison",
                            "customer feedback themes",
                        ],
                    },
                )
                self._event(
                    investigation,
                    "agent",
                    "Research Planner set the direction",
                    plan.focus,
                    {"role": "Research Planner", "output": plan.model_dump()},
                )

            revenue_sql = f"""
                SELECT
                  CASE WHEN month >= '2025-10-01' THEN 'current' ELSE 'previous' END AS period,
                  ROUND(SUM(net_revenue), 2) AS net_revenue,
                  SUM(units) AS units,
                  ROUND(SUM(net_revenue) / SUM(units), 2) AS realized_price
                FROM sales_monthly
                WHERE product_id = '{product_id}' AND month BETWEEN '2025-07-01' AND '2025-12-31'
                GROUP BY period ORDER BY period
            """
            revenue = self.warehouse.run(revenue_sql)
            investigation.artifacts.append(self._query_artifact("q-revenue", "Revenue decomposition", revenue))
            self._event(
                investigation,
                "query",
                "Revenue decomposition complete",
                "Separated changes in units from changes in realised selling price.",
                {"query_id": "q-revenue", "sql": revenue.sql, "rows": revenue.rows},
            )

            operations_sql = f"""
                SELECT
                  CASE WHEN r.month >= '2025-10-01' THEN 'current' ELSE 'previous' END AS period,
                  ROUND(AVG(r.return_rate) * 100, 2) AS return_rate_pct,
                  SUM(r.quality_returns) AS quality_returns,
                  ROUND(AVG(i.availability_rate) * 100, 2) AS availability_pct,
                  SUM(i.stockout_days) AS stockout_days
                FROM returns_monthly r
                JOIN inventory_monthly i ON i.month = r.month AND i.product_id = r.product_id
                WHERE r.product_id = '{product_id}' AND r.month BETWEEN '2025-07-01' AND '2025-12-31'
                GROUP BY period ORDER BY period
            """
            operations = self.warehouse.run(operations_sql)
            investigation.artifacts.append(
                self._query_artifact("q-operations", "Returns and availability", operations)
            )
            self._event(
                investigation,
                "query",
                "Alternative explanations checked",
                "Compared quality returns and availability to distinguish product issues from stockouts.",
                {"query_id": "q-operations", "sql": operations.sql, "rows": operations.rows},
            )

            feedback_sql = f"""
                SELECT
                  CASE WHEN feedback_date >= '2025-10-01' THEN 'current' ELSE 'previous' END AS period,
                  theme, COUNT(*) AS review_count, ROUND(AVG(rating), 2) AS avg_rating
                FROM feedback
                WHERE product_id = '{product_id}' AND feedback_date BETWEEN '2025-07-01' AND '2025-12-31'
                GROUP BY period, theme ORDER BY period, review_count DESC
            """
            feedback_metrics = self.warehouse.run(feedback_sql)
            investigation.artifacts.append(
                self._query_artifact("q-feedback", "Feedback themes by quarter", feedback_metrics)
            )
            evidence = self.evidence_index.search(
                "audio cutting out disconnecting loose hinge battery problem",
                product_id=product_id,
                limit=8,
            )
            investigation.evidence = evidence
            self._event(
                investigation,
                "evidence",
                "Customer evidence inspected",
                f"Retrieved {len(evidence)} comments and retained exact source references.",
                {"evidence_ids": [item.evidence_id for item in evidence]},
            )

            current = next(row for row in revenue.rows if row["period"] == "current")
            previous = next(row for row in revenue.rows if row["period"] == "previous")
            current_ops = next(row for row in operations.rows if row["period"] == "current")
            previous_ops = next(row for row in operations.rows if row["period"] == "previous")
            revenue_change = (current["net_revenue"] / previous["net_revenue"] - 1) * 100
            units_change = (current["units"] / previous["units"] - 1) * 100
            price_change = (current["realized_price"] / previous["realized_price"] - 1) * 100
            evidence_ids = [item.evidence_id for item in evidence[:4]]

            investigation.findings = [
                Finding(
                    finding_id="finding-revenue",
                    title="Revenue decline is primarily volume-driven",
                    observation=(
                        f"Net revenue changed {revenue_change:.1f}% quarter over quarter while units "
                        f"changed {units_change:.1f}% and realised price changed {price_change:.1f}%."
                    ),
                    interpretation=(
                        "The decline is better explained by fewer units than by discounting. The data "
                        "shows association, so the next evidence determines the likely driver."
                    ),
                    next_step="Compare returns, availability and feedback for the same periods.",
                    evidence_ids=["q-revenue"],
                    confidence="high",
                ),
                Finding(
                    finding_id="finding-quality",
                    title="Quality deterioration is the strongest supported explanation",
                    observation=(
                        f"Average return rate increased from {previous_ops['return_rate_pct']:.2f}% to "
                        f"{current_ops['return_rate_pct']:.2f}%, with {current_ops['quality_returns']} "
                        "quality-coded returns in the current quarter."
                    ),
                    interpretation=(
                        "The return movement and customer reports of dropouts, hinges and battery "
                        "problems point in the same direction. They do not independently prove root cause."
                    ),
                    next_step="Inspect manufacturing batch or firmware-release data if available.",
                    evidence_ids=["q-operations", *evidence_ids],
                    confidence="high",
                ),
                Finding(
                    finding_id="finding-availability",
                    title="Stock availability is not the leading explanation",
                    observation=(
                        f"Current-quarter availability averaged {current_ops['availability_pct']:.1f}% "
                        f"with {current_ops['stockout_days']} recorded stockout days."
                    ),
                    interpretation="Availability remained comparatively stable while returns rose.",
                    next_step="Keep availability as a monitored alternative rather than the primary hypothesis.",
                    evidence_ids=["q-operations"],
                    confidence="medium",
                ),
            ]

            investigation.artifacts.extend(
                [
                    Artifact(
                        artifact_id="chart-revenue",
                        kind="metric_comparison",
                        title="Quarterly performance",
                        data={
                            "series": [
                                {
                                    "period": row["period"],
                                    "net_revenue": row["net_revenue"],
                                    "units": row["units"],
                                }
                                for row in revenue.rows
                            ]
                        },
                        source_query_id="q-revenue",
                    ),
                    Artifact(
                        artifact_id="chart-themes",
                        kind="theme_bars",
                        title="Customer feedback themes",
                        data={"series": feedback_metrics.rows},
                        source_query_id="q-feedback",
                    ),
                ]
            )

            if self.settings.sourcelens_live_agent and self.settings.openai_api_key:
                assessment = await self.agent_team.assess(
                    investigation.investigation_id,
                    {
                        "plan": plan.model_dump() if plan else None,
                        "findings": [finding.model_dump() for finding in investigation.findings],
                        "evidence": [item.model_dump() for item in investigation.evidence],
                    },
                )
                self._event(
                    investigation,
                    "agent",
                    "Evidence Analyst challenged the findings",
                    assessment.strongest_explanation,
                    {"role": "Evidence Analyst", "output": assessment.model_dump()},
                )

            summary = (
                f"{product_name}'s current-quarter revenue fell {abs(revenue_change):.1f}% versus the "
                f"previous quarter, driven mainly by a {abs(units_change):.1f}% fall in units rather "
                f"than realised price. Returns and customer reports both deteriorated, while availability "
                "was comparatively stable. The strongest supported hypothesis is a product-quality issue; "
                "batch or firmware data is needed before treating it as a confirmed root cause."
            )
            if self.settings.sourcelens_live_agent and self.settings.openai_api_key:
                narrative = await self.agent_team.synthesize(
                    investigation.investigation_id,
                    {
                        "brief": investigation.brief,
                        "scope": investigation.scope,
                        "plan": plan.model_dump() if plan else None,
                        "evidence_assessment": assessment.model_dump() if assessment else None,
                        "findings": [finding.model_dump() for finding in investigation.findings],
                        "evidence": [item.model_dump() for item in investigation.evidence],
                        "draft": summary,
                    },
                )
                summary = narrative.executive_summary
                investigation.limitations.extend(narrative.limitations)
                self._event(
                    investigation,
                    "agent",
                    "Lead Investigator prepared the brief",
                    narrative.recommended_next_step,
                    {"role": "Lead Investigator"},
                )

            investigation.executive_summary = summary
            investigation.limitations.extend(
                [
                    "Source coverage is limited to the connected product, sales, returns, inventory and feedback records.",
                    "Temporal alignment and correlated evidence do not prove causation.",
                    "Manufacturing batch and firmware release data are unavailable in the current source set.",
                ]
            )
            investigation.status = InvestigationStatus.READY
            self._event(
                investigation,
                "brief",
                "Evidence-backed brief ready",
                "Synthesised observations, alternatives, limitations and the recommended next test.",
            )
        except Exception as exc:
            investigation.status = InvestigationStatus.FAILED
            self._event(investigation, "error", "Investigation failed", str(exc))

    async def _run_uploaded_source(
        self, investigation: Investigation, source: DataSource
    ) -> None:
        records = self.store.source_preview(source.source_id, limit=500)
        if not records:
            raise ValueError(f"{source.name} does not contain readable records")
        profile = self._profile_records(records)
        investigation.title = f"{source.name}: source investigation"
        investigation.scope = {
            "source_id": source.source_id,
            "source_name": source.name,
            "records_profiled": len(records),
            "records_available": source.record_count,
        }
        self._event(
            investigation,
            "scope",
            "Source and scope established",
            f"Selected {source.name} and profiled {len(records):,} records.",
            investigation.scope,
        )

        plan: InvestigationPlan | None = None
        if self.settings.sourcelens_live_agent and self.settings.openai_api_key:
            plan = await self.agent_team.plan(
                investigation.investigation_id,
                {
                    "brief": investigation.brief,
                    "scope": investigation.scope,
                    "source": source.model_dump(),
                    "profile": profile,
                    "record_preview": records[:20],
                },
            )
            self._event(
                investigation,
                "agent",
                "Research Planner set the direction",
                plan.focus,
                {"role": "Research Planner", "output": plan.model_dump()},
            )

        evidence = [
            Evidence(
                evidence_id=f"{source.source_id}:record:{index}",
                kind="source_record",
                source_id=source.source_id,
                title=f"{source.name} · record {index}",
                excerpt=self._record_excerpt(record),
                metadata={"record_index": index, "source_sha256": source.metadata.get("sha256")},
            )
            for index, record in enumerate(records[:8], start=1)
        ]
        investigation.evidence = evidence
        investigation.artifacts.append(
            Artifact(
                artifact_id="source-profile",
                kind="source_profile",
                title="Source profile",
                data=profile,
            )
        )
        total_cells = max(1, len(records) * max(1, len(profile["columns"])))
        missing_cells = sum(profile["missing"].values())
        completeness = 100 * (1 - missing_cells / total_cells)
        findings = [
            Finding(
                finding_id="finding-coverage",
                title="Source coverage is ready for exploration",
                observation=(
                    f"The source contains {source.record_count:,} records across "
                    f"{len(profile['columns'])} fields; the profiled records are {completeness:.1f}% complete."
                ),
                interpretation=(
                    "This establishes the usable shape of the source. Conclusions remain bounded by "
                    "the fields and records present in this upload."
                ),
                next_step="Use the named fields to narrow the next business question.",
                evidence_ids=[item.evidence_id for item in evidence[:3]],
                confidence="high",
            )
        ]
        if profile["categories"]:
            field, values = next(iter(profile["categories"].items()))
            leaders = ", ".join(f"{item['value']} ({item['count']})" for item in values[:3])
            findings.append(
                Finding(
                    finding_id="finding-distribution",
                    title=f"{field.replace('_', ' ').title()} has a visible concentration",
                    observation=f"The most frequent values are {leaders}.",
                    interpretation="The concentration is descriptive and may identify a useful segment to compare.",
                    next_step=f"Compare outcomes across {field.replace('_', ' ')} values.",
                    evidence_ids=[item.evidence_id for item in evidence[:4]],
                    confidence="medium",
                )
            )
        if profile["numeric"]:
            field, values = next(iter(profile["numeric"].items()))
            findings.append(
                Finding(
                    finding_id="finding-range",
                    title=f"{field.replace('_', ' ').title()} establishes a measurable range",
                    observation=(
                        f"Values range from {values['minimum']:.2f} to {values['maximum']:.2f}, "
                        f"with an average of {values['average']:.2f}."
                    ),
                    interpretation="The range is descriptive; segment and time context are needed to explain variation.",
                    next_step=f"Break down {field.replace('_', ' ')} by a relevant category or period.",
                    evidence_ids=[item.evidence_id for item in evidence[:4]],
                    confidence="high",
                )
            )
        investigation.findings = findings
        self._event(
            investigation,
            "evidence",
            "Source records inspected",
            f"Profiled fields and retained {len(evidence)} cited record excerpts.",
            {"role": "Evidence Analyst", "evidence_ids": [item.evidence_id for item in evidence]},
        )

        assessment: EvidenceAssessment | None = None
        if self.settings.sourcelens_live_agent and self.settings.openai_api_key:
            assessment = await self.agent_team.assess(
                investigation.investigation_id,
                {
                    "plan": plan.model_dump() if plan else None,
                    "profile": profile,
                    "findings": [finding.model_dump() for finding in findings],
                    "evidence": [item.model_dump() for item in evidence],
                },
            )
            self._event(
                investigation,
                "agent",
                "Evidence Analyst challenged the findings",
                assessment.strongest_explanation,
                {"role": "Evidence Analyst", "output": assessment.model_dump()},
            )

        summary = (
            f"{source.name} contains {source.record_count:,} records across "
            f"{len(profile['columns'])} fields. The initial profile is {completeness:.1f}% complete "
            "and identifies the strongest visible distributions and numeric ranges. These are source-level "
            "patterns rather than causal conclusions; the next pass should apply the user's decision context "
            "to the most relevant fields."
        )
        if self.settings.sourcelens_live_agent and self.settings.openai_api_key:
            narrative = await self.agent_team.synthesize(
                investigation.investigation_id,
                {
                    "brief": investigation.brief,
                    "scope": investigation.scope,
                    "plan": plan.model_dump() if plan else None,
                    "evidence_assessment": assessment.model_dump() if assessment else None,
                    "findings": [finding.model_dump() for finding in findings],
                    "evidence": [item.model_dump() for item in evidence],
                    "draft": summary,
                },
            )
            summary = narrative.executive_summary
            investigation.limitations.extend(narrative.limitations)
            self._event(
                investigation,
                "agent",
                "Lead Investigator prepared the brief",
                narrative.recommended_next_step,
                {"role": "Lead Investigator"},
            )
        investigation.executive_summary = summary
        investigation.limitations.extend(
            [
                f"The first pass profiles at most 500 of {source.record_count:,} records.",
                "Field distributions describe this source and do not establish causation.",
            ]
        )
        investigation.status = InvestigationStatus.READY
        self._event(
            investigation,
            "brief",
            "Evidence-backed brief ready",
            "Prepared a source profile, cited observations and the next analytical direction.",
        )

    def prepare_refinement(self, investigation: Investigation, direction: str) -> Investigation:
        investigation.brief = f"{investigation.brief}\nFollow-up direction: {direction}"
        investigation.status = InvestigationStatus.RUNNING
        investigation.executive_summary = ""
        investigation.findings = []
        investigation.artifacts = []
        investigation.evidence = []
        investigation.limitations = []
        self._event(
            investigation,
            "direction",
            "User judgment incorporated",
            direction,
            {"direction": direction},
        )
        return investigation

def new_investigation(brief: str) -> Investigation:
    return Investigation(
        investigation_id=str(uuid4()),
        title="New investigation",
        brief=brief,
        status=InvestigationStatus.RUNNING,
    )
