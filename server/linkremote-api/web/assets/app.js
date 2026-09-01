const views = [...document.querySelectorAll('[data-view]')];
const statusRegion = document.querySelector('#live-status');
const deviceList = document.querySelector('#device-list');
const deviceCount = document.querySelector('#device-count');

function announce(message = '', isError = false) {
  statusRegion.textContent = message;
  statusRegion.classList.toggle('error', isError);
}

function showView(name) {
  for (const view of views) view.hidden = view.dataset.view !== name;
  announce();
  document.querySelector(`[data-view="${name}"] h1`)?.focus();
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) {
    showView('login');
    throw new Error('登录已失效，请重新登录');
  }
  if (!response.ok || body.error) {
    throw new Error(body.error || `请求失败 (${response.status})`);
  }
  return body;
}

async function submitJson(form, path, onSuccess) {
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    const body = Object.fromEntries(new FormData(form).entries());
    const result = await request(path, {method: 'POST', body: JSON.stringify(body)});
    form.querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; });
    await onSuccess(result);
  } catch (error) {
    announce(error.message, true);
  } finally {
    submit.disabled = false;
  }
}

function formatDate(value) {
  if (!value) return '从未登录';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function button(label, action, className = '') {
  const element = document.createElement('button');
  element.type = 'button';
  element.textContent = label;
  element.className = className;
  element.addEventListener('click', action);
  return element;
}

function renderDevice(device) {
  const card = document.createElement('article');
  card.className = 'device-card';
  const info = device.info || {};
  const title = document.createElement('h3');
  title.textContent = info.device_name || device.id;
  const detail = document.createElement('p');
  detail.className = 'device-detail';
  detail.textContent = `${device.id} · ${info.os || 'Unknown'} · 最近登录 ${formatDate(device.last_login)}`;
  const actions = document.createElement('div');
  actions.className = 'device-actions';
  const copy = button('复制 ID', async () => {
    try {
      await navigator.clipboard.writeText(device.id);
      announce('设备 ID 已复制');
    } catch (_) {
      announce('复制失败，请手动选择设备 ID', true);
    }
  });
  const remove = button('移除', async () => {
    if (!window.confirm('仅从账号列表移除此设备？')) return;
    try {
      await request(`/api/peers/${encodeURIComponent(device.uuid)}`, {method: 'DELETE'});
      announce('设备已从账号列表移除');
      await loadDevices();
    } catch (error) {
      announce(error.message, true);
    }
  }, 'remove');
  actions.append(copy, remove);
  card.append(title, detail, actions);
  return card;
}

async function loadDevices() {
  deviceList.setAttribute('aria-busy', 'true');
  try {
    const result = await request('/api/peers');
    const devices = Array.isArray(result.data) ? result.data : [];
    deviceCount.textContent = `${devices.length} 台`;
    deviceList.replaceChildren();
    if (!devices.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.textContent = '暂无设备。电脑端或安卓端登录后会显示在这里。';
      deviceList.append(empty);
      return;
    }
    deviceList.append(...devices.map(renderDevice));
  } finally {
    deviceList.setAttribute('aria-busy', 'false');
  }
}

async function loadDashboard() {
  const user = await request('/api/currentUser', {method: 'POST', body: '{}'});
  document.querySelector('#account-email').textContent = user.email || user.name || '—';
  showView('dashboard');
  await loadDevices();
}

async function sendCode(buttonElement) {
  const purpose = buttonElement.dataset.sendCode;
  const form = buttonElement.closest('form');
  const field = purpose === 'change_email' ? 'new_email' : (purpose === 'reset' ? 'email' : 'username');
  const email = form.elements[field].value.trim();
  if (!email) {
    announce('请先填写邮箱', true);
    form.elements[field].focus();
    return;
  }
  buttonElement.disabled = true;
  try {
    const result = await request('/api/send-code', {
      method: 'POST',
      body: JSON.stringify({email, purpose}),
    });
    announce(result.message || '验证码已发送');
  } catch (error) {
    announce(error.message, true);
  } finally {
    buttonElement.disabled = false;
  }
}

document.querySelectorAll('[data-show-view]').forEach(element => {
  element.addEventListener('click', () => showView(element.dataset.showView));
});
document.querySelectorAll('[data-send-code]').forEach(element => {
  element.addEventListener('click', () => sendCode(element));
});

document.querySelector('#login-form').addEventListener('submit', event => {
  event.preventDefault();
  return submitJson(event.currentTarget, '/api/web/login', loadDashboard);
});
document.querySelector('#register-form').addEventListener('submit', event => {
  event.preventDefault();
  return submitJson(event.currentTarget, '/api/web/register', loadDashboard);
});
document.querySelector('#reset-form').addEventListener('submit', event => {
  event.preventDefault();
  return submitJson(event.currentTarget, '/api/reset-password', async result => {
    showView('login');
    announce(result.message || '密码已重置，请重新登录');
  });
});
document.querySelector('#change-email-form').addEventListener('submit', event => {
  event.preventDefault();
  return submitJson(event.currentTarget, '/api/change-email', async () => {
    await loadDashboard();
    announce('绑定邮箱已更新');
  });
});
document.querySelector('#refresh-devices').addEventListener('click', async () => {
  try { await loadDevices(); announce('设备列表已刷新'); }
  catch (error) { announce(error.message, true); }
});
document.querySelector('#logout-button').addEventListener('click', async () => {
  try {
    await request('/api/web/logout', {method: 'POST', body: '{}'});
    showView('login');
    announce('已安全退出');
  } catch (error) {
    announce(error.message, true);
  }
});

loadDashboard().catch(() => showView('login'));
