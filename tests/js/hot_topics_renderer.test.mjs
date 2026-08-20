/* CSR 侧的判别力：这里真的**跑** renderHotTopics()。
 *
 * Python 那边的 SSR 测试只能证明服务端渲染对；两侧是否同形，此前只有一个
 * "class 名出没出现" 的检查，它在分档写对和写错两种情况下读数相同。首页又只
 * 渲染前 2 条，所以第 3 名与 rest 档在 Python 侧根本走不到。
 *
 * 由 tests/test_hot_topics_renderer.py 通过 `node --test` 拉起，因此它跟着
 * 常规 pytest 套件一起跑，而不是躺在仓里没人执行。
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { HOT_RETRY_DELAYS_MS, renderHotTopics } from "../../web/static/app.js";

function stubContainer() {
  return {
    innerHTML: "",
    hidden: false,
    removeAttribute() {},
  };
}

function stubFetch(items) {
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({ success: true, data: { items } }),
  });
}

function item(overrides = {}) {
  return {
    id: "i",
    title: "标题",
    url: "https://example.invalid/a",
    heat: 42,
    ...overrides,
  };
}

describe("renderHotTopics", () => {
  it("assigns rank 1/2/3 their own classes and everything after that 'rest'", async () => {
    stubFetch([
      item({ id: "a", title: "第一" }),
      item({ id: "b", title: "第二" }),
      item({ id: "c", title: "第三" }),
      item({ id: "d", title: "第四" }),
      item({ id: "e", title: "第五" }),
    ]);
    const container = stubContainer();

    await renderHotTopics(container);

    const ranks = [...container.innerHTML.matchAll(/hot-topics-rank hot-topics-rank-(\S+?)"/g)].map(
      (match) => match[1],
    );
    assert.deepEqual(ranks, ["1", "2", "3", "rest", "rest"]);
  });

  it("keeps the API order and numbers rows from one", async () => {
    stubFetch([item({ id: "a", title: "甲" }), item({ id: "b", title: "乙" })]);
    const container = stubContainer();

    await renderHotTopics(container);

    const titles = [...container.innerHTML.matchAll(/class="hot-topics-link"[^>]*>([^<]*)</g)].map(
      (match) => match[1],
    );
    assert.deepEqual(titles, ["甲", "乙"]);
    const numbers = [...container.innerHTML.matchAll(/aria-hidden="true">(\d+)</g)].map(
      (match) => match[1],
    );
    assert.deepEqual(numbers, ["1", "2"]);
  });

  it("escapes titles and urls instead of emitting raw markup", async () => {
    stubFetch([
      item({
        title: '<img src=x onerror="alert(1)">',
        url: 'https://example.invalid/"><script>alert(1)</script>',
      }),
    ]);
    const container = stubContainer();

    await renderHotTopics(container);

    assert.ok(!container.innerHTML.includes("<img src=x onerror"));
    assert.ok(!container.innerHTML.includes("<script>alert(1)</script>"));
    assert.ok(container.innerHTML.includes("&lt;img src=x"));
  });

  it("carries the same target and rel the SSR partial uses", async () => {
    stubFetch([item()]);
    const container = stubContainer();

    await renderHotTopics(container);

    assert.ok(container.innerHTML.includes('target="_blank"'));
    assert.ok(container.innerHTML.includes('rel="noopener noreferrer"'));
  });

  it("hides the box on a genuinely empty result rather than rendering an empty list", async () => {
    stubFetch([]);
    const container = stubContainer();

    await renderHotTopics(container);

    assert.equal(container.hidden, true);
    assert.equal(container.innerHTML, "");
  });

  it("schedules the next attempt and keeps the skeleton while still within budget", async () => {
    globalThis.fetch = async () => ({
      ok: false,
      status: 503,
      json: async () => ({ detail: "not ready" }),
    });
    const container = stubContainer();
    const scheduled = [];
    const realSetTimeout = globalThis.setTimeout;
    globalThis.setTimeout = (fn, delay) => {
      scheduled.push(delay);
      return 0; // 不真的排队：只记录这一轮排了什么
    };

    try {
      await renderHotTopics(container, 0);
    } finally {
      globalThis.setTimeout = realSetTimeout;
    }

    assert.deepEqual(scheduled, [HOT_RETRY_DELAYS_MS[0]], "预算内应排下一次重试");
    assert.equal(container.hidden, false, "预算内不得隐藏容器——骨架要留住，否则会跳变");
  });

  it("hides only once the retry budget is spent (503)", async () => {
    let calls = 0;
    globalThis.fetch = async () => {
      calls += 1;
      return { ok: false, status: 503, json: async () => ({ detail: "not ready" }) };
    };
    const container = stubContainer();

    // 从重试预算之外进入：重试链就此终止，既不排真实定时器、也不必猜一个
    // 会随预算调整而漂的数字。
    await renderHotTopics(container, HOT_RETRY_DELAYS_MS.length);

    assert.equal(calls, 1);
    assert.equal(container.hidden, true, "重试预算用尽后才隐藏");
  });
});
