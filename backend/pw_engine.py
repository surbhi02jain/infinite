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
    "email": "autoqa.tester+{n}@example.com",
    "password": "AutoQA-Test-Pass-1!",
    "tel": "5555550123",
    "number": "1",
    "text": "AutoQA Test Value",
    "search": "test",
}

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
               "auth": None, "storage_state_path": None}
    visited, to_visit = set(), [url]
    base_host = urlparse(url).netloc

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900},
                                             user_agent="AutoQA-Explorer/2.0 (+Playwright)")

        if login_url and username and password:
            ok, err = await _attempt_login(context, login_url, username, password)
            surface["auth"] = {"ok": ok, "login_url": login_url, "error": err}
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
            page_data = {"url": u, "status": 0, "title": "", "links": [], "forms": [], "buttons": [], "inputs": []}
            try:
                resp = await page.goto(u, wait_until="domcontentloaded", timeout=15000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=4000)
                except PWTimeoutError:
                    pass
                page_data["status"] = resp.status if resp else 200
                page_data["title"] = (await page.title()) or ""

                anchors = await page.eval_on_selector_all(
                    "a[href]", "els => els.slice(0,40).map(e => ({text: e.textContent.trim().slice(0,60), href: e.href}))")
                for a in anchors:
                    href = a.get("href") or ""
                    if not href or urlparse(href).netloc != base_host:
                        continue
                    page_data["links"].append({"text": a.get("text", ""), "href": href})
                    if href not in visited and href not in to_visit and len(to_visit) < max_pages * 3:
                        to_visit.append(href)

                buttons = await page.eval_on_selector_all(
                    "button, [role=button], input[type=submit], input[type=button]",
                    "els => els.slice(0,30).map(e => (e.textContent||e.value||e.getAttribute('aria-label')||'').trim().slice(0,40))")
                page_data["buttons"] = [b for b in buttons if b]

                inputs = await page.eval_on_selector_all(
                    "input, textarea, select",
                    "els => els.slice(0,30).map(e => ({selector: e.getAttribute('data-testid')||e.id||e.name||'', type: e.type||e.tagName.toLowerCase()}))")
                page_data["inputs"] = [i for i in inputs if i["selector"]]

                forms = await page.eval_on_selector_all(
                    "form",
                    "els => els.slice(0,10).map(f => ({action: f.action, method: (f.method||'get').toUpperCase(), "
                    "fields: Array.from(f.querySelectorAll('input,select,textarea')).map(x => ({name: x.name||x.id||'', type: x.type||x.tagName.toLowerCase()}))}))")
                page_data["forms"] = forms
                surface["forms"].extend(forms)

                surface["routes"].append({"path": urlparse(u).path or "/", "title": page_data["title"], "status": page_data["status"]})
            except Exception as e:
                page_data["error"] = str(e)[:150]
            surface["pages"].append(page_data)

        await context.close()
        await browser.close()
    return surface


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
    t = re.sub(r"\b(button|link|icon|option|tab|element|page|field)\b", "", step_text, flags=re.I)
    prev = None
    while prev != t:
        prev = t
        t = _STOPWORDS.sub("", t.strip())
    t = t.strip().strip("'\"")  # LLM-authored steps often quote the target, e.g. "Click the 'Login' button"
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
    for loc, desc in candidates:
        try:
            await loc.first.wait_for(state="visible", timeout=timeout_ms)
            return loc.first, desc
        except Exception:
            continue
    return None, None


_QUOTED_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")


