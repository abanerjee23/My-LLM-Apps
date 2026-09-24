export type Event = {
  sequence: number; event_type: string; title: string; detail: string;
  status: string; payload: Record<string, unknown>; created_at: string;
};

export type Evidence = {
  evidence_id: string; kind: string; source_id: string; title: string;
  excerpt: string; metadata: Record<string, string | number>;
};

export type Finding = {
  finding_id: string; title: string; observation: string; interpretation: string;
  next_step: string; evidence_ids: string[]; confidence: string;
};

export type Artifact = {
  artifact_id: string; kind: string; title: string;
  data: {
    series?: Array<Record<string, string | number>>;
    rows?: Array<Record<string, string | number>>;
    sql?: string;
    record_count?: number;
    columns?: string[];
    missing?: Record<string, number>;
    numeric?: Record<string, { minimum: number; maximum: number; average: number }>;
    categories?: Record<string, Array<{ value: string; count: number }>>;
  };
  source_query_id?: string;
};

export type Investigation = {
  investigation_id: string; title: string; brief: string; status: "running" | "ready" | "failed";
  scope: Record<string, string>; executive_summary: string; findings: Finding[];
  artifacts: Artifact[]; evidence: Evidence[]; events: Event[]; limitations: string[];
  created_at: string; updated_at: string;
};

export type NotebookEntry = {
  entry_id: string; investigation_id: string; revision: number; title: string;
  saved_at: string; decision: "accepted" | "edited_accepted" | "rejected";
  summary: string; accuracy_rating?: number; usefulness_rating?: number;
  note?: string; brief_snapshot: Investigation;
};

export type DataSource = {
  source_id: string; name: string; kind: string;
  status: "connected" | "configured" | "processing" | "failed";
  record_count: number; created_at: string; metadata: Record<string, unknown>;
};
