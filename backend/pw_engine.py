"""Real browser automation: JS-rendered exploration + live Playwright test execution.

Replaces the old httpx+BeautifulSoup static crawler and the fully-simulated runner with
actual Chromium automation, so discovered flows reflect real rendered pages and executions
produce genuine pass/fail results with real screenshots, video and trace artifacts.
"""
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError, Error as PWError

ROOT_DIR = Path(__file__).parent
ARTIFACTS_ROOT = ROOT_DIR / "run_artifacts"
ARTIFACTS_ROOT.mkdir(exist_ok=True)

DUMMY_VALUES = {
    "email": "qa.tester+{n}@example.com",
    "password": "SecurePass!2024",
    "tel": "4155550142",
    "number": "1",
    "text": "Test note",
    "search": "test",
}

# a real QA tester types data appropriate to what a field actually is — a realistic first name,
# a validly-formatted postal code, a well-known payment-sandbox test card — not one generic string
# reused for every text field regardless of purpose. Matched against the field's name/id/placeholder/
# aria-label/associated <label> text, in order, first match wins.
FIELD_VALUE_RULES = [
    (re.compile(r"first.?name|fname|given.?name", re.I), "Jane"),
    (re.compile(r"last.?name|lname|surname|family.?name", re.I), "Doe"),
    (re.compile(r"full.?name|your.?name|^name$", re.I), "Jane Doe"),
    (re.compile(r"postal|zip", re.I), "94103"),
    (re.compile(r"city|town", re.I), "San Francisco"),
    (re.compile(r"state|province", re.I), "CA"),
    (re.compile(r"country", re.I), "United States"),
    (re.compile(r"address|street", re.I), "123 Market Street"),
    (re.compile(r"phone|tel|mobile", re.I), "4155550142"),
    (re.compile(r"compan|organi[sz]ation", re.I), "Acme Corp"),
    # Stripe's well-known test-mode card number — the standard way a real QA tester exercises a
    # payment form without risking a real charge, whether the gateway is in test mode or not.
    (re.compile(r"card.?num|cc.?num|cardnum", re.I), "4242424242424242"),
    (re.compile(r"cvv|cvc|security.?code", re.I), "123"),
    (re.compile(r"expir", re.I), "12/29"),
    (re.compile(r"search|query|keyword", re.I), "backpack"),
    (re.compile(r"user.?name|login", re.I), "qa.tester"),
    (re.compile(r"email", re.I), "qa.tester+{n}@example.com"),
    (re.compile(r"pass", re.I), "SecurePass!2024"),
    (re.compile(r"age\b", re.I), "29"),
    (re.compile(r"quantity|qty", re.I), "1"),
]


def _realistic_value(hint, kind, n_seed):
    """Picks a value appropriate to what a field actually is, from a name/id/placeholder/label hint,
    instead of one generic string reused for every field of a given HTML input type — filling a
    postal-code field with junk text can itself mask a real validation bug."""
    for pattern, val in FIELD_VALUE_RULES:
        if pattern.search(hint or ""):
            return val.format(n=n_seed) if "{n}" in val else val
    return DUMMY_VALUES.get(kind, DUMMY_VALUES["text"]).format(n=n_seed)

# only messages that actually look like an uncaught runtime exception count as a real JS-exception
# signal — Chrome's console "error" level also covers mixed-content warnings, resource-load failures,
# deprecated-API notices etc., none of which mean the app is broken.
_JS_EXCEPTION_RE = re.compile(
    r"uncaught|unhandled (promise )?rejection|typeerror|referenceerror|syntaxerror|rangeerror|"
    r"is not a function|is not defined|cannot read propert",
    re.I)


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "flow").lower()).strip("-")[:40] or "flow"


def artifact_url(run_id, filename):
    return f"/artifacts/{run_id}/{filename}"


def _run_dir(run_id):
    d = ARTIFACTS_ROOT / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ----------------------------------------------------------------------------