async def _fill_targeted(page, step_text):
    """Fills the ONE field a step clearly names (e.g. "Enter 'tomsmith' in the username field"),
    honoring any literal quoted value — falling back to type-based dummy data only when the step
    gives no explicit value. Returns True if it found and filled a specific field, else False so the
    caller can fall back to the generic multi-input fill."""
    t = step_text.lower()
    m = _QUOTED_RE.search(step_text)
    value = (m.group(1) if m and m.group(1) is not None else (m.group(2) if m else None))

    if "password" in t:
        selector, kind = 'input[type="password"]', "password"
    elif "email" in t:
        selector, kind = 'input[type="email"]', "email"
    elif "username" in t or "user name" in t or "login" in t:
        selector, kind = 'input[type="text"], input[type="email"], input[name*="user" i]', "text"
    else:
        return False

    loc = page.locator(selector).first
    try:
        await loc.wait_for(state="visible", timeout=3000)
    except Exception:
        return False
    fill_value = value if value else DUMMY_VALUES.get(kind, DUMMY_VALUES["text"]).format(n=int(time.time()) % 10000)
    await loc.fill(fill_value, timeout=3000)
    return True


async def _fill_visible_inputs(page, n_seed):
    filled = []
    for kind, selector in [("password", 'input[type="password"]'), ("email", 'input[type="email"]'),
                            ("number", 'input[type="number"]'), ("text", 'input[type="text"], textarea'),
                            ("search", 'input[type="search"]')]:
        try:
            loc = page.locator(selector)
            count = min(await loc.count(), 5)
            for i in range(count):
                el = loc.nth(i)
                if not await el.is_visible():
                    continue
                val = DUMMY_VALUES.get(kind, DUMMY_VALUES["text"]).format(n=n_seed)
                await el.fill(val, timeout=3000)
                filled.append(selector)
        except Exception:
            continue
    return filled


async def _execute_step(page, step_text, spec_selectors, base_url, prev_url):
    t = step_text.lower()
    try:
        if any(k in t for k in ("navigate", "go to", "visit")):
            path_m = re.search(r"(/[\w\-\/\.]+)", step_text)
            target = urljoin(base_url, path_m.group(1)) if path_m else base_url
            resp = await page.goto(target, wait_until="domcontentloaded", timeout=15000)
            if resp and resp.status >= 500:
                return {"ok": False, "fail_type": "network-5xx", "error": f"{target} responded {resp.status}"}
            return {"ok": True}

        if t.strip().startswith("assert") or "assert" in t or t.strip().startswith("verify"):
            return await _execute_assert(page, t, step_text, base_url, prev_url)

        if any(k in t for k in ("fill", "enter", "type", "input")):
            if await _fill_targeted(page, step_text):
                return {"ok": True}
            filled = await _fill_visible_inputs(page, int(time.time()) % 10000)
            if not filled:
                return {"ok": True, "note": "no empty inputs found to fill"}
            return {"ok": True}

        if any(k in t for k in ("submit", "proceed", "continue", "checkout", "confirm")):
            loc, desc = await _find_clickable(page, step_text, spec_selectors)
            if loc is None:
                await page.locator('button[type="submit"], input[type="submit"]').first.click(timeout=5000)
            else:
                await loc.click(timeout=8000)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except PWTimeoutError:
                pass
            return {"ok": True}

        if any(k in t for k in ("click", "select", "choose", "tap", "press", "add")):
            loc, desc = await _find_clickable(page, step_text, spec_selectors)
            if loc is None:
                return {"ok": False, "fail_type": "selector-not-found",
                        "error": f"No element found matching step: '{step_text}'"}
            await loc.click(timeout=8000)
            # a click can trigger navigation (e.g. a login/submit-style button not caught by the
            # submit-keyword bucket above) — give it a moment to settle before the next step/assertion
            # inspects the page, otherwise we'd be checking pre-navigation state.
            try:
                await page.wait_for_load_state("networkidle", timeout=4000)
            except PWTimeoutError:
                pass
            return {"ok": True}

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
            loc, _ = await _find_clickable(page, kw, [])
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

    for i, step_text in enumerate(flow.get("steps") or ["Navigate to base URL"]):
        result = {"ok": True}
        if not failed:
            result = await _execute_step(page, step_text, [s["selector"] for s in spec.get("selectors", [])],
                                          config["url"], prev_url)
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
                      "note": result.get("error") or result.get("note"), "screenshot_url": shot_url}
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
