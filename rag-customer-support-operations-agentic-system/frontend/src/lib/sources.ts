export type Source = {
  title: string;
  document: string;
  excerpt?: string;
  passageId?: string;
  kind?: "referenced";
};

export const policyDocuments = [
  { title: "Returns & exchanges", document: "tarnfield_returns_policy.pdf" },
  { title: "Product catalogue", document: "tarnfield_product_catalogue.pdf" },
] as const;

const knownDocuments = new Set<string>(policyDocuments.map((policy) => policy.document));
const aliases: Record<string, RegExp> = {
  "tarnfield_returns_policy.pdf": /\breturns(?:\s*(?:&|and)\s*exchanges)?\s+policy\b/i,
  "tarnfield_product_catalogue.pdf": /\bproduct\s+catalog(?:ue)?\b/i,
};

function validatedSources(value: unknown): Source[] {
  if (!Array.isArray(value)) return [];
  const accepted = new Map<string, Source>();
  for (const item of value) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    if (typeof item.document !== "string" || !knownDocuments.has(item.document)) continue;
    if (typeof item.title !== "string" || !item.title.trim()) continue;
    if (item.excerpt !== undefined && typeof item.excerpt !== "string") continue;
    if (item.passageId !== undefined && typeof item.passageId !== "string") continue;
    if (item.kind !== undefined && item.kind !== "referenced") continue;
    const source: Source = { title: item.title, document: item.document };
    if (item.excerpt !== undefined) source.excerpt = item.excerpt;
    if (item.passageId !== undefined) source.passageId = item.passageId;
    if (item.kind === "referenced") source.kind = item.kind;
    if (!accepted.has(source.document)) accepted.set(source.document, source);
  }
  return [...accepted.values()];
}

function citationText(content: string): string {
  const cited: string[] = [];
  let sourceList = false;
  for (const line of content.split(/\r?\n/)) {
    const label = /^\s*(?:\*\*)?sources?(?:\*\*)?\s*:\s*(?:\*\*)?(.*)$/i.exec(line);
    if (label) {
      cited.push(label[1]);
      sourceList = !label[1].trim();
    } else if (sourceList) {
      if (!line.trim()) sourceList = false;
      else cited.push(line);
    }
  }
  return cited.join("\n");
}

export function answerSources(message: { content: string; sources?: Source[] }): Source[] {
  const metadata = validatedSources(message.sources);
  if (metadata.length) return metadata;
  const content = typeof message.content === "string" ? message.content : "";
  const cited = citationText(content);
  // Prose references link to an original policy; they never manufacture retrieved excerpts.
  return policyDocuments.filter((policy) =>
    content.toLowerCase().includes(policy.document) || aliases[policy.document].test(cited),
  ).map((policy) => ({ ...policy, kind: "referenced" }));
}