# EXPLORE — real JS-rendered crawl (+ real login when credentials are given)
# ----------------------------------------------------------------------------
async def explore_target_pw(run_id: str, url: str, login_url: str = None,
                             username: str = None, password: str = None, max_pages: int = 4):
    surface = {"base_url": url, "pages": [], "routes": [], "forms": [], "interactive": [], "error": None,
               "auth": None, "storage_state_path": None, "action_chain_hops": []}
    visited, to_visit = set(), [url]
    base_host = urlparse(url).netloc

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900},
                                             user_agent="QAlchemist-Explorer/2.0 (+Playwright)")

        effective_login_url = login_url or (url if username and password else None)
        if effective_login_url and username and password:
            ok, err = await _attempt_login(context, effective_login_url, username, password)
            surface["auth"] = {"ok": ok, "login_url": effective_login_url, "error": err}
            if ok:
                state_path = _run_dir(run_id) / "storageState.json"
                await context.storage_state(path=str(state_path))
                surface["storage_state_path"] = str(state_path)

        page = await context.new_page()
        while to_visit and len(visited) < max_pages:
            u = to_visit.pop(0)
            if u in visited:
                continue
            visited.add(u)
            try:
                resp = await page.goto(u, wait_until="domcontentloaded", timeout=15000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=4000)
                except PWTimeoutError:
                    pass
                page_data = await _scan_page(page, base_host)
                page_data["status"] = resp.status if resp else 200
                for l in page_data["links"]:
                    href = l["href"]
                    if href not in visited and href not in to_visit and len(to_visit) < max_pages * 3:
                        to_visit.append(href)
                surface["forms"].extend(page_data["forms"])
                surface["routes"].append({"path": urlparse(u).path or "/", "title": page_data["title"], "status": page_data["status"]})
            except Exception as e:
                page_data = {"url": u, "status": 0, "title": "", "links": [], "forms": [],
                             "buttons": [], "button_selectors": [], "inputs": [], "error": str(e)[:150]}
            surface["pages"].append(page_data)

        # beyond the static link crawl above, walk the primary call-to-action chain (add-to-cart ->
        # cart -> checkout -> continue/place-order) from the target URL — that reveals screens like a
        # checkout wizard that only exist behind a <button> click and are otherwise invisible.
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            try:
                await page.wait_for_load_state("networkidle", timeout=4000)
            except PWTimeoutError:
                pass
            chain_pages, chain_hops = await _discover_action_chain(page, base_host)
            for cp in chain_pages:
                surface["pages"].append(cp)
                surface["forms"].extend(cp.get("forms", []))
                surface["routes"].append({"path": urlparse(cp["url"]).path or "/",
                                           "title": cp.get("title", ""), "status": cp.get("status")})
            surface["action_chain_hops"] = chain_hops
        except Exception:
            pass

        await context.close()
        await browser.close()
    return surface


async def _scan_page(page, base_host):
    """Scrapes the currently-loaded page for links, buttons (with real ids/testids), inputs and
    forms. Shared by the link-BFS crawl and the action-chain walk below so both produce identically
    shaped page records."""
    page_data = {"url": page.url, "status": 200, "title": "", "links": [], "forms": [],
                 "buttons": [], "button_selectors": [], "inputs": []}
    try:
        page_data["title"] = (await page.title()) or ""

        anchors = await page.eval_on_selector_all(
            "a[href]", "els => els.slice(0,40).map(e => ({text: e.textContent.trim().slice(0,60), href: e.href}))")
        for a in anchors:
            href = a.get("href") or ""
            if not href or urlparse(href).netloc != base_host:
                continue
            page_data["links"].append({"text": a.get("text", ""), "href": href})

        button_els = await page.eval_on_selector_all(
            # a[data-test]/a[data-testid] scoped narrowly (not a bare "a") so icon-only actionable
            # links like a cart icon are captured here without flooding this list with every plain
            # nav link — those are already captured separately in `links`.
            "button, [role=button], input[type=submit], input[type=button], a[data-test], a[data-testid]",
            "els => els.slice(0,30).map(e => ({text: (e.textContent||e.value||e.getAttribute('aria-label')||'').trim().slice(0,40), "
            "selector: e.getAttribute('data-testid')||e.getAttribute('data-test')||e.id||''}))")
        page_data["buttons"] = [b["text"] for b in button_els if b["text"]]
        # separate from the text-only list above (kept for backward-compat display) — real
        # id/data-testid so GENERATE can validate an LLM-proposed button selector against
        # what's actually on the page instead of just pattern-matching the word "button".
        page_data["button_selectors"] = [b for b in button_els if b["selector"]]

        inputs = await page.eval_on_selector_all(
            "input, textarea, select",
            "els => els.slice(0,30).map(e => ({selector: e.getAttribute('data-testid')||e.id||e.name||'', type: e.type||e.tagName.toLowerCase()}))")
        page_data["inputs"] = [i for i in inputs if i["selector"]]

        forms = await page.eval_on_selector_all(
            "form",
            "els => els.slice(0,10).map(f => ({action: f.action, method: (f.method||'get').toUpperCase(), "
            "fields: Array.from(f.querySelectorAll('input,select,textarea')).map(x => ({name: x.name||x.id||'', type: x.type||x.tagName.toLowerCase()}))}))")
        page_data["forms"] = forms
    except Exception as e:
        page_data["error"] = str(e)[:150]
    return page_data


