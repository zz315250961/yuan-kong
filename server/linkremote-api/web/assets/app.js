const views = [...document.querySelectorAll('[data-view]')];
const statusRegion = document.querySelector('#live-status');
const deviceList = document.querySelector('#device-list');
const deviceCount = document.querySelector('#device-count');
const deviceSearch = document.querySelector('#device-search');
let allDevices = [];
let deviceRequest = 0;
let hasLoadedDevices = false;
let accountEmail = '';
let accountRequest = 0;
let announcementTimer;

function announce(message = '', isError = false) {
  window.clearTimeout(announcementTimer);
  statusRegion.textContent = message;
  statusRegion.classList.toggle('error', isError);
  if (message && !isError) announcementTimer = window.setTimeout(() => { statusRegion.textContent = ''; }, 5000);
}

function showView(name, focus = true) {
  if (name !== 'dashboard') {
    ++accountRequest;
    ++deviceRequest;
    allDevices = [];
    hasLoadedDevices = false;
    deviceList.replaceChildren();
    deviceCount.textContent = '';
  }
  for (const view of views) view.hidden = view.dataset.view !== name;
  document.querySelectorAll('[data-dashboard-section]').forEach(button => {
    button.disabled = name !== 'dashboard';
  });
  document.querySelector('.mobile-nav').hidden = name !== 'dashboard';
  if (name === 'reset' && accountEmail) {
    document.querySelector('#reset-form').elements.email.value = accountEmail;
  }
  if (focus) document.querySelector(`[data-view="${name}"] h1`)?.focus({preventScroll: true});
  announce();
}

async function request(path, options = {}, redirectUnauthorized = true) {
  const accountGeneration = accountRequest;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, {
      ...options,
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', ...(options.headers || {})},
      signal: controller.signal,
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && redirectUnauthorized && path !== '/remote/api/web/login' && accountGeneration === accountRequest) showView('login');
    if (!response.ok || body.error) {
      const error = new Error(body.error || `请求失败 (${response.status})，请重试`);
      error.status = response.status;
      throw error;
    }
    return body;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时，请检查网络后重试');
    if (error instanceof TypeError) throw new Error('无法连接服务器，请检查网络后重试');
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function submitJson(form, path, onSuccess) {
  const submit = form.querySelector('[type="submit"]');
  if (submit.disabled) return;
  const label = submit.textContent;
  submit.disabled = true;
  submit.textContent = '正在提交…';
  form.setAttribute('aria-busy', 'true');
  try {
    const body = Object.fromEntries(new FormData(form).entries());
    const result = await request(path, {method: 'POST', body: JSON.stringify(body)});
    form.querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; });
    await onSuccess(result);
  } catch (error) {
    announce(error.message, true);
  } finally {
    submit.disabled = false;
    submit.textContent = label;
    form.setAttribute('aria-busy', 'false');
  }
}

function formatDate(value) {
  if (!value) return '从未登录';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  const seconds = Math.max(0, (Date.now() - date.valueOf()) / 1000);
  if (seconds < 60) return '刚刚';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return date.toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
}

function actionButton(label, action, className = '') {
  const element = document.createElement('button');
  element.type = 'button';
  element.textContent = label;
  element.className = `table-action ${className}`.trim();
  element.addEventListener('click', () => action(element));
  return element;
}

function tableCell(label, content, className = '') {
  const cell = document.createElement('td');
  cell.dataset.label = label;
  cell.className = className;
  if (content instanceof Node) cell.append(content);
  else cell.textContent = content;
  return cell;
}

