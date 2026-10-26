// Run with Node.js, Playwright and LINKREMOTE_TEST_PYTHON pointing to Python 3.
// All API data is held in a temporary database; no existing server is contacted.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

const fixture = `
import os, sys, tempfile
sys.path.insert(0, sys.argv[1])
import app
with tempfile.TemporaryDirectory() as temp:
    app.DB_PATH = os.path.join(temp, 'web-test.db')
    app.COOKIE_SECURE = False
    app.SMTP_CONFIG = None
    app.generate_code = lambda: '123456'
    app.init_db()
    with app.db_connect() as conn:
        conn.execute('INSERT INTO users (email,password_hash,salt,created_at) VALUES (?,?,?,?)',
                     ('owner@example.test',app.hash_password('secret1','test'),'test',app.now_iso()))
    server = app.make_server(host='127.0.0.1', port=0)
    print('TEST_PORT=' + str(server.server_address[1]), flush=True)
    server.serve_forever()
`;

async function main() {
  const python = process.env.LINKREMOTE_TEST_PYTHON || 'python';
  const server = spawn(python, ['-u', '-c', fixture, path.resolve(__dirname, '..')], {stdio: ['ignore', 'pipe', 'pipe']});
  let browser;
  let diagnosticPage;
  let output = '';
  let errors = '';
  server.stdout.on('data', chunk => { output += chunk; });
  server.stderr.on('data', chunk => { errors += chunk; });
  const timeout = setTimeout(() => server.kill(), 120000);
  try {
    const port = await new Promise((resolve, reject) => {
      const inspect = () => {
        const match = output.match(/TEST_PORT=(\d+)/);
        if (match) resolve(match[1]);
      };
      server.stdout.on('data', inspect);
      server.on('exit', code => reject(new Error(`Test API exited (${code}): ${errors}`)));
      inspect();
    });
    browser = await chromium.launch({headless: true, channel: process.env.LINKREMOTE_TEST_BROWSER || 'msedge'});
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    diagnosticPage = page;
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const base = `http://127.0.0.1:${port}`;
    const nativeLogin = async device => {
      const response = await fetch(`${base}/api/login`, {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: 'owner@example.test', password: 'secret1', ...device})});
      assert.equal(response.status, 200);
      return (await response.json()).access_token;
    };
    // Real client login and authenticated late-ID registration, never direct DB upsert.
    const desktopToken = await nativeLogin({id: '', uuid: 'office/device + 1',
      deviceInfo: {os: 'windows', name: '办公室电脑 · ' + '超长设备名称'.repeat(14)}});
    const heartbeat = await fetch(`${base}/api/heartbeat`, {method: 'POST',
      headers: {'Content-Type': 'application/json', Authorization: `Bearer ${desktopToken}`},
      body: JSON.stringify({id: '123456789', uuid: 'office/device + 1'})});
    assert.equal(heartbeat.status, 200);
    const androidToken = await nativeLogin({id: '987654321', uuid: 'android-test', deviceInfo: {os: 'android', name: '我的手机'}});
    await page.goto(`${base}/remote/manage`);
    await page.locator('#login-view').waitFor({state: 'visible'});
    const snapshots = process.env.LINKREMOTE_SCREENSHOT_DIR;
    if (snapshots) fs.mkdirSync(snapshots, {recursive: true});
    const capture = async name => {
      if (snapshots) await page.screenshot({path: path.join(snapshots, `${name}.png`), fullPage: true});
    };
    const checkWidth = async () => {
      const dimensions = await page.evaluate(() => ({scroll: document.documentElement.scrollWidth, width: innerWidth}));
      assert.ok(dimensions.scroll <= dimensions.width, `Horizontal overflow: ${JSON.stringify(dimensions)}`);
    };
    if (process.env.LINKREMOTE_VISUAL_ONLY) {
      for (const width of [1440, 900, 390]) {
        await page.setViewportSize({width, height: 1000});
        await capture(`before-login-${width}`);
        console.log('layout', width, await page.evaluate(() => document.documentElement.scrollWidth));
      }
      return;
    }

    await page.keyboard.press('Tab');
    assert.equal(await page.locator('.skip-link').evaluate(element => element === document.activeElement), true);
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#main-content').evaluate(element => element === document.activeElement), true);

    // Wrong credentials retain a useful error and form input.
    await page.locator('#login-form [name=username]').fill('owner@example.test');
    await page.locator('#login-form [name=password]').fill('wrong-password');
    await page.locator('#login-form [type=submit]').click();
    await page.getByText('邮箱或密码错误', {exact: true}).waitFor();
    await page.route('**/remote/api/peers', route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: '设备服务暂不可用，请重试'})}));
    await page.locator('#login-form [name=password]').fill('secret1');
    await page.locator('#login-form [type=submit]').click();
    await page.locator('#dashboard-view').waitFor({state: 'visible'});
    await page.getByText('设备加载失败，请点击“刷新”重试。', {exact: true}).waitFor();
    await page.unroute('**/remote/api/peers');
    await page.locator('#refresh-devices').click();
    await page.getByText('2 台设备', {exact: true}).waitFor();
    await capture('devices-desktop');
    await checkWidth();
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.locator('#device-list tr').filter({hasText: '987654321'}).getByRole('button', {name: '复制 ID', exact: true}).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), '987654321');
    await page.setViewportSize({width: 390, height: 1000});
    await checkWidth();
    await capture('devices-long-mobile');
    await page.setViewportSize({width: 1440, height: 1000});
    await page.locator('#device-search').fill('does-not-exist');
    await page.getByText('没有匹配的设备。', {exact: true}).waitFor();
    await page.locator('#device-search').fill('');

    await nativeLogin({id: '0', uuid: 'ios-controller', deviceInfo: {os: 'ios', name: 'iPhone 控制端'}});
    await page.locator('#refresh-devices').click();
    await page.getByText('3 台设备', {exact: true}).waitFor();
    const iosRow = page.locator('#device-list tr').filter({hasText: 'iPhone 控制端'});
    await iosRow.getByText('iOS 控制端（不提供被控 ID）', {exact: true}).waitFor();
    assert.equal(await iosRow.getByRole('button', {name: '复制 ID', exact: true}).isDisabled(), true);
    page.once('dialog', dialog => dialog.accept());
    await iosRow.getByRole('button', {name: '移除', exact: true}).click();
    await page.getByText('2 台设备', {exact: true}).waitFor();

    // Failed refresh keeps existing data and offers a retry.
    await page.route('**/remote/api/peers', route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: '设备服务暂不可用，请重试'})}));
    await page.locator('#refresh-devices').click();
    await page.getByText('设备服务暂不可用，请重试', {exact: true}).waitFor();
    assert.equal(await page.locator('#device-list tr').count(), 2);
    await page.unroute('**/remote/api/peers');
    await page.locator('#refresh-devices').click();
    await page.getByText('设备列表已刷新', {exact: true}).waitFor();

    // True login sessions are distinct from device records. Password stays
    // request-local; revoking this synthetic phone does not delete its peer.
    await page.locator('#refresh-sessions').click();
    await page.locator('#session-list tr').filter({hasText: '我的手机'}).waitFor();
    assert.equal(await page.locator('#session-list tr').filter({hasText: '当前会话'}).getByRole('button').count(), 0);
    await page.locator('#session-list tr').filter({hasText: '我的手机'}).getByRole('button', {name: '退出登录', exact: true}).click();
    const revokeForm = page.locator('#session-revoke-form');
    await revokeForm.locator('[name=password]').fill('wrong');
    await revokeForm.locator('[type=submit]').click();
    await page.locator('#session-revoke-error').getByText('当前密码不正确', {exact: true}).waitFor();
    assert.equal(await revokeForm.locator('[name=password]').inputValue(), '');
    await revokeForm.locator('[name=password]').fill('secret1');
    await revokeForm.locator('[type=submit]').click();
    await page.getByText('所选登录会话已退出', {exact: true}).waitFor();
    assert.equal(await page.locator('#session-list tr').filter({hasText: '我的手机'}).count(), 0);
    assert.equal((await fetch(`${base}/api/sessions`, {headers: {Authorization: `Bearer ${androidToken}`}})).status, 401);
    assert.equal((await fetch(`${base}/api/sessions`, {headers: {Authorization: `Bearer ${desktopToken}`}})).status, 200);
    assert.equal(await page.locator('#device-list tr').filter({hasText: '987654321'}).count(), 1);

    // Encoded identifiers must reach the API intact.
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#device-list tr').filter({hasText: '123456789'}).getByRole('button', {name: '移除', exact: true}).click();
    await page.getByText('1 台设备', {exact: true}).waitFor();

    // Change email exercises the previously broken asynchronous success callback.
    await page.locator('[data-toggle-email]').first().click();
    const emailForm = page.locator('#change-email-form');
    await emailForm.locator('[name=new_email]').fill('changed@example.test');
    await emailForm.locator('[data-send-code]').click();
    await page.getByText('验证码已发送', {exact: true}).waitFor();
    await emailForm.locator('[name=code]').fill('123456');
    await emailForm.locator('[name=password]').fill('secret1');
    await emailForm.locator('[type=submit]').click();
    await page.getByText('绑定邮箱已更新', {exact: true}).waitFor();
    assert.equal(await page.locator('#account-email').textContent(), 'changed@example.test');
    assert.equal(await emailForm.isVisible(), false);

    for (const width of [900, 390]) {
      await page.setViewportSize({width, height: 1000});
      await checkWidth();
      await capture(`devices-${width}`);
    }
    await page.locator('#logout-button').click();
    await page.getByText('已安全退出', {exact: true}).waitFor();

    // Registration, logout and password reset use real HTTP and cookie sessions.
    await page.locator('#login-view [data-show-view=register]').click();
    const register = page.locator('#register-form');
    await register.locator('[name=username]').fill('new@example.test');
    await register.locator('[data-send-code]').click();
    await page.getByText('验证码已发送', {exact: true}).waitFor();
    await register.locator('[name=code]').fill('123456');
    await register.locator('[name=password]').fill('newsecret');
    await register.locator('[type=submit]').click();
    await page.getByText('暂无设备。Windows 或 Android 客户端登录后会显示在这里。', {exact: true}).waitFor();
    await page.locator('#logout-button').click();
    await page.getByText('已安全退出', {exact: true}).waitFor();
    await page.locator('#login-view [data-show-view=reset]').click();
    const reset = page.locator('#reset-form');
    await reset.locator('[name=email]').fill('new@example.test');
    await reset.locator('[data-send-code]').click();
    await page.getByText('验证码已发送', {exact: true}).waitFor();
    await reset.locator('[name=code]').fill('123456');
    await reset.locator('[name=password]').fill('resetsecret');
    await reset.locator('[type=submit]').click();
    await page.getByText('密码已重置，请使用新密码登录', {exact: true}).waitFor();
    await page.locator('#login-form [name=username]').fill('new@example.test');
    await page.locator('#login-form [name=password]').fill('resetsecret');
    await page.locator('#login-form [type=submit]').click();
    await page.locator('#dashboard-view').waitFor({state: 'visible'});
    // A late old-account list (or 401) must not leak rows or log out the new account.
    for (const oldStatus of [200, 401]) {
      await page.locator('#logout-button').click();
      await page.getByText('已安全退出', {exact: true}).waitFor();
      await page.locator('#login-form [name=username]').fill('changed@example.test');
      await page.locator('#login-form [name=password]').fill('secret1');
      await page.locator('#login-form [type=submit]').click();
      await page.getByText('1 台设备', {exact: true}).waitFor();
      let release;
      let markHeld;
      let markFinished;
      const held = new Promise(resolve => { markHeld = resolve; });
      const released = new Promise(resolve => { release = resolve; });
      const finished = new Promise(resolve => { markFinished = resolve; });
      let first = true;
      await page.route('**/remote/api/peers', async route => {
        if (!first) return route.continue();
        first = false;
        const actual = await route.fetch();
        const body = await actual.text();
        markHeld();
        await released;
        await route.fulfill({status: oldStatus, contentType: 'application/json',
          body: oldStatus === 200 ? body : JSON.stringify({error: '旧账号登录已失效'})});
        markFinished();
      });
      await page.locator('#refresh-devices').click();
      await held;
      await page.locator('#logout-button').click();
      await page.getByText('已安全退出', {exact: true}).waitFor();
      await page.locator('#login-form [name=username]').fill('new@example.test');
      await page.locator('#login-form [name=password]').fill('resetsecret');
      await page.locator('#login-form [type=submit]').click();
      await page.getByText('0 台设备', {exact: true}).waitFor();
      release();
      await finished;
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await page.locator('#dashboard-view').isVisible(), true);
      assert.equal(await page.locator('#account-email').textContent(), 'new@example.test');
      assert.equal(await page.locator('#device-count').textContent(), '0 台设备');
      assert.equal(await page.locator('#device-list').textContent().then(text => text.includes('987654321')), false);
      assert.equal(await page.locator('#live-status').textContent().then(text => text.includes('旧账号')), false);
      await page.unroute('**/remote/api/peers');
    }
    await page.locator('#logout-button').click();
    await page.getByText('已安全退出', {exact: true}).waitFor();
    for (const width of [1440, 1200, 1120, 900, 768, 390, 320]) {
      await page.setViewportSize({width, height: 1000});
      await checkWidth();
      await capture(`login-${width}`);
    }
    assert.deepEqual(pageErrors, []);
    console.log('PASS: real login-session listing/current refusal/password-error clear/selected revoke with retained peer and other tokens; real Windows/Android login + late-ID heartbeat, iOS controller-only list, account-switch late 200/401 isolation, keyboard skip navigation, cookie login/logout, search/copy/delete/failure recovery, email/register/reset, 320–1440px overflow, no browser exceptions');
  } catch (error) {
    if (diagnosticPage && !diagnosticPage.isClosed()) {
      console.error('Browser failure state:', await diagnosticPage.evaluate(() => ({
        account: document.querySelector('#account-email')?.textContent,
        devices: document.querySelector('#device-count')?.textContent,
        status: document.querySelector('#live-status')?.textContent,
        visible: [...document.querySelectorAll('[data-view]')].filter(view => !view.hidden).map(view => view.dataset.view),
      })).catch(() => ({})));
    }
    console.error('Temporary test server recent requests:', errors.slice(-3500));
    throw error;
  } finally {
    clearTimeout(timeout);
    if (browser) await browser.close();
    server.kill();
    if (server.exitCode === null) await once(server, 'exit');
  }
}

main().catch(error => {console.error(error); process.exitCode = 1;});