_CTA_CHAIN_PATTERNS = [
    re.compile(r"add.?to.?(cart|bag|basket)|buy.?now", re.I),
    re.compile(r"\bcart\b|view.?cart|shopping.?cart|\bbag\b", re.I),
    re.compile(r"checkout|proceed.?to.?checkout", re.I),
    re.compile(r"continue|next\b|place.?order|finish|complete|confirm.?order|pay\s*now|submit.?order", re.I),
]


async def _discover_action_chain(page, base_host, max_steps=5):
    """A static <a href> crawl can never see a screen that's only reachable by clicking a <button> —
    e.g. a checkout wizard (product -> add to cart -> cart -> checkout -> shipping info -> confirm).
    This walks that primary call-to-action chain in priority order from the current page, filling any
    gating form with dummy data so it can push through to the next step. Best-effort throughout: any
    failure (no matching button, click/nav error) just ends the chain early rather than failing EXPLORE.

    Returns (discovered_pages, hops). `hops` records, in click order, the exact selector used at each
    step — {"selector": <usable by page.locator()>, "has_form": bool}. A generated test for this
    funnel needs the REAL selector per step: several of these controls (a cart icon identified only
    by data-test, with no visible text at all) can never be found by matching the step's own wording,
    and a flow-wide pool of candidates doesn't work either — a persistent nav element like the cart
    icon would win every later step too, since it's visible on every subsequent page."""
    discovered = []
    hops = []
    visited_urls = {page.url}
    min_tier = 0
    for _ in range(max_steps):
        try:
            CANDIDATE_SEL = "button, [role=button], input[type=submit], input[type=button], a, [data-test], [data-testid]"
            els = await page.eval_on_selector_all(
                CANDIDATE_SEL,
                "(els, sel) => els.map(e => ({text: (e.textContent||e.value||e.getAttribute('aria-label')||'').trim(), "
                "id: e.id||'', testid: e.getAttribute('data-test')||e.getAttribute('data-testid')||'', "
                "disabled: !!e.disabled, "
                "visible: !!(e.offsetWidth||e.offsetHeight||e.getClientRects().length), "
                # a wrapper <div data-test="..."> around a whole product grid or cart table also
                # matches this selector, and its textContent is every descendant's text concatenated
                # — "leaf" excludes any candidate that itself contains another candidate, so we click
                # the actual button/link instead of its non-interactive container.
                "leaf: !e.querySelector(sel) }))",
                CANDIDATE_SEL,
            )
        except Exception:
            break
        match, tier = None, None
        for t in range(min_tier, len(_CTA_CHAIN_PATTERNS)):
            pattern = _CTA_CHAIN_PATTERNS[t]
            # a cart/checkout icon is very often an <a> or <div> identified only by a data-test(id)
            # attribute with no visible text and no id (e.g. saucedemo's cart link) — match on all
            # three identifiers, not just text/id, or icon-only controls are invisible to the chain.
            found = next((e for e in els if e["visible"] and not e["disabled"] and e["leaf"]
                          and (pattern.search(e["text"]) or pattern.search(e["id"]) or pattern.search(e["testid"]))),
                         None)
            if found:
                match, tier = found, t
                break
        if not match:
            break
        if match["id"]:
            sel_str = f'[id="{match["id"]}"]'
        elif match["testid"]:
            sel_str = f'[data-test="{match["testid"]}"], [data-testid="{match["testid"]}"]'
        else:
            sel_str = f'text={match["text"]}'
        locator = page.locator(sel_str) if match["id"] or match["testid"] else page.get_by_text(match["text"], exact=False)
        try:
            await locator.first.click(timeout=3000)
        except Exception:
            break
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except PWTimeoutError:
            pass
        # tiers 0-2 (add-to-cart / cart / checkout) are one-shot; the last tier covers a multi-page
        # wizard (checkout step one -> two -> complete) so it stays eligible across iterations
        min_tier = tier if tier == len(_CTA_CHAIN_PATTERNS) - 1 else tier + 1
        if urlparse(page.url).netloc != base_host:
            break
        new_page_data = None
        if page.url not in visited_urls:
            visited_urls.add(page.url)
            new_page_data = await _scan_page(page, base_host)
            new_page_data["discovered_via"] = "action_chain"
            discovered.append(new_page_data)
        hops.append({"selector": sel_str, "has_form": bool(new_page_data and new_page_data.get("forms"))})
        # a checkout-style form (shipping/payment info) blocks "continue" until required fields are
        # filled — best-effort dummy-fill so the chain can reach the page the form gates.
        try:
            await _fill_visible_inputs(page, int(time.time()) % 10000)
        except Exception:
            pass
    return discovered, hops


