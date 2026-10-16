"""Single read-only browser visit. Supervisor owns the hard process-tree deadline."""
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

from discovery_contract import ACCOUNT, event, failure, observation, validate


# Observed live layout: one status role-link per LI; a neighboring LI must never
# supply its Version label or status. Title subtrees are explicitly excluded.
ROW_SAMPLE_JS = r"""links => links.map(a => {
    const li=a.closest('li');
    if(!li || li.querySelectorAll('[role="link"]').length!==1)
        return {href:'',text:a.innerText||'',title:a.getAttribute('aria-label')||'',
            structured:true,status:'Unknown',interactive:false};
    const label=a.getAttribute('aria-label')||'';
    const title=label.match(/\. Click to open (.*) in new window$/)?.[1];
    const nodes=Array.from(li.querySelectorAll('*'));
    const titleNodes=title?nodes.filter(e=>e.textContent.trim()===title):[];
    const labels=title?Array.from(new Set(nodes.filter(e=>e.childElementCount===0 &&
        !titleNodes.some(t=>t===e || t.contains(e))).map(e=>e.textContent.trim()).filter(t=>
        ['Running','Queued','Pending','Successful','Complete','Failed','Canceled','Cancelled','Unknown'].includes(t)))):[];
    return {href:a.getAttribute('href')?a.href:'',text:li.innerText,title:title||label,
        status:labels.length===1?labels[0]:'Unknown',structured:true,
        interactive:nodes.some(e=>e.childElementCount===0 && e.textContent.trim()==='Interactive Session' &&
            !titleNodes.some(t=>t===e || t.contains(e)))};
})"""


def private_file(path):
    import stat
    st = path.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600):
        raise ValueError("private_state_unavailable")
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise ValueError("private_state_unavailable")


def save_private(path, value):
    import tempfile
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".refresh-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


async def resolve_panel_link(panel, index, expected_text):
    """Observed provider role-links have no href; read their popup destination only.

    Status-link navigation opens an existing version, never an editor/run control.
    Recheck the sampled row before clicking so a reordered panel fails closed.
    """
    link = panel.get_by_role("link", name=re.compile(r"^Status for .*Click to open")).nth(index)
    current = await link.evaluate(r"""a => {
        let p=a; for(let i=0;i<5 && p;i++,p=p.parentElement) {
            if(/Version #\d+/.test(p.innerText))
                return (a.getAttribute('aria-label')||'')+' '+p.innerText;
        } return a.getAttribute('aria-label')||'';
    }""")
    if current != expected_text:
        raise ValueError("panel_unavailable")
    popup = None
    try:
        async with panel.page.expect_popup(timeout=5000) as opened:
            await link.click(timeout=3000)
        popup = await opened.value
        await popup.wait_for_url(re.compile(r"^https://www\.kaggle\.com/code/"), timeout=5000,
                                 wait_until="domcontentloaded")
        return popup.url
    finally:
        if popup:
            await popup.close()


async def collect_panel(panel, pause, resolve=None):
    # Minimum 8 seconds, even when the zero-active footer is already visible.
    # Bounded scrolling collects visible rows; stability is NOT completeness proof.
    rows = {}
    for index in range(12):
        await pause(1000)
        sample = await panel.get_by_role("link", name=re.compile(r"^Status for .*Click to open")).evaluate_all(ROW_SAMPLE_JS)
        # Check mixed panels too: one supported link must not hide another
        # visible version whose destination/selector we do not understand.
        visible_versions = Counter(re.findall(r"\bVersion #(\d+)\b", await panel.inner_text()))
        linked_versions = Counter(v for row in sample for v in
                                  set(re.findall(r"\bVersion #(\d+)\b", row["text"])))
        if visible_versions - linked_versions:
            raise ValueError("panel_unavailable")
        occurrences = Counter()
        for position, row in enumerate(sample):
            if row.get("structured"):
                # Same titles are not identities: keep their occurrence multiplicity.
                # Across visits all href-less identities remain snapshot-scoped.
                fingerprint = hashlib.sha256(repr((row.get("title"), row.get("interactive"),
                    re.findall(r"\bVersion #(\d+)\b", row["text"]))).encode()).hexdigest()
                occurrences[fingerprint] += 1
                key = row.get("href") or f"ui:{fingerprint}:{occurrences[fingerprint]}"
                if len(rows) >= 100 and key not in rows:
                    raise ValueError("panel_unavailable")
                rows[key] = row
                continue
            # Interactive role-links may share a panel ancestor containing other
            # versions. Do not navigate an unidentifiable row to an editor page.
            if len(set(re.findall(r"\bVersion #(\d+)\b", row["text"]))) != 1:
                raise ValueError("panel_unavailable")
            if not row.get("href"):
                if resolve is None:
                    raise ValueError("panel_unavailable")
                # Compatibility resolver for offline adapters: resolve each row in
                # each sample, never cache by nonunique text or changing position.
                row["href"] = await resolve(position, row["text"])
            if len(rows) >= 100 and row["href"] not in rows:
                raise ValueError("panel_unavailable")
            previous = rows.get(row["href"])
            if previous and re.findall(r"\bVersion #(\d+)\b", previous["text"]) != re.findall(
                    r"\bVersion #(\d+)\b", row["text"]):
                raise ValueError("invalid_event_identity")
            rows[row["href"]] = row
        scrolling = await panel.evaluate("""el => {
            let moved=false;
            for(const x of [el,...el.querySelectorAll('*')]) {
                if(x.clientHeight>0 && x.scrollHeight>x.clientHeight &&
                   ['auto','scroll'].includes(getComputedStyle(x).overflowY)) {
                    const before=x.scrollTop; x.scrollTop+=Math.max(1,x.clientHeight*0.8);
                    moved ||= x.scrollTop>before;
                }
            } return moved;
        }""")
        if index >= 7 and not scrolling:
            break
    return list(rows.values())


