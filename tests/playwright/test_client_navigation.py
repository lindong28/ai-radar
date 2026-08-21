"""Client-side switching between 精选 (`/`) and 全部 AI 动态 (`/all`).

These two pages are what a reader alternates between, and every switch used to
be a full document navigation. The swap keeps the document, so the risks are
not "does it render" -- they are the ones that only appear after a few round
trips, and never as an error:

* bindings from the outgoing page surviving and running twice;
* history entries that go somewhere other than where the reader came from;
* the sidebar claiming one page while the content shows another.

Each test below pins one of those.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

CURATED_TITLE = "AI Radar · 精选"
ALL_TITLE = "AI Radar · 全部 AI 动态"


def _sidebar_link(page: Page, href: str):
    return page.locator(f'.sidebar .side-link[href="{href}"]')


def _mark_document(page: Page) -> None:
    """Tag this document so a full reload becomes observable.

    Without this the tests cannot tell a fast reload from a real client-side
    swap -- both end with the right URL and the right content on screen, which
    is exactly why "it looks instant" is not evidence.
    """
    page.evaluate("window.__documentMark = 'original'")


def _document_survived(page: Page) -> bool:
    return page.evaluate("window.__documentMark === 'original'")


def test_switching_tabs_keeps_the_document(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    expect(page).to_have_title(CURATED_TITLE)
    _mark_document(page)

    _sidebar_link(page, "/all").click()

    expect(page).to_have_url(f"{base_url}/all")
    expect(page).to_have_title(ALL_TITLE)
    expect(page.locator("main h1")).to_have_text("全部 AI 动态")
    assert _document_survived(page), "the page was reloaded, not swapped in place"


def test_active_nav_follows_the_content(page: Page, base_url: str) -> None:
    """The sidebar is outside <main>, so nothing updates it unless we do.

    A stale highlight is the most likely visible defect of swapping only the
    content: the reader is on 全部 AI 动态 while 精选 still looks selected.
    """
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    expect(_sidebar_link(page, "/")).to_have_class(__import__("re").compile(r"side-link-active"))

    _sidebar_link(page, "/all").click()
    expect(page.locator("main h1")).to_have_text("全部 AI 动态")

    expect(_sidebar_link(page, "/all")).to_have_class(__import__("re").compile(r"side-link-active"))
    expect(_sidebar_link(page, "/")).not_to_have_class(__import__("re").compile(r"side-link-active"))


def test_back_returns_to_the_previous_tab(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    _mark_document(page)
    _sidebar_link(page, "/all").click()
    expect(page).to_have_url(f"{base_url}/all")

    page.go_back()

    expect(page).to_have_url(f"{base_url}/")
    expect(page).to_have_title(CURATED_TITLE)
    expect(page.locator("main h1")).to_contain_text("精选")
    expect(_sidebar_link(page, "/")).to_have_class(__import__("re").compile(r"side-link-active"))


def test_repeated_round_trips_do_not_accumulate_popstate_handlers(
    page: Page, base_url: str
) -> None:
    """The failure this guards against is silent and delayed.

    Each swap re-runs a page initializer. If those initializers' window-level
    listeners are not torn down with the page that registered them, the Nth
    back button press is handled N times -- the content still ends up correct,
    so nothing looks wrong until a handler has a side effect. Counting
    registrations is the only cheap way to see it.

    The instrument is installed before the document runs, so it counts what the
    page actually registers rather than what we believe it registers. The
    assertion is that the live count does not *grow* with round trips -- not
    that it equals some particular number. A fixed expectation would encode how
    many listeners today's initializers happen to want and would have to be
    edited every time that changed, which is how a guard quietly stops guarding.
    """
    page.add_init_script(
        """
        window.__pop = { adds: 0, removes: 0 };
        const add = window.addEventListener.bind(window);
        const remove = window.removeEventListener.bind(window);
        window.addEventListener = (type, handler, options) => {
          if (type === 'popstate') {
            window.__pop.adds += 1;
            // An AbortController signal detaches the listener without ever
            // calling removeEventListener, so aborts count as removals.
            options?.signal?.addEventListener('abort', () => { window.__pop.removes += 1; });
          }
          return add(type, handler, options);
        };
        window.removeEventListener = (type, handler, options) => {
          if (type === 'popstate') window.__pop.removes += 1;
          return remove(type, handler, options);
        };
        """
    )
    page.goto(f"{base_url}/", wait_until="domcontentloaded")

    def live() -> int:
        return page.evaluate("window.__pop.adds - window.__pop.removes")

    def round_trip() -> None:
        _sidebar_link(page, "/all").click()
        expect(page.locator("main h1")).to_have_text("全部 AI 动态")
        _sidebar_link(page, "/").click()
        expect(page.locator("main h1")).to_contain_text("精选")

    round_trip()
    after_one = live()
    for _ in range(2):
        round_trip()
    after_three = live()

    # The instrument has to have seen something, or both numbers would be zero
    # and the comparison below would hold vacuously.
    assert page.evaluate("window.__pop.adds") >= 6
    assert page.evaluate("window.__pop.removes") >= 4
    assert after_three == after_one, (
        f"popstate handlers grew from {after_one} to {after_three} over two more round trips"
    )


def test_switching_starts_the_new_page_at_the_top(page: Page, base_url: str) -> None:
    """A navigation lands at the top; the swap has to do that itself.

    `history.scrollRestoration` is set to "manual" so that going *back* can
    restore where the reader was. That same setting means the browser will not
    reset the offset going forward either, so without an explicit scroll the
    new page opens halfway down the previous one -- and the pushed history
    entry, which records scrollY 0, would then disagree with the screen.
    """
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.evaluate("window.scrollTo(0, 400)")
    assert page.evaluate("window.scrollY") > 0

    _sidebar_link(page, "/all").click()
    expect(page.locator("main h1")).to_have_text("全部 AI 动态")

    assert page.evaluate("window.scrollY") == 0


def test_back_to_a_filtered_url_does_not_strand_the_previous_content(
    page: Page, base_url: str
) -> None:
    """Going back to a URL this swap cannot render must still render it.

    The browser applies the history entry before dispatching popstate, so by
    the time the handler runs the address bar already shows the filtered URL.
    Declining to act there is the one thing that must not happen: the reader
    would be looking at 全部 AI 动态 under the address of a filtered 精选, with
    no error and no way back except another click.
    """
    page.goto(f"{base_url}/?category=ai-models", wait_until="domcontentloaded")
    expect(page.locator("main h1")).to_contain_text("精选")

    _sidebar_link(page, "/all").click()
    expect(page).to_have_url(f"{base_url}/all")
    expect(page.locator("main h1")).to_have_text("全部 AI 动态")

    page.go_back()
    page.wait_for_load_state("domcontentloaded")

    expect(page).to_have_url(f"{base_url}/?category=ai-models")
    # The assertion that matters: content followed the URL.
    expect(page.locator("main h1")).to_contain_text("精选")


def test_an_unusable_response_falls_back_to_a_real_navigation(
    page: Page, base_url: str
) -> None:
    """When the swap cannot be applied, the reader must still get the page.

    `preventDefault()` has already cancelled the browser's navigation by then,
    so declining silently would strand them on the old page with nothing having
    happened and no error to see.

    The failure is injected over the network rather than by patching a global.
    Two earlier attempts did the latter and broke the harness instead of the
    page: Playwright's own utility scripts call
    `Document.prototype.querySelectorAll` and construct `IntersectionObserver`,
    so patching either throws inside `page.evaluate` before the click happens.

    Scope note: this covers the "response cannot be applied" branch. The
    sibling branch -- `route.init()` itself throwing after the DOM and history
    are already swapped -- is covered behaviourally by
    `test_a_failing_initializer_reloads_instead_of_leaving_half_a_page` below.
    """
    served = {"count": 0}

    def broken_all_document(route):
        # Matched on URL alone, deliberately. The swap fetches `/all` with
        # `fetch()`, whose resource_type is "fetch", not "document" -- keying on
        # the type lets the prefetch through with good markup, the click then
        # serves from that cache, and the test passes while exercising nothing.
        if route.request.url.endswith("/all"):
            served["count"] += 1
            if served["count"] == 1:
                # Structurally valid HTML with no <main>: applyDocument cannot
                # use it, which is exactly the "cannot apply" branch.
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body="<!doctype html><html><body><p>no main here</p></body></html>",
                )
                return
        route.continue_()

    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.route("**/*", broken_all_document)
    _mark_document(page)

    _sidebar_link(page, "/all").click()
    page.wait_for_load_state("domcontentloaded")

    expect(page).to_have_url(f"{base_url}/all")
    # The real navigation re-requested the URL and got the good response.
    expect(page.locator("main h1")).to_have_text("全部 AI 动态")
    assert not _document_survived(page), (
        "the swap swallowed an unusable response instead of navigating"
    )


def test_breakpoint_listeners_do_not_accumulate_across_swaps(
    page: Page, base_url: str
) -> None:
    """`bindResponsiveTimeline` guards per container, and the container is
    inside <main>. Every swap gives it a fresh one, so it binds again -- while
    the listener sits on the MediaQueryList, which is document-scoped and
    outlives the swap holding the old container in its closure.
    """
    page.add_init_script(
        """
        window.__mq = { adds: 0, removes: 0 };
        const realMatchMedia = window.matchMedia.bind(window);
        window.matchMedia = (query) => {
          const mql = realMatchMedia(query);
          if (query !== '(max-width: 960px)') return mql;
          const add = mql.addEventListener?.bind(mql);
          const remove = mql.removeEventListener?.bind(mql);
          if (add) {
            mql.addEventListener = (type, handler, options) => {
              if (type === 'change') {
                window.__mq.adds += 1;
                options?.signal?.addEventListener('abort', () => { window.__mq.removes += 1; });
              }
              return add(type, handler, options);
            };
            mql.removeEventListener = (type, handler, options) => {
              if (type === 'change') window.__mq.removes += 1;
              return remove(type, handler, options);
            };
          }
          return mql;
        };
        """
    )
    page.goto(f"{base_url}/", wait_until="domcontentloaded")

    def live() -> int:
        return page.evaluate("window.__mq.adds - window.__mq.removes")

    def round_trip() -> None:
        _sidebar_link(page, "/all").click()
        expect(page.locator("main h1")).to_have_text("全部 AI 动态")
        _sidebar_link(page, "/").click()
        expect(page.locator("main h1")).to_contain_text("精选")

    round_trip()
    after_one = live()
    for _ in range(2):
        round_trip()

    assert page.evaluate("window.__mq.adds") >= 3, (
        "the breakpoint listener was never registered; this test is watching nothing"
    )
    assert live() == after_one, (
        f"breakpoint listeners grew from {after_one} to {live()} over two more round trips"
    )


def test_links_outside_the_pair_still_do_a_real_navigation(page: Page, base_url: str) -> None:
    """`/about` is not one of the two client routes, so it must load normally.

    Only sidebar links to `/` and `/all` are intercepted. Everything else --
    /hot, /wechat, /daily, an article -- has a different shell or a different
    initializer, and swapping <main> for it would leave the page half-wired.
    The marker disappearing is what proves the browser did the navigation.
    """
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    _mark_document(page)

    _sidebar_link(page, "/about").click()
    page.wait_for_load_state("domcontentloaded")

    expect(page).to_have_url(f"{base_url}/about")
    assert not _document_survived(page), "/about was swapped instead of navigating"


def test_category_filtering_still_belongs_to_the_page(page: Page, base_url: str) -> None:
    """The category chips were already client-side before this change.

    `bindCategoryControls` calls preventDefault and reloads the list in place,
    so these clicks never reach the navigation module -- it checks
    `event.defaultPrevented`, and its route test rejects any URL with a query
    string besides. Asserted as a non-regression: the risk of adding a
    document-level click handler is that it starts eating clicks that already
    had an owner.
    """
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    _mark_document(page)

    # Located by data-category, not href: app.js rewrites these hrefs at
    # runtime to carry the current search and channel, so the SSR value is not
    # what is in the DOM by the time this runs.
    page.locator('.seg-list a[data-category="model"]').click()

    expect(page).to_have_url(f"{base_url}/?category=ai-models")
    assert _document_survived(page), "the category chip stopped filtering in place"
    # Still on 精选, and the sidebar was not repainted to some other route.
    expect(page.locator("main h1")).to_contain_text("精选")
    expect(_sidebar_link(page, "/")).to_have_class(__import__("re").compile(r"side-link-active"))


def test_a_fragment_link_keeps_the_browsers_own_navigation(page: Page, base_url: str) -> None:
    """`/all#search` is the mobile search affordance on `/`.

    Its whole purpose is the fragment: the browser scrolls to and reveals the
    search field. Intercepting it would swallow that, and this module then
    moves focus to <main> -- so the reader taps "search" and lands somewhere
    else, with no error. Only the *bare* pair is ours.
    """
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    _mark_document(page)

    page.locator('a.mobile-search-link[href="/all#search"]').click()
    page.wait_for_load_state("domcontentloaded")

    assert page.evaluate("location.pathname") == "/all"
    assert page.evaluate("location.hash") == "#search"
    assert not _document_survived(page), "a fragment link was swapped instead of navigating"


def test_a_response_missing_the_page_contract_is_refused_before_the_swap(
    page: Page, base_url: str
) -> None:
    """Transport success is not page success.

    A gateway or CDN error page, or a template regression, can return 200 with
    a <main> and none of the elements the initializer needs. Checking after the
    swap is too late -- the DOM and history are already replaced and the only
    recovery is a reload, which fetches the same broken response again.
    """
    served = {"count": 0}

    def contract_breaking_all(route):
        if route.request.url.endswith("/all"):
            served["count"] += 1
            if served["count"] == 1:
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    # Has <main>; has no #__PRELOAD__ and no #list.
                    body="<!doctype html><html><body><main><h1>全部 AI 动态</h1></main></body></html>",
                )
                return
        route.continue_()

    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.route("**/*", contract_breaking_all)
    _mark_document(page)

    page.locator('.sidebar .side-link[href="/all"]').click()
    page.wait_for_load_state("domcontentloaded")

    expect(page).to_have_url(f"{base_url}/all")
    expect(page.locator("main h1")).to_have_text("全部 AI 动态")
    assert not _document_survived(page), (
        "markup missing the page contract was swapped in instead of being refused"
    )
    assert page.evaluate("document.querySelector('#__PRELOAD__') !== null"), (
        "the reader was left on the contract-breaking document"
    )


def test_a_superseded_navigation_does_not_poison_the_next_one(
    page: Page, base_url: str
) -> None:
    """Two quick activations must still end in a swap, not a reload.

    The cached fetch outlives the navigation that started it. Tying it to that
    navigation's AbortController means superseding the first click rejects the
    entry the second one reuses -- so the second reports failure and the
    browser does a full page load. The content still ends up right, which is
    why only the document marker catches it.
    """
    slow = {"seen": 0}

    def slow_all(route):
        if route.request.url.endswith("/all"):
            slow["seen"] += 1
            import time as _time

            _time.sleep(0.4)
        route.continue_()

    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    # The page's initializer is an `async` module, so it runs *after*
    # DOMContentLoaded. Dispatching before it is installed means the clicks
    # fall through to native navigation and the test measures nothing -- it
    # fails looking exactly like the bug. (That gap is real and harmless in
    # production: a click landing in it simply navigates.)
    page.wait_for_function("document.documentElement.dataset.clientNav === 'on'")
    page.route("**/*", slow_all)
    _mark_document(page)

    link = page.locator('.sidebar .side-link[href="/all"]')
    # Two activations while the first request is still in flight. dispatch_event
    # skips actionability waits, so the second really does land mid-flight --
    # `.click()` would serialise them and never reach the race.
    link.dispatch_event("click")
    link.dispatch_event("click")
    expect(page.locator("main h1")).to_have_text("全部 AI 动态")

    assert _document_survived(page), (
        "a superseded navigation degraded the next one into a full page load"
    )


def test_a_failing_initializer_reloads_instead_of_leaving_half_a_page(
    page: Page, base_url: str
) -> None:
    """By the time `route.init()` runs, the swap is already committed.

    <main> has been replaced and the history entry pushed, so there is no
    earlier state to return to -- a throw there leaves new markup with none of
    its behaviour wired to it, which looks like a working page until the reader
    touches it. Reloading is the only coherent outcome and is what the failed
    navigation would have produced.

    The failure is injected through a response that passes `applyDocument`'s
    contract check (it has <main>, `#__PRELOAD__` and `#list`) but omits
    `#search`, which the timeline initializer dereferences. An earlier attempt
    patched browser globals instead; `Document.prototype.querySelectorAll` and
    `IntersectionObserver` are both used by Playwright's own utility scripts,
    so that broke the harness rather than the page.
    """
    served = {"count": 0}
    good_body = {"html": ""}

    def strip_search_input(route):
        if route.request.url.endswith("/all"):
            served["count"] += 1
            if served["count"] == 1:
                response = route.fetch()
                body = response.text()
                good_body["html"] = body
                # Keep everything the contract check looks for; remove only the
                # element the initializer will dereference.
                broken = body.replace('id="search"', 'id="search-removed"')
                assert broken != body, "fixture no longer contains the search input"
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=broken)
                return
        route.continue_()

    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_function("document.documentElement.dataset.clientNav === 'on'")
    page.route("**/*", strip_search_input)
    _mark_document(page)

    _sidebar_link(page, "/all").click()
    page.wait_for_load_state("domcontentloaded")

    expect(page).to_have_url(f"{base_url}/all")
    expect(page.locator("main h1")).to_have_text("全部 AI 动态")
    assert not _document_survived(page), (
        "the initializer failed and the half-built document was left in place"
    )
    # The reload fetched the page again, so the reader ends on a fully wired one.
    assert page.evaluate("document.querySelector('#search') !== null")