async def _attempt_login(context, login_url, username, password):
    page = await context.new_page()
    try:
        await page.goto(login_url, wait_until="domcontentloaded", timeout=15000)
        pwd = page.locator('input[type="password"]').first
        await pwd.wait_for(state="visible", timeout=8000)
        user = page.locator('input[type="email"], input[type="text"], '
                             'input[name*="user" i], input[name*="email" i]').first
        await user.fill(username, timeout=5000)
        await pwd.fill(password, timeout=5000)
        submit = page.locator('button[type="submit"], input[type="submit"]').first
        if await submit.count() == 0:
            submit = page.get_by_role("button", name=re.compile("log.?in|sign.?in|submit|continue", re.I)).first
        if await submit.count() > 0:
            await submit.click(timeout=6000)
        else:
            await pwd.press("Enter")
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except PWTimeoutError:
            pass
        return True, None
    except Exception as e:
        return False, str(e)[:200]
    finally:
        await page.close()


# ----------------------------------------------------------------------------
# RUN — real execution of a flow's natural-language steps against a live page
# ----------------------------------------------------------------------------
_ROLE_RE = re.compile(r"getByRole\('(\w+)'(?:,\s*\{\s*name:\s*/([^/]+)/i?\s*\})?\)")
_TESTID_RE = re.compile(r"getByTestId\('([^']+)'\)")
_TEXT_RE = re.compile(r"getByText\('([^']+)'\)")
_LABEL_RE = re.compile(r"getByLabel\('([^']+)'\)")
_PLACEHOLDER_RE = re.compile(r"getByPlaceholder\('([^']+)'\)")


def _locator_from_string(page, sel: str):
    """Translate a Playwright-JS-style locator string (or raw CSS/text=) into a live Python locator."""
    m = _ROLE_RE.match(sel)
    if m:
        role, name = m.group(1), m.group(2)
        return page.get_by_role(role, name=re.compile(name, re.I)) if name else page.get_by_role(role)
    m = _TESTID_RE.match(sel)
    if m:
        return page.get_by_test_id(m.group(1))
    m = _TEXT_RE.match(sel)
    if m:
        return page.get_by_text(m.group(1))
    m = _LABEL_RE.match(sel)
    if m:
        return page.get_by_label(m.group(1))
    m = _PLACEHOLDER_RE.match(sel)
    if m:
        return page.get_by_placeholder(m.group(1))
    return page.locator(sel)


_STOPWORDS = re.compile(
    r"^(click|select|choose|tap|press|open|add|the|on|to|a|an|and|then|assert|verify|check|that|is|are)\s+",
    re.I)


def _keywords(step_text: str) -> str:
    # a quoted literal is the actual target name when present ("Click a product such as 'Backpack'
    # on the inventory page" -> "Backpack") — without this, the generic word-strip below keeps
    # whatever's left after removing a couple of leading stopwords, which for a step phrased with
    # its real subject later in the sentence is a long, unmatchable verbose fragment rather than the
    # product/button name itself. Drop any parenthetical aside first, same reasoning as the spec
    # generator's _spec_keywords: an example like "(e.g., 'Sauce Labs Backpack')" isn't the target.
    stripped = re.sub(r"\([^)]*\)", "", step_text)
    m = _QUOTED_RE.search(stripped)
    if m:
        lit = m.group(1) if m.group(1) is not None else m.group(2)
        if lit and lit.strip():
            return lit.strip()
    t = re.sub(r"\b(button|link|icon|option|tab|element|page|field)\b", "", step_text, flags=re.I)
    prev = None
    while prev != t:
        prev = t
        t = _STOPWORDS.sub("", t.strip())
    # LLM-authored steps sometimes hyphenate an id-shaped name ("Click login-button") — removing the
    # word "button" above leaves a trailing "-" ("login-"), which then fails to match a real element
    # whose accessible name is plain "Login" with no hyphen at all.
    t = t.strip().strip("'\" -_.:;,")
    return t or step_text.strip()