function renderDevice(device) {
  const info = device.info || {};
  const row = document.createElement('tr');
  const name = document.createElement('span');
  name.className = 'device-name';
  const icon = document.createElement('span');
  icon.className = 'device-type';
  icon.setAttribute('aria-hidden', 'true');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  const shape = document.createElementNS(svg.namespaceURI, 'path');
  shape.setAttribute('d', String(info.os || '').toLowerCase().includes('android')
    ? 'M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Zm2 15h4'
    : 'M3 4h18v13H3V4Zm5 17h8m-4-4v4');
  svg.append(shape);
  icon.append(svg);
  name.append(icon, document.createTextNode(info.device_name || device.id || '未命名设备'));

  const status = document.createElement('span');
  status.className = 'device-status';
  status.textContent = '已绑定';
  const controllerOnly = device.controller_only === true || ['ios', 'ipados'].includes(String(info.os || '').toLowerCase());
  const canCopyId = Boolean(device.id) && !controllerOnly && device.can_connect !== false;

  const actions = document.createElement('div');
  actions.append(
    actionButton('复制 ID', async () => {
      try {
        await navigator.clipboard.writeText(device.id);
        announce('设备 ID 已复制');
      } catch (_) {
        announce('复制失败，请手动选择设备 ID', true);
      }
    }),
    actionButton('移除', async button => {
      const accountGeneration = accountRequest;
      if (!window.confirm('从当前账号的设备列表移除此记录？客户端再次登录或同步账号时，设备可能重新出现。')) return;
      button.disabled = true;
      try {
        await request(`/remote/api/peers/${encodeURIComponent(device.uuid)}`, {method: 'DELETE'});
        if (accountGeneration !== accountRequest) return;
        allDevices = allDevices.filter(item => item.uuid !== device.uuid);
        renderDevices();
        announce('设备记录已移除');
        await loadDevices();
      } catch (error) {
        if (accountGeneration === accountRequest) announce(error.message, true);
      } finally {
        button.disabled = false;
      }
    }, 'remove'),
  );
  actions.querySelector('button').disabled = !canCopyId;

  row.append(
    tableCell('设备', name),
    tableCell('系统', info.os || '未知'),
    tableCell('设备 ID', controllerOnly ? 'iOS 控制端（不提供被控 ID）' : device.id || '设备 ID 尚未就绪', 'device-id'),
    tableCell('状态', status),
    tableCell('最近登录', formatDate(device.last_login)),
    tableCell('操作', actions),
  );
  return row;
}

function renderDevices() {
  const query = deviceSearch.value.trim().toLocaleLowerCase('zh-CN');
  const devices = query ? allDevices.filter(device => {
    const info = device.info || {};
    return [device.id, info.device_name, info.os]
      .some(value => String(value || '').toLocaleLowerCase('zh-CN').includes(query));
  }) : allDevices;

  deviceCount.textContent = query
    ? `找到 ${devices.length} 台设备，共 ${allDevices.length} 台`
    : `${allDevices.length} 台设备`;
  deviceList.replaceChildren();
  if (!devices.length) {
    const row = document.createElement('tr');
    row.className = 'empty-row';
    const cell = document.createElement('td');
    cell.colSpan = 6;
    cell.textContent = query ? '没有匹配的设备。' : '暂无设备。Windows 或 Android 客户端登录后会显示在这里。';
    row.append(cell);
    deviceList.append(row);
    return;
  }
  deviceList.append(...devices.map(renderDevice));
}

async function loadDevices() {
  const requestId = ++deviceRequest;
  const refresh = document.querySelector('#refresh-devices');
  refresh.disabled = true;
  refresh.textContent = '刷新中…';
  deviceList.setAttribute('aria-busy', 'true');
  if (!hasLoadedDevices) showDeviceMessage('正在加载设备…');
  try {
    const result = await request('/remote/api/peers');
    if (requestId !== deviceRequest) return;
    allDevices = Array.isArray(result.data) ? result.data : [];
    hasLoadedDevices = true;
    renderDevices();
  } catch (error) {
    if (requestId !== deviceRequest) return;
    if (!hasLoadedDevices) showDeviceMessage('设备加载失败，请点击“刷新”重试。');
    throw error;
  } finally {
    if (requestId === deviceRequest) {
      deviceList.setAttribute('aria-busy', 'false');
      refresh.disabled = false;
      refresh.textContent = '刷新';
    }
  }
}

function showDeviceMessage(message) {
  const row = document.createElement('tr');
  row.className = 'empty-row';
  const cell = document.createElement('td');
  cell.colSpan = 6;
  cell.textContent = message;
  row.append(cell);
  deviceList.replaceChildren(row);
  deviceCount.textContent = message.startsWith('正在') ? '正在加载…' : '暂时无法获取设备数量';
}

