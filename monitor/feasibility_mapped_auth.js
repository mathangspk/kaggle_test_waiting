// Run via the Playwright run_code tool from the existing isolated Kaggle page.
// User-reported mappings select setup targets, never original user-data paths.
// No cookie/key extraction, profile copying, credential entry or auth bypass.
async (page) => {
  const mappings = [
    ['Profile 6', 'thanglong1996'],
    ['Profile 7', 'annalynhi'],
    ['Profile 5', 'thnhmngc'],
  ];
  const known = new Set([...mappings.map(x => x[1]), 'thangma']);
  const identityFrom = async response => {
    const names = [];
    const walk = x => {
      if (x && typeof x === 'object') for (const [key, value] of Object.entries(x)) {
        if (/^username$/i.test(key) && known.has(value)) names.push(value);
        else if (value && typeof value === 'object') walk(value);
      }
    };
    try {
      walk(await response.json());
      return {http_status: response.status(), known_usernames: [...new Set(names)]};
    } catch { return {status: 'unreadable'}; }
  };
  const current = {};
  let responsePromise;
  try {
    responsePromise = page.waitForResponse(r =>
      r.url().endsWith('/users.UsersService/GetCurrentUser'), {timeout: 30000})
      .then(identityFrom).catch(() => ({status: 'not_observed'}));
    await page.reload({waitUntil: 'domcontentloaded', timeout: 60000});
    Object.assign(current, await responsePromise);
  } catch (e) { current.error_type = e.name; }

  const results = [];
  for (const [profile, account] of mappings) {
    let browser;
    let stage = 'launch_empty_isolated_browser';
    const result = {account, profile, mapping_provenance: 'user_reported',
                    original_profile_opened: false, reused_source_state: false};
    try {
      browser = await page.context().browser().browserType().launch({channel: 'chrome', headless: false});
      const context = await browser.newContext();
      const p = await context.newPage();
      const responses = [];
      p.on('response', r => {
        if (r.url().endsWith('/users.UsersService/GetCurrentUser')) responses.push(identityFrom(r));
      });
      stage = 'kaggle_login';
      await p.goto('https://www.kaggle.com/', {waitUntil: 'domcontentloaded', timeout: 60000});
      // Both observed Kaggle home variants are supported.
      const login = p.getByRole('button', {name: 'Login', exact: true})
        .or(p.getByRole('link', {name: 'Sign In', exact: true}));
      await login.first().click({timeout: 30000});
      const google = p.getByRole('button', {name: 'Sign in with Google', exact: true});
      await google.waitFor({timeout: 15000});
      result.kaggle_login_form_reached = true;
      result.kaggle_identity = await Promise.all(responses);
      stage = 'normal_google_sign_in';
      await google.click();
      await p.waitForTimeout(4000);
      result.auth_pages = [];
      for (const authPage of context.pages()) {
        const url = new URL(authPage.url());
        const googleHost = url.hostname === 'accounts.google.com';
        const text = await authPage.locator('body').innerText();
        const signals = ["Couldn't sign you in", 'Access blocked', 'Authorization Error',
          'invalid_request', 'This browser or app may not be secure', 'Choose an account',
          'Email or phone', 'Sign in with Google', 'JavaScript', 'cookies'].filter(s => text.includes(s));
        result.auth_pages.push({
          origin: url.origin,
          path: url.pathname,
          visible_fixed_signals: signals,
          google_email_prompt_visible: googleHost && await authPage.locator('input[type="email"]').isVisible(),
          browser_rejection_visible: await authPage.getByText(/This browser or app may not be secure/i).count() > 0,
          challenge_visible: await authPage.getByText(/verify you are human|unusual traffic/i).count() > 0,
        });
      }
      result.status = 'user_authentication_required';
      result.coverage = 'none';
      result.credential_entry_attempted = false;
    } catch (e) {
      result.status = 'blocked';
      result.stage = stage;
      result.error_type = e.name;
    } finally {
      if (browser) await browser.close();
    }
    results.push(result);
  }
  return {observed_at: new Date().toISOString(),
          existing_isolated_context_identity: current, results};
}