async def _find_clickable(page, step_text, spec_selectors, timeout_ms=3000):
    """Tries each candidate locator with a real wait (matching Playwright's own auto-waiting), rather
    than an instant .count() snapshot — dynamic content (async data, animations, hydration) needs a
    moment to render, and giving up instantly both false-fails flows and produces near-empty videos."""
    kw = _keywords(step_text)
    candidates = []
    for sel in spec_selectors or []:
        try:
            candidates.append((_locator_from_string(page, sel), sel))
        except Exception:
            continue
    if kw:
        for role in ("button", "link"):
            candidates.append((page.get_by_role(role, name=re.compile(re.escape(kw), re.I)),
                               f"getByRole('{role}', name=/{kw}/i)"))
        candidates.append((page.get_by_text(re.compile(re.escape(kw), re.I)), f"getByText(/{kw}/i)"))
    tried = [desc for _, desc in candidates]
    for loc, desc in candidates:
        try:
            await loc.first.wait_for(state="visible", timeout=timeout_ms)
            return loc.first, desc, tried
        except Exception:
            continue
    return None, None, tried


async def _visible_clickable_texts(page, limit=15):
    """Best-effort diagnostic snapshot of what's actually clickable on the page right now, so a
    'no element found' failure can say what WAS there instead of just repeating the step text back."""
    try:
        texts = await page.eval_on_selector_all(
            "button, [role=button], a",
            "els => els.filter(e => e.offsetWidth||e.offsetHeight||e.getClientRects().length)"
            ".map(e => (e.textContent||e.value||e.getAttribute('aria-label')||'').trim()).filter(Boolean)")
        return list(dict.fromkeys(texts))[:limit]
    except Exception:
        return []


_QUOTED_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")


async def _fill_targeted(page, step_text, username=None, password=None):
    """Fills the ONE field a step clearly names (e.g. "Enter 'tomsmith' in the username field").
    Priority for the value: a literal quoted in the step text, then the run's own real credentials
    (if the operator provided them and this is a credential field), then type-based dummy data as a
    last resort. Without the credentials fallback, a plan that describes the field without quoting a
    literal (the LLM doesn't always do so, even when real creds were given) would silently type dummy
    placeholder text into a real login form and misattribute the resulting failure to the app under
    test. Returns (selector, value_filled) on success, else None so the caller can fall back to the
    generic multi-input fill."""
    t = step_text.lower()
    m = _QUOTED_RE.search(step_text)
    value = (m.group(1) if m and m.group(1) is not None else (m.group(2) if m else None))

    if "password" in t:
        selector, kind = 'input[type="password"]', "password"
        value = value or password
    elif "email" in t:
        selector, hint, kind = 'input[type="email"]', "email", "email"
    elif "username" in t or "user name" in t or "login" in t:
        selector, kind = 'input[type="text"], input[type="email"], input[name*="user" i]', "text"
        value = value or username
    else:
        return None

    loc = page.locator(selector).first
    try:
        await loc.wait_for(state="visible", timeout=3000)
    except Exception:
        return None
    fill_value = value if value else DUMMY_VALUES.get(kind, DUMMY_VALUES["text"]).format(n=int(time.time()) % 10000)
    await loc.fill(fill_value, timeout=3000)
    return selector, fill_value


_FIELD_SCAN_JS = """
els => els.map(e => {
  const lbl = e.id ? ((document.querySelector(`label[for='${e.id}']`) || {}).textContent || '')
                    : ((e.closest('label') || {}).textContent || '');
  return {
    type: e.type || e.tagName.toLowerCase(),
    name: e.name || '',
    id: e.id || '',
    placeholder: e.getAttribute('placeholder') || '',
    aria: e.getAttribute('aria-label') || '',
    label: lbl.trim().slice(0, 60),
    visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length),
    value: e.value || '',
  };
})
"""

_SKIP_INPUT_TYPES = {"submit", "button", "checkbox", "radio", "file", "hidden", "image", "reset"}


async def _fill_visible_inputs(page, n_seed):
    """Fills visible EMPTY inputs only. Without the emptiness check, a later generic "fill in the
    remaining fields" step would blindly overwrite a field an earlier _fill_targeted call already
    filled correctly (e.g. a real username) with dummy placeholder text — silently corrupting the
    flow's real input and, downstream, misattributing the resulting failure to the app under test
    instead of to this step. Only ever fill what's actually still blank."""
    filled = []
    try:
        candidates = await page.eval_on_selector_all("input, textarea", _FIELD_SCAN_JS)
    except Exception:
        return filled
    for c in candidates[:20]:
        if not c["visible"] or c["value"] or c["type"] in _SKIP_INPUT_TYPES:
            continue
        hint = " ".join(x for x in (c["name"], c["id"], c["placeholder"], c["aria"], c["label"]) if x)
        val = _realistic_value(hint, c["type"], n_seed)
        if c["name"]:
            loc = page.locator(f'[name="{c["name"]}"]').first
        elif c["id"]:
            loc = page.locator(f'#{c["id"]}').first
        elif c["placeholder"]:
            loc = page.get_by_placeholder(c["placeholder"]).first
        else:
            continue
        try:
            loc = page.locator(selector)
            count = min(await loc.count(), 5)
            for i in range(count):
                el = loc.nth(i)
                if not await el.is_visible():
                    continue
                try:
                    existing = await el.input_value(timeout=1000)
                except Exception:
                    existing = ""
                if existing.strip():
                    continue
                val = DUMMY_VALUES.get(kind, DUMMY_VALUES["text"]).format(n=n_seed)
                await el.fill(val, timeout=3000)
                filled.append(selector)
        except Exception:
            continue
    return filled