def identity_hint(raw):
    """Display name is a locating hint only; exact username and UI href are proof."""
    def fields(value, key):
        if isinstance(value, dict):
            return [v for k, v in value.items() if k.lower() == key] + [
                item for v in value.values() for item in fields(v, key)]
        if isinstance(value, list):
            return [item for v in value for item in fields(v, key)]
        return []
    names = fields(raw, "username")
    if not names:
        raise ValueError("authentication_required")
    if any(not isinstance(n, str) or n != ACCOUNT for n in names):
        raise ValueError("identity_mismatch")
    display = fields(raw, "displayname")
    if (not display or any(not isinstance(n, str) or not n.strip() or len(n) > 100 for n in display)
            or len(set(display)) != 1):
        raise ValueError("identity_mismatch")
    return display[0]


async def verify_profile(page, hint):
    # GetCurrentUser can precede React's navigation hydration by >5s under CPU cap.
    # One bounded wait, within the unchanged whole-visit 50s/outer 60s deadlines.
    await page.get_by_role("button", name=hint, exact=True).click(timeout=12000)
    profile = page.get_by_role("link", name="Your Profile", exact=True)
    await profile.wait_for(timeout=5000)
    if await profile.get_attribute("href") != "/" + ACCOUNT:
        raise ValueError("identity_mismatch")
    await page.keyboard.press("Escape")


async def visit(state_path):
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    at = time.time()
    try:
        private_file(state_path)
    except (OSError, ValueError):
        return failure("private_state_unavailable", at)
    try:
        async with asyncio.timeout(50):
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True, timeout=15000)
                try:
                    ctx = await browser.new_context(storage_state=str(state_path))
                    # Defense in depth: this adapter never needs a notebook/editor
                    # document. Abort any unexpected navigation to one.
                    await ctx.route("**/code/**", lambda route: route.abort())
                    page = await ctx.new_page()
                    async with page.expect_response(lambda r:
                            urlsplit(r.url).hostname == "www.kaggle.com" and
                            urlsplit(r.url).path.endswith("/users.UsersService/GetCurrentUser"),
                            timeout=20000) as response:
                        await page.goto("https://www.kaggle.com/", wait_until="domcontentloaded", timeout=20000)
                    response = await response.value
                    if response.status != 200:
                        return failure("authentication_required", at)
                    raw = await response.json()
                    await verify_profile(page, identity_hint(raw))
                    await page.get_by_role("button", name=re.compile("View Active Events|Open Active Events")).click(timeout=5000)
                    panel = page.get_by_role("tooltip")
                    await panel.wait_for(timeout=5000)
                    samples = await collect_panel(panel, page.wait_for_timeout)
                    at = time.time()
                    observations = [observation(row, at, slot) for slot, row in enumerate(samples)]
                    result = {"account": ACCOUNT, "observed_at": at, "coverage": "partial",
                              "observations": observations, "error": None, "auth_error": None}
                    validate(result, {ACCOUNT}, at)
                    raw = await ctx.storage_state()
                    save_private(state_path, {
                        "cookies": [c for c in raw["cookies"] if c["domain"] in
                                    ("kaggle.com", ".kaggle.com", "www.kaggle.com")],
                        "origins": [o for o in raw["origins"] if o["origin"] == "https://www.kaggle.com"]})
                    return result
                finally:
                    await browser.close()
    except (TimeoutError, PlaywrightTimeout):
        return failure("visit_timeout", at)
    except ValueError as exc:
        if str(exc) in ("authentication_required", "identity_mismatch"):
            return failure(str(exc), at)
        return failure("panel_unavailable" if str(exc) == "panel_unavailable" else "invalid_event_identity", at)
    except Exception:
        # Never log provider exceptions, page bodies, cookies or URLs.
        return failure("visit_failed", at)


if __name__ == "__main__":
    save_private(Path(sys.argv[2]), asyncio.run(visit(Path(sys.argv[1]))))
