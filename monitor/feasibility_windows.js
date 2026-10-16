// #20 thangma-only read-only verifier; invoke via Playwright run_code filename.
// Kaggle-only storageState stays in memory. Close only browsers created here.
async (page) => {
  const sample = async (p, resolveLinks) => {
    const started = Date.now();
    const response = p.waitForResponse(r =>
      r.url().endsWith('/users.UsersService/GetCurrentUser'), {timeout: 25000});
    // Register the rejection handler before navigation to avoid an unhandled timeout.
    const identityPromise = response.then(async r => {
      let thangma = false;
      const walk = x => {
        if (x && typeof x === 'object') for (const [k, v] of Object.entries(x)) {
          if (/^username$/i.test(k) && v === 'thangma') thangma = true;
          else if (v && typeof v === 'object') walk(v);
        }
      };
      walk(await r.json());
      return {http_status: r.status(), username: thangma ? 'thangma' : null};
    }).catch(() => ({error: 'identity_response_unavailable'}));
    const cdp = await p.context().newCDPSession(p);
    await cdp.send('Performance.enable');
    try {
      await p.goto('https://www.kaggle.com/', {waitUntil: 'domcontentloaded', timeout: 25000});
      const identity = await identityPromise;
      if (identity.username !== 'thangma') return {identity, error: 'identity_not_verified'};
      await p.getByRole('button', {name: 'Thang Ma', exact: true}).click({timeout: 10000});
      const profile = await p.getByRole('link', {name: 'Your Profile', exact: true}).getAttribute('href');
      if (profile !== '/thangma') return {identity, error: 'profile_mismatch'};
      await p.keyboard.press('Escape');
      const openEvents = p.getByRole('button', {name: /View Active Events|Open Active Events/});
      await openEvents.click({timeout: 10000});
      const panel = p.getByRole('tooltip');
      await panel.waitFor({timeout: 10000});
      await p.waitForTimeout(3000);
      const before = await panel.innerText();
      const scrolling = await panel.evaluate(el => {
        const xs = [el, ...el.querySelectorAll('*')].filter(x => x.clientHeight > 0 &&
          x.scrollHeight > x.clientHeight && ['auto', 'scroll'].includes(getComputedStyle(x).overflowY));
        return xs.map(x => {
          const initial = x.scrollTop;
          x.scrollTop = x.scrollHeight;
          return {client_height: x.clientHeight, scroll_height: x.scrollHeight,
                  before: initial, after: x.scrollTop};
        });
      });
      await p.waitForTimeout(1000);
      const events = await panel.innerText();
      const destinations = [];
      if (resolveLinks) {
        // Event view links only. Never touch menus, editors or production controls.
        const rows = panel.getByRole('link', {name: /^Status for .*Click to open/});
        const count = Math.min(await rows.count(), 5);
        for (let i = 0; i < count; i++) {
          let popup;
          try {
            [popup] = await Promise.all([
              p.waitForEvent('popup', {timeout: 5000}), rows.nth(i).click({timeout: 5000}),
            ]);
            await popup.waitForURL(/kaggle\.com\/code\//, {timeout: 5000});
            const u = new URL(popup.url());
            destinations.push({row: i + 1, path: u.pathname,
              script_version_id: u.searchParams.get('scriptVersionId'), native_session_id: null});
          } catch (e) { destinations.push({row: i + 1, error_type: e.name}); }
          finally { if (popup) await popup.close(); }
        }
      }
      const metrics = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(x => [x.name, x.value]));
      return {observed_at: new Date().toISOString(), identity, profile, events,
        panel_text_unchanged_after_scroll_sample: before === events, scrolling,
        destinations, coverage: 'partial', elapsed_ms: Date.now() - started,
        page_metrics: {js_heap_used_bytes: metrics.JSHeapUsedSize,
          js_heap_total_bytes: metrics.JSHeapTotalSize, task_duration_seconds: metrics.TaskDuration},
        metrics_scope: 'one page renderer; not total browser RSS or CPU utilization'};
    } finally { await cdp.detach(); }
  };
  const bounded = async work => {
    let timer;
    try {
      return await Promise.race([work(), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('visit_deadline')), 60000);
      })]);
    } catch (e) { return {error_type: e.name, deadline_exceeded: e.message === 'visit_deadline'}; }
    finally { clearTimeout(timer); }
  };
  const source = await bounded(() => sample(page, true));
  if (source.identity?.username !== 'thangma' || source.profile !== '/thangma') return {source};
  const raw = await page.context().storageState();
  const state = {
    cookies: raw.cookies.filter(c => ['kaggle.com', '.kaggle.com', 'www.kaggle.com'].includes(c.domain)),
    origins: raw.origins.filter(o => o.origin === 'https://www.kaggle.com'),
  };
  const results = [];
  for (let attempt = 1; attempt <= 2; attempt++) {
    let browser;
    try {
      browser = await page.context().browser().browserType().launch({headless: true, channel: 'chrome', timeout: 20000});
      const context = await browser.newContext({storageState: state});
      const p = await context.newPage();
      results.push({attempt, headless: true, ...await bounded(() => sample(p, false))});
    } catch (e) { results.push({attempt, error_type: e.name}); }
    finally { if (browser) await browser.close(); }
  }
  return {source, results, state_handling: 'Kaggle-only in memory; no exported state file',
    scope: 'thangma only; other accounts pending by user choice', visit_deadline_ms: 60000};
}