async function loadDashboard({initial = false} = {}) {
  const accountGeneration = ++accountRequest;
  let user;
  try {
    user = await request('/remote/api/currentUser', {method: 'POST', body: '{}'}, !initial);
  } catch (error) {
    if (accountGeneration !== accountRequest) return;
    throw error;
  }
  if (accountGeneration !== accountRequest) return;
  const email = user.email || user.name || '—';
  if (accountEmail !== email) {
    allDevices = [];
    hasLoadedDevices = false;
    deviceSearch.value = '';
  }
  accountEmail = email;
  document.querySelector('#account-email').textContent = email;
  document.querySelector('#user-menu').textContent = email.split('@')[0] || '账号';
  showView('dashboard');
  await loadDevices();
}

async function sendCode(buttonElement) {
  const purpose = buttonElement.dataset.sendCode;
  const form = buttonElement.closest('form');
  const field = purpose === 'change_email' ? 'new_email' : (purpose === 'reset' ? 'email' : 'username');
  const email = form.elements[field].value.trim();
  if (!form.elements[field].reportValidity()) {
    return;
  }
  buttonElement.disabled = true;
  try {
    const result = await request('/remote/api/send-code', {
      method: 'POST',
      body: JSON.stringify({email, purpose}),
    });
    announce(result.message || '验证码已发送');
    const originalLabel = buttonElement.textContent;
    let remaining = 60;
    buttonElement.textContent = `${remaining} 秒后重发`;
    const countdown = window.setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        window.clearInterval(countdown);
        buttonElement.textContent = originalLabel;
        buttonElement.disabled = false;
      } else {
        buttonElement.textContent = `${remaining} 秒后重发`;
      }
    }, 1000);
  } catch (error) {
    announce(error.message, true);
    buttonElement.disabled = false;
  }
}

document.querySelectorAll('[data-show-view]').forEach(element => {
  element.addEventListener('click', () => showView(element.dataset.showView));
});
document.querySelectorAll('[data-send-code]').forEach(element => {
  element.addEventListener('click', () => sendCode(element));
});
document.querySelectorAll('[data-toggle-email]').forEach(element => {
  element.addEventListener('click', () => {
    const form = document.querySelector('#change-email-form');
    form.hidden = !form.hidden;
    if (!form.hidden) form.elements.new_email.focus();
  });
});
document.querySelectorAll('[data-dashboard-section]').forEach(element => {
  element.addEventListener('click', () => {
    document.querySelectorAll('[data-dashboard-section]').forEach(item => {
      const selected = item.dataset.dashboardSection === element.dataset.dashboardSection;
      item.classList.toggle('active', selected);
      if (selected) item.setAttribute('aria-current', 'location');
      else item.removeAttribute('aria-current');
    });
    document.querySelector(`#${element.dataset.dashboardSection}-section`)?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    });
  });
});

document.querySelector('#login-form').addEventListener('submit', event => {
  event.preventDefault();
  return submitJson(event.currentTarget, '/remote/api/web/login', loadDashboard);
});
document.querySelector('#register-form').addEventListener('submit', event => {
  event.preventDefault();
  return submitJson(event.currentTarget, '/remote/api/web/register', loadDashboard);
});
document.querySelector('#reset-form').addEventListener('submit', event => {
  event.preventDefault();
  return submitJson(event.currentTarget, '/remote/api/reset-password', async result => {
    showView('login');
    announce(result.message || '密码已重置，请重新登录');
  });
});
document.querySelector('#change-email-form').addEventListener('submit', event => {
  event.preventDefault();
  const form = event.currentTarget;
  return submitJson(form, '/remote/api/change-email', async () => {
    form.hidden = true;
    await loadDashboard();
    announce('绑定邮箱已更新');
  });
});
document.querySelector('#refresh-devices').addEventListener('click', async () => {
  const accountGeneration = accountRequest;
  try {
    await loadDevices();
    if (accountGeneration === accountRequest) announce('设备列表已刷新');
  } catch (error) {
    if (accountGeneration === accountRequest) announce(error.message, true);
  }
});
document.querySelector('#logout-button').addEventListener('click', async () => {
  try {
    await request('/remote/api/web/logout', {method: 'POST', body: '{}'});
    accountEmail = '';
    allDevices = [];
    hasLoadedDevices = false;
    deviceSearch.value = '';
    document.querySelectorAll('form').forEach(form => form.reset());
    showView('login');
    announce('已安全退出');
  } catch (error) {
    announce(error.message, true);
  }
});
deviceSearch.addEventListener('input', renderDevices);

loadDashboard({initial: true}).catch(error => {
  showView('login', false);
  if (error.status !== 401) announce(error.message, true);
});
