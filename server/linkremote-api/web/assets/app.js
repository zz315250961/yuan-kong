const views = [...document.querySelectorAll('[data-view]')];
const statusRegion = document.querySelector('#live-status');
const deviceList = document.querySelector('#device-list');
const deviceCount = document.querySelector('#device-count');
const deviceSearch = document.querySelector('#device-search');
let allDevices = [];

function announce(message = '', isError = false) {
  statusRegion.textContent = message;
  statusRegion.classList.toggle('error', isError);
}

function showView(name) {
  for (const view of views) view.hidden = view.dataset.view !== name;
  announce();
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
  element.addEventListener('click', action);
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
  icon.textContent = String(info.os || '').toLowerCase().includes('android') ? '▯' : '▣';
  name.append(icon, document.createTextNode(info.device_name || device.id || '未命名设备'));

  const status = document.createElement('span');
  status.className = 'device-status';
  status.textContent = '已绑定';

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
    actionButton('移除', async () => {
      if (!window.confirm('仅从当前账号中移除此设备？客户端本身不会被卸载。')) return;
      try {
        await request(`/remote/api/peers/${encodeURIComponent(device.uuid)}`, {method: 'DELETE'});
        announce('设备已从账号中移除');
        await loadDevices();
      } catch (error) {
        announce(error.message, true);
      }
    }, 'remove'),
  );

  row.append(
    tableCell('设备', name),
    tableCell('系统', info.os || '未知'),
    tableCell('设备 ID', device.id || '—', 'device-id'),
    tableCell('状态', status),
    tableCell('最后在线', formatDate(device.last_login)),
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
  deviceList.setAttribute('aria-busy', 'true');
  try {
    const result = await request('/remote/api/peers');
    allDevices = Array.isArray(result.data) ? result.data : [];
    renderDevices();
  } finally {
    deviceList.setAttribute('aria-busy', 'false');
  }
}

async function loadDashboard() {
  const user = await request('/remote/api/currentUser', {method: 'POST', body: '{}'});
  const email = user.email || user.name || '—';
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
  if (!email) {
    announce('请先填写邮箱', true);
    form.elements[field].focus();
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
    document.querySelectorAll('[data-dashboard-section]').forEach(item => item.classList.toggle('active', item === element));
    document.querySelector(`#${element.dataset.dashboardSection}-section`)?.scrollIntoView({behavior: 'smooth'});
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
  return submitJson(event.currentTarget, '/remote/api/change-email', async () => {
    event.currentTarget.hidden = true;
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
    await request('/remote/api/web/logout', {method: 'POST', body: '{}'});
    showView('login');
    announce('已安全退出');
  } catch (error) {
    announce(error.message, true);
  }
});
deviceSearch.addEventListener('input', renderDevices);

loadDashboard().catch(error => {
  showView('login');
  announce(error.message, true);
});