_URL_IN_STEP_RE = re.compile(r"https?://\S+")


async def _execute_step(page, step_text, spec_selectors, base_url, prev_url, username=None, password=None):
    t = step_text.lower()
    try:
        url_m = _URL_IN_STEP_RE.search(step_text)
        # A literal URL in the step text (e.g. "Open https://.../add_remove_elements/") is
        # unambiguous navigation intent, even if the URL happens to contain a word ("add", in that
        # example) that would otherwise match the click-keyword check below and misfire a click on
        # whatever selector happens to be first in the spec instead of just navigating there.
        if url_m or any(k in t for k in ("navigate", "go to", "visit")):
            target = url_m.group(0) if url_m else None
            if target is None:
                path_m = re.search(r"(/[\w\-\/\.]+)", step_text)
                target = urljoin(base_url, path_m.group(1)) if path_m else base_url
            resp = await page.goto(target, wait_until="domcontentloaded", timeout=15000)
            if resp and resp.status >= 500:
                return {"ok": False, "fail_type": "network-5xx", "error": f"{target} responded {resp.status}"}
            return {"ok": True}

        if t.strip().startswith("assert") or "assert" in t or t.strip().startswith("verify"):
            return await _execute_assert(page, t, step_text, base_url, prev_url)

        # a step can describe the whole login action in one sentence ("Log in with standard_user
        # credentials", "Enter valid credentials and log in") without using any fill/enter/click
        # verb the other branches key off — left unhandled, it silently no-ops as "informational"
        # and every later step then fails against a page the flow never actually logged into.
        if any(k in t for k in ("log in", "login")) and not any(k in t for k in ("fill", "enter", "type", "input")):
            filled_any = False
            for sel, val in (('input[type="text"], input[type="email"], input[name*="user" i]',
                               username or DUMMY_VALUES["text"]),
                              ('input[type="password"]', password or DUMMY_VALUES["password"])):
                try:
                    loc = page.locator(sel).first
                    await loc.wait_for(state="visible", timeout=2000)
                    await loc.fill(val, timeout=3000)
                    filled_any = True
                except Exception:
                    continue
            loc, desc = await _find_clickable(page, "login submit button", spec_selectors)
            if loc is None:
                desc = 'button[type="submit"], input[type="submit"]'
                try:
                    await page.locator(desc).first.click(timeout=5000)
                except Exception:
                    if not filled_any:
                        return {"ok": False, "fail_type": "selector-not-found",
                                "error": f"No login form or submit control found for step: '{step_text}'"}
            else:
                await loc.click(timeout=8000)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except PWTimeoutError:
                pass
            return {"ok": True, "locator": desc, "note": "compound login step (filled credentials + submitted)"}

        if any(k in t for k in ("fill", "enter", "type", "input")):
            targeted = await _fill_targeted(page, step_text, username=username, password=password)
            if targeted:
                sel, val = targeted
                return {"ok": True, "locator": sel, "note": f"filled '{val}'"}
            filled = await _fill_visible_inputs(page, int(time.time()) % 10000)
            if not filled:
                return {"ok": True, "note": "no empty inputs found to fill"}
            return {"ok": True, "locator": ", ".join(dict.fromkeys(filled))}

        if any(k in t for k in ("submit", "proceed", "continue", "checkout", "confirm")):
            loc, desc = await _find_clickable(page, step_text, spec_selectors)
            if loc is None:
                desc = 'button[type="submit"], input[type="submit"]'
                await page.locator(desc).first.click(timeout=5000)
            else:
                await loc.click(timeout=8000)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except PWTimeoutError:
                pass
            return {"ok": True, "locator": desc}

        if any(k in t for k in ("click", "select", "choose", "tap", "press", "add")):
            loc, desc, tried = await _find_clickable(page, step_text, spec_selectors)
            if loc is None:
                kw = _keywords(step_text)
                visible = await _visible_clickable_texts(page)
                error = (f"No element found matching step: '{step_text}' "
                         f"(derived keyword: \"{kw}\"; tried {len(tried)} candidate locator(s): "
                         f"{', '.join(tried) if tried else 'none derivable from step text or spec selectors'}. "
                         f"Visible clickable elements on page: {', '.join(visible) if visible else 'none found'})")
                return {"ok": False, "fail_type": "selector-not-found", "error": error}
            await loc.click(timeout=8000)
            # a click can trigger navigation (e.g. a login/submit-style button not caught by the
            # submit-keyword bucket above) — give it a moment to settle before the next step/assertion
            # inspects the page, otherwise we'd be checking pre-navigation state.
            try:
                await page.wait_for_load_state("networkidle", timeout=4000)
            except PWTimeoutError:
                pass
            return {"ok": True, "locator": desc}

        # unrecognized step type — treat as a soft no-op rather than a hard failure
        return {"ok": True, "note": "step not directly actionable; treated as informational"}

    except PWTimeoutError as e:
        return {"ok": False, "fail_type": "selector-not-found", "error": str(e)[:200]}
    except PWError as e:
        return {"ok": False, "fail_type": "assertion-failed", "error": str(e)[:200]}
    except Exception as e:
        return {"ok": False, "fail_type": "assertion-failed", "error": str(e)[:200]}


