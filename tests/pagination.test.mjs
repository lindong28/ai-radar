import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { paginationPages, paginationSequence, paginationState } from "../web/static/app.js";

describe("pagination window model", () => {
  it("keeps first and last pages with a current +/-2 window and gaps", () => {
    assert.deepEqual(paginationPages(8, 42), [1, 6, 7, 8, 9, 10, 42]);
    assert.deepEqual(paginationSequence(8, 42), [1, "...", 6, 7, 8, 9, 10, "...", 42]);
  });

  it("omits the previous arrow on the first page", () => {
    const state = paginationState({ page: 1, total: 42, limit: 1 });

    assert.deepEqual(paginationPages(1, 42), [1, 2, 3, 42]);
    assert.equal(state.hidden, false);
    assert.equal(state.hasPrev, false);
    assert.equal(state.hasNext, true);
    assert.deepEqual(state.sequence, [1, 2, 3, "...", 42]);
  });

  it("omits the next arrow on the last page", () => {
    const state = paginationState({ page: 42, total: 42, limit: 1 });

    assert.deepEqual(paginationPages(42, 42), [1, 38, 39, 40, 41, 42]);
    assert.equal(state.hidden, false);
    assert.equal(state.hasPrev, true);
    assert.equal(state.hasNext, false);
    assert.deepEqual(state.sequence, [1, "...", 38, 39, 40, 41, 42]);
  });

  it("does not insert gaps when all pages are visible", () => {
    const state = paginationState({ page: 3, total: 5, limit: 1 });

    assert.deepEqual(paginationPages(3, 5), [1, 2, 3, 4, 5]);
    assert.deepEqual(state.sequence, [1, 2, 3, 4, 5]);
  });

  it("hides pagination controls for one or zero pages", () => {
    assert.deepEqual(paginationState({ page: 1, total: 1, limit: 50 }), {
      hidden: true,
      current: 1,
      totalPages: 1,
      hasPrev: false,
      hasNext: false,
      sequence: [],
    });
    assert.equal(paginationState({ page: 1, total: 0, limit: 50 }).hidden, true);
  });
});
