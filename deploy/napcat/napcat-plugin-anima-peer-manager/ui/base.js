// AnimaBot shared admin helpers. Edit here, then run `node build-ui.mjs` to inline into every page.
'use strict';
window.AB = (() => {
  const $ = id => document.getElementById(id);

  class ApiError extends Error {
    constructor(message, status = 0) { super(message); this.status = status; }
  }

  // fetch + JSON with readable Chinese errors for network failures, HTML error pages and expired logins.
  async function request(url, {method, body, headers = {}} = {}) {
    let response;
    try {
      response = await fetch(url, {method: method || (body === undefined ? 'GET' : 'POST'), cache: 'no-store',
        headers: {'Content-Type': 'application/json', ...headers}, body: body === undefined ? undefined : JSON.stringify(body)});
    } catch {
      throw new ApiError('无法连接服务器，请确认服务正在运行、网络正常后重试。');
    }
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON, e.g. a proxy error page */ }
    return {response, data};
  }

  // Client for this plugin's NapCat routes; the NapCat WebUI keeps its login token in localStorage.
  function napcat(base) {
    return async (path, body) => {
      let token = null;
      try { token = localStorage.getItem('token'); } catch { /* storage blocked */ }
      if (!token) throw new ApiError('请先返回 NapCat 登录，再重新打开本页面。', 401);
      const {response, data} = await request(base + path, {body, headers: {Authorization: 'Bearer ' + token}});
      if (response.status === 401 || data?.code === -1) throw new ApiError('登录已过期，请点击上方链接重新登录 NapCat，再从扩展页面进入。', 401);
      if (!data) throw new ApiError(`服务器返回了无法识别的内容（HTTP ${response.status}），请刷新页面；若持续出现请查看 NapCat 日志。`, response.status);
      if (!response.ok || data.code !== 0) throw new ApiError(data.message || `操作失败（HTTP ${response.status}）`, response.status);
      return data.data;
    };
  }

  // Toasts sit at the bottom of the viewport so results are visible however far the page is scrolled.
  let toastRoot = null;
  function toast(message, kind = 'info', {action} = {}) {
    if (!toastRoot) {
      toastRoot = document.createElement('div');
      toastRoot.id = 'ab-toasts';
      document.body.append(toastRoot);
    }
    toastRoot.replaceChildren();
    const box = document.createElement('div');
    box.className = 'toast ' + kind;
    box.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    const text = document.createElement('span');
    text.textContent = message;
    box.append(text);
    if (action) {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = action.label;
      b.onclick = () => { box.remove(); action.run(); };
      box.append(b);
    }
    const close = document.createElement('button');
    close.type = 'button'; close.textContent = '关闭'; close.setAttribute('aria-label', '关闭提示');
    close.onclick = () => box.remove();
    box.append(close);
    toastRoot.append(box);
    if (kind === 'ok') setTimeout(() => box.remove(), 5000);
  }
  const ok = message => toast(message, 'ok');
  function error(err, reload) {
    const message = err?.message || String(err);
    toast(message, 'error', err?.status === 409 && reload ? {action: {label: '重新读取', run: reload}} : undefined);
  }

  // One action at a time. Buttons are disabled while it runs; form fields stay usable so focus is never lost.
  function runner({onError} = {}) {
    let busy = false;
    const run = async fn => {
      if (busy) return false;
      busy = true;
      document.body.setAttribute('aria-busy', 'true');
      const buttons = [...document.querySelectorAll('button:not([data-free])')].filter(b => !b.disabled);
      buttons.forEach(b => { b.disabled = true; });
      try { await fn(); return true; }
      catch (err) { (onError || error)(err); return false; }
      finally {
        busy = false;
        document.body.removeAttribute('aria-busy');
        buttons.forEach(b => { b.disabled = false; });
      }
    };
    run.busy = () => busy;
    return run;
  }

  // Poll while the tab is visible, never overlapping requests.
  function poll(fn, ms) {
    let inFlight = false;
    const tick = async () => {
      if (inFlight || document.hidden) return;
      inFlight = true;
      try { await fn(); } finally { inFlight = false; }
    };
    setInterval(tick, ms);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });
    tick();
    return tick;
  }

  // Rebuild a <select> only when its options really changed, so an open dropdown is not closed by polling.
  function setOptions(select, items, fallback = '') {
    const signature = JSON.stringify(items.map(i => [i.value, i.label]));
    if (select.dataset.signature !== signature) {
      const old = select.value;
      select.replaceChildren(...items.map(i => new Option(i.label, i.value)));
      select.dataset.signature = signature;
      select.value = items.some(i => i.value === old) ? old : fallback;
    }
    return select.value;
  }

  // Update table cells in place (text selection and scroll survive refreshes). Rows are arrays of text,
  // or {empty: text} for a single full-width message row. `labels` feed the narrow-screen card layout.
  function setRows(tbody, rows, {labels = [], wrap = []} = {}) {
    rows.forEach((row, index) => {
      let tr = tbody.rows[index];
      const cells = row.empty !== undefined ? [row.empty] : row;
      if (!tr || tr.cells.length !== cells.length) {
        const fresh = document.createElement('tr');
        cells.forEach((_, i) => {
          const td = document.createElement('td');
          if (row.empty !== undefined) td.colSpan = labels.length || 1;
          else { if (labels[i]) td.dataset.label = labels[i]; if (wrap.includes(i)) td.className = 'wrap'; }
          fresh.append(td);
        });
        tr ? tr.replaceWith(fresh) : tbody.append(fresh);
        tr = fresh;
      }
      cells.forEach((value, i) => {
        const text = value ?? '—';
        const td = tr.cells[i];
        if (td.textContent !== String(text)) td.textContent = text;
      });
    });
    while (tbody.rows.length > rows.length) tbody.deleteRow(-1);
  }

  // Assign text only when it changed, so periodic refreshes do not reset the user's text selection.
  function setText(el, text) { if (el.textContent !== text) el.textContent = text; }

  const pad = n => String(n).padStart(2, '0');
  function duration(seconds) {
    const n = Math.max(0, Math.ceil(seconds));
    if (!n) return '已到期';
    const h = Math.floor(n / 3600), m = Math.floor(n % 3600 / 60), s = n % 60;
    return (h ? h + '时' : '') + (h || m ? m + '分' : '') + s + '秒';
  }
  const stamp = seconds => seconds ? new Date(seconds * 1000).toLocaleString() : '—';

  // Unsaved-change tracking: marks the flag element, warns before leaving or replacing the form.
  function dirtyTracker(flag) {
    let dirty = false;
    const show = () => {
      if (!flag) return;
      flag.dataset.dirty = String(dirty);
      flag.textContent = dirty ? '有未保存的修改' : '已与服务器同步';
    };
    window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
    show();
    return {
      get dirty() { return dirty; },
      mark() { if (!dirty) { dirty = true; show(); } },
      clear() { dirty = false; show(); },
      watch(root) { root.addEventListener('input', () => this.mark()); root.addEventListener('change', () => this.mark()); },
      confirmDiscard(message = '有未保存的修改，继续将丢失这些修改。确定继续吗？') { return !dirty || confirm(message); },
    };
  }

  return {$, ApiError, request, napcat, toast, ok, error, runner, poll, setOptions, setRows, setText, duration, stamp, pad, dirtyTracker};
})();