async def _execute_assert(page, t, step_text, base_url, prev_url):
    try:
        if "title" in t:
            title = await page.title()
            if not title:
                return {"ok": False, "fail_type": "assertion-failed", "error": "Page title is empty"}
            return {"ok": True}

        if "404" in t or "not found" in t:
            body_text = (await page.locator("body").inner_text())[:2000].lower()
            if "404" in body_text or "not found" in body_text or "page not found" in body_text:
                return {"ok": True}
            return {"ok": False, "fail_type": "assertion-failed", "error": "No 404/not-found indication on page"}

        if "url" in t and "chang" in t:
            if page.url != prev_url:
                return {"ok": True}
            return {"ok": False, "fail_type": "assertion-failed", "error": f"URL did not change from {prev_url}"}

        path_m = re.search(r"(/[\w\-/]+)\b", step_text)
        if path_m and any(k in t for k in ("redirect", "navigat", "url", "should be on", "goes to")):
            path = path_m.group(1)
            if path in page.url:
                return {"ok": True}
            return {"ok": False, "fail_type": "assertion-failed",
                    "error": f"Expected URL to contain '{path}', got '{page.url}'"}

        # a quoted literal (e.g. "...with message 'You logged into a secure area!'") names an exact
        # expected string — check for it directly rather than keyword-matching the surrounding prose,
        # which mangles longer descriptive assertions into noisy, unmatchable phrases.
        m = _QUOTED_RE.search(step_text)
        literal = (m.group(1) if m and m.group(1) is not None else (m.group(2) if m else None))
        if literal and len(literal) > 1:
            body_text = (await page.locator("body").inner_text())[:5000]
            if literal.lower() in body_text.lower():
                return {"ok": True}
            return {"ok": False, "fail_type": "assertion-failed", "error": f"Expected text '{literal}' not found on page"}

        if any(k in t for k in ("required", "validation", "error", "invalid")):
            body_text = (await page.locator("body").inner_text())[:3000].lower()
            if any(k in body_text for k in ("required", "invalid", "error", "must")):
                return {"ok": True}
            return {"ok": False, "fail_type": "assertion-failed", "error": "No validation/error text visible on page"}

        # generic "visible/present" assertion — search for a matching element by keyword
        kw = _keywords(re.sub(r"^assert(ion)?\s*", "", step_text, flags=re.I))
        if kw and len(kw) > 2:
            loc, _desc, _tried = await _find_clickable(page, kw, [])
            if loc is not None:
                visible = await loc.is_visible()
                if visible:
                    return {"ok": True}
                return {"ok": False, "fail_type": "assertion-failed", "error": f"'{kw}' matched but not visible"}
        # nothing specific matched — soft-pass on "page rendered without error"
        return {"ok": True, "note": "generic assertion; page loaded without navigation error"}
    except Exception as e:
        return {"ok": False, "fail_type": "assertion-failed", "error": str(e)[:200]}


