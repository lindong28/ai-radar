import assert from "node:assert/strict";
import { it } from "node:test";
import { renderDailyReport } from "../../web/static/app.js";

it("renders six explicit categories separately and recognizes opinion without the legacy boolean", () => {
  const container = { innerHTML: "" };
  const categories = ["model", "product", "industry", "paper", "tutorial", "opinion"];
  const items = categories.map((category) => ({
    id: category,
    title: `Article ${category}`,
    primary_category: category,
    is_opinion: false,
    topic_tags: ["模型发布"],
    summary_zh: "新闻摘要。",
    source_kind: "feed",
    source_name: "Source",
    url: `https://example.com/${category}`,
    published_at: "2026-09-20T00:00:00Z",
  }));
  renderDailyReport(container, items, "2026-09-20");
  for (const category of categories) {
    const section = category === "tutorial" ? "practice" : category;
    assert.ok(container.innerHTML.includes(`data-section="${section}"`));
  }
  const opinionSection = container.innerHTML.split('id="daily-section-opinion"')[1];
  assert.ok(opinionSection.includes('data-opinion="true"'));
  assert.ok(opinionSection.includes('>观点</span>'));
  assert.ok(opinionSection.includes("Article opinion"));
  assert.ok(!opinionSection.includes("Article tutorial"));
});
