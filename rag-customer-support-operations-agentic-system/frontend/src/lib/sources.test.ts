import { test } from "node:test";
import assert from "node:assert/strict";
import { answerSources, policyDocuments, type Source } from "./sources";

const returnsReference: Source = {
  title: "Returns & exchanges", document: "tarnfield_returns_policy.pdf", kind: "referenced",
};

test("restored answers recognise the live Returns and Exchanges source wording", () => {
  const result = answerSources({
    content: "Your request is awaiting review.\nSource: Tarnfield Returns and Exchanges Policy — Section 1",
  });
  assert.deepEqual(result, [returnsReference]);
  assert.equal(result[0].excerpt, undefined);
});

test("recognises returns/catalogue aliases and excludes billing", () => {
  const examples = [
    ["Source: Returns & Exchanges Policy — Section 1", "tarnfield_returns_policy.pdf"],
    ["Source: Returns Policy — Section 1", "tarnfield_returns_policy.pdf"],
    ["Source: Tarnfield Product Catalogue — final-sale item", "tarnfield_product_catalogue.pdf"],
    ["Source: Tarnfield Product Catalog — final-sale item", "tarnfield_product_catalogue.pdf"],
  ];
  for (const [content, document] of examples) {
    const result = answerSources({ content });
    assert.equal(result.length, 1, content);
    assert.equal(result[0].document, document, content);
    assert.equal(result[0].kind, "referenced");
    assert.equal(result[0].excerpt, undefined);
  }
});

test("supports case-insensitive filenames and links only to canonical known documents", () => {
  for (const policy of policyDocuments) {
    const result = answerSources({ content: `Read \`${policy.document.toUpperCase()}\` for the complete policy.` });
    assert.deepEqual(result, [{ ...policy, kind: "referenced" }]);
  }
  assert.deepEqual(answerSources({ content: "Source: another_company_returns_policy.pdf — invented rule" }), []);
});

test("ordinary policy mentions are not presented as named citations", () => {
  assert.deepEqual(answerSources({
    content: "I can help with the Returns and Exchanges Policy, Billing & Payments Policy and Product Catalog.",
  }), []);
  assert.deepEqual(answerSources({
    content: "Source: Unknown document — a rule\n\nAsk about the Returns and Exchanges Policy if you need more help.",
  }), []);
});

test("handles Markdown source labels, plural lists and multiple policies without duplicates", () => {
  const content = "**Source:** Tarnfield Returns and Exchanges Policy — Section 1\n\n**Sources**:\n- Billing and Payments Policy\n- Product Catalog\n\nSource: Returns & Exchanges Policy — Section 2";
  assert.deepEqual(answerSources({ content }).map((item) => item.document), [
    "tarnfield_returns_policy.pdf", "tarnfield_product_catalogue.pdf",
  ]);
});

test("retains valid retrieved metadata and does not infer additional checked sources from prose", () => {
  const source: Source = {
    title: "Returns & exchanges policy", document: "tarnfield_returns_policy.pdf",
    excerpt: "Unworn shoes are eligible within 14 days.",
  };
  const sources = [source];
  assert.deepEqual(answerSources({ content: "Source: Billing Policy — Section 4", sources }), [source]);
  assert.deepEqual(sources, [source]);
});

test("filters unknown, null, malformed and invalid optional source metadata defensively", () => {
  const valid: Source = { title: "Product catalogue", document: "tarnfield_product_catalogue.pdf" };
  const sources = [
    null, undefined, false, "bad source", [], {},
    { title: "Other policy", document: "other_policy.pdf", excerpt: "Fabricated rule" },
    { title: "Wrong type", document: 42 },
    { title: 42, document: "tarnfield_returns_policy.pdf" },
    { title: "  ", document: "tarnfield_returns_policy.pdf" },
    { title: "Invalid excerpt", document: "tarnfield_returns_policy.pdf", excerpt: 42 },
    { title: "Invalid kind", document: "tarnfield_returns_policy.pdf", kind: "verified" },
    valid, valid,
  ] as unknown as Source[];
  assert.deepEqual(answerSources({ content: "", sources }), [valid]);
});

test("malformed metadata containers cannot hide or fabricate prose references", () => {
  for (const sources of [null, "invalid", {}, [null], [{ title: "Unknown", document: "unknown.pdf" }]]) {
    const result = answerSources({
      content: "Source: Tarnfield Returns and Exchanges Policy — Section 1",
      sources: sources as unknown as Source[],
    });
    assert.deepEqual(result, [returnsReference]);
    assert.equal(result[0].excerpt, undefined);
  }
});