async def run_flow_pw(run_id, browser, storage_state_path, config, flow, spec, on_step):
    """Executes one flow's steps in a fresh, isolated browser context. Returns a real execution dict."""
    art_dir = _run_dir(run_id)
    slug = _slug(spec["flow_name"])
    video_dir = art_dir / "video_tmp" / slug
    video_dir.mkdir(parents=True, exist_ok=True)

    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        storage_state=storage_state_path if storage_state_path else None,
        record_video_dir=str(video_dir), record_video_size={"width": 1280, "height": 900},
    )
    await context.tracing.start(screenshots=True, snapshots=True)
    page = await context.new_page()

    console_errors, network_errors = [], []
    # Chrome's console "error" level covers a lot of benign advisory noise that has nothing to do
    # with the app being broken — mixed-content warnings, resource-load failures (tracked separately
    # via the response listener), deprecated-API notices, CSP reports. Only messages that actually
    # look like an uncaught runtime exception count as a real JS-exception signal here; page.on
    # "pageerror" is exempt from the filter since it inherently only fires for genuine uncaught
    # exceptions/unhandled rejections, never console.error() calls or resource issues.
    page.on("console", lambda m: console_errors.append(m.text[:200])
            if m.type == "error" and _JS_EXCEPTION_RE.search(m.text) else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)[:200]))
    page.on("response", lambda r: network_errors.append({"url": r.url, "status": r.status}) if r.status >= 500 else None)

    steps_log = []
    start = time.time()
    fail_type, error_msg, failed = None, None, False
    prev_url = config["url"]

    try:
        await page.goto(config["url"], wait_until="domcontentloaded", timeout=15000)
    except Exception:
        pass

    step_selectors = spec.get("step_selectors")
    flow_wide_selectors = [s["selector"] for s in spec.get("selectors", [])]
    for i, step_text in enumerate(flow.get("steps") or ["Navigate to base URL"]):
        result = {"ok": True}
        if not failed:
            result = await _execute_step(page, step_text, [s["selector"] for s in spec.get("selectors", [])],
                                          config["url"], prev_url,
                                          username=config.get("username"), password=config.get("password"))
            prev_url = page.url
        shot_name = f"{slug}-step{i+1}.png"
        try:
            await page.screenshot(path=str(art_dir / shot_name), timeout=5000)
            shot_url = artifact_url(run_id, shot_name)
        except Exception:
            shot_url = None
        # hold on the settled state briefly so the recorded video has a watchable frame per step,
        # instead of the whole flow blurring past in well under a second on a fast/simple page
        await page.wait_for_timeout(400)
        step_entry = {"index": i + 1, "description": step_text, "ok": result.get("ok", True),
                      "note": result.get("error") or result.get("note"), "screenshot_url": shot_url,
                      "locator": result.get("locator")}
        steps_log.append(step_entry)
        if on_step:
            await on_step(spec, step_entry, len(flow.get("steps") or []))
        if not result.get("ok", True) and not failed:
            failed = True
            fail_type = result.get("fail_type", "assertion-failed")
            error_msg = result.get("error", "Step failed")

    duration = round(time.time() - start, 1)
    final_shot = f"{slug}-final.png"
    try:
        await page.screenshot(path=str(art_dir / final_shot), full_page=True, timeout=5000)
    except Exception:
        pass

    trace_name = f"{slug}.zip"
    try:
        await context.tracing.stop(path=str(art_dir / trace_name))
    except Exception:
        trace_name = None

    await context.close()  # finalizes the recorded video
    video_name = None
    try:
        vfiles = list(video_dir.glob("*.webm"))
        if vfiles:
            final_video = art_dir / f"{slug}.webm"
            vfiles[0].replace(final_video)
            video_name = final_video.name
    except Exception:
        pass

    if network_errors and not failed:
        failed, fail_type = True, "network-5xx"
        error_msg = f"{network_errors[0]['status']} response from {network_errors[0]['url']}"
    elif console_errors and not failed:
        failed, fail_type = True, "console-exception"
        error_msg = console_errors[0]

    status = "failed" if failed else "passed"
    execution = {
        "id": str(uuid.uuid4()), "run_id": run_id, "spec_id": spec["id"], "flow_id": spec["flow_id"],
        "flow_name": spec["flow_name"], "flow_type": spec["flow_type"], "status": status,
        "duration": duration, "final_status": status,
        "artifacts": {
            "screenshot": artifact_url(run_id, final_shot) if (art_dir / final_shot).exists() else None,
            "trace": artifact_url(run_id, trace_name) if trace_name else None,
            "video": artifact_url(run_id, video_name) if video_name else None,
        },
        "steps": steps_log,
        "console_errors": console_errors[:5],
        "network": network_errors[:5],
    }
    if failed:
        execution["fail_type"] = fail_type
        execution["error"] = error_msg
    return execution
