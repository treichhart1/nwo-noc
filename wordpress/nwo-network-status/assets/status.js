/* Public presentation only: never fetch private NOC APIs or inject HTML. */
(() => {
  'use strict';
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  document.querySelectorAll('[data-nwo-status-endpoint]').forEach(root => {
    let options;
    try { options = JSON.parse(root.dataset.nwoStatusOptions); } catch { return; }
    const endpoint = new URL(root.dataset.nwoStatusEndpoint, location.href);
    if (endpoint.origin !== location.origin) return;
    let expiryTimer, pending = false, offset = 0, lastSuccess = null;
    root.dataset.theme = options.theme;
    root.dataset.view = options.view;
    const labels = options.labels || {};
    for (const [key, color] of Object.entries(options.colors || {})) {
      if (['operational','disruption','unknown'].includes(key) && /^#[a-f0-9]{6}$/i.test(color)) root.style.setProperty('--nwo-' + key, color);
    }
    const label = value => labels[value] || 'Status unknown';
    function support() {
      root.append(el('p', options.support_text));
      if (options.support_url) {
        try { const url = new URL(options.support_url); if (url.protocol === 'https:') { const link=el('a','Support');link.href=url.href;root.append(link); } } catch { /* fail closed */ }
      }
    }
    function retrieved() { if (lastSuccess) root.append(el('p', `Last successful retrieval: ${new Date(lastSuccess).toLocaleString()}`)); }
    function unavailable(message) {
      root.replaceChildren(el('h2', options.brand), el('p', '⚠ Status unknown', 'nwo-status-summary'), el('p', message));
      root.dataset.state = 'unknown';
      retrieved(); support();
    }
    function render(data) {
      clearTimeout(expiryTimer);
      if (typeof data.last_success === 'string' && Number.isFinite(Date.parse(data.last_success))) lastSuccess = data.last_success;
      if (!data.available || !data.feed || Date.parse(data.valid_until) <= Date.now()) {
        unavailable('The approved gateway is unavailable. Previously cached operational status is not current.');
        return;
      }
      const feed = data.feed;
      root.dataset.state = feed.overall_status;
      root.replaceChildren(el('h2', options.brand));
      const summary = el('p', `${feed.overall_status === 'all_systems_operational' ? '✓' : '⚠'} ${label(feed.overall_status)}`, 'nwo-status-summary');
      summary.setAttribute('role', 'status');
      root.append(summary);
      root.append(el('p', `Last approved public update: ${new Date(feed.last_public_update).toLocaleString()}`));
      if (feed.freshness !== 'fresh') root.append(el('p', 'Stale information — current service status is unknown.', 'nwo-status-warning'));
      if (options.view !== 'banner') {
        if (feed.service_areas.length) {
          const list = el('ul');
          feed.service_areas.forEach(area => list.append(el('li', `${area.label}${area.county ? ` (${area.county})` : ''}${area.group_label ? ` · ${area.group_label}` : ''}${area.technology && area.technology !== 'unknown' ? ` · ${area.technology}` : ''}: ${label(area.status)}`)));
          root.append(el('h3', 'Service areas'), list);
        }
        [['incidents', 'Current incidents'], ['maintenance', 'Maintenance'], ['resolved', 'Incident history']].forEach(([key, title]) => {
          if (!feed[key].length) return;
          root.append(el('h3', title));
          feed[key].forEach(item => {
            const article = el('article');
            article.append(el('h4', item.title), el('p', label(item.display_status)), el('p', item.description), el('p', item.areas.join(', ')));
            if (item.starts_at) article.append(el('p', `Starts: ${new Date(item.starts_at).toLocaleString()}`));
            if (item.ends_at) article.append(el('p', `Ends: ${new Date(item.ends_at).toLocaleString()}`));
            if (item.estimated_restoration_at) article.append(el('p', `Approved restoration estimate: ${new Date(item.estimated_restoration_at).toLocaleString()}`));
            root.append(article);
          });
        });
        if (offset || feed.next_history_offset !== null) {
          const controls = el('div');
          const previous = el('button', 'Previous history'); previous.type = 'button'; previous.disabled = !offset;
          previous.onclick = () => { offset = Math.max(0, offset - 20); refresh(); };
          const next = el('button', 'More history'); next.type = 'button'; next.disabled = feed.next_history_offset === null || feed.next_history_offset > 180;
          next.onclick = () => { offset = feed.next_history_offset; refresh(); };
          controls.append(previous, next); root.append(controls);
        }
      }
      retrieved(); support();
      expiryTimer = setTimeout(() => unavailable('The last retrieved update expired. Current status is unknown.'), Math.max(0, Date.parse(data.valid_until) - Date.now()));
    }
    async function refresh() {
      if (pending) return;
      pending = true;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const url = new URL(endpoint); url.searchParams.set('history_offset', String(offset));
        const response = await fetch(url, {cache: 'no-store', credentials: 'omit', redirect: 'error', signal: controller.signal});
        if (!response.ok && response.status !== 503) throw new Error('unavailable');
        const data = await response.json();
        render(response.ok ? data : {available:false,last_success:data.last_success});
      } catch { clearTimeout(expiryTimer); unavailable('The approved gateway is unavailable. Current status is unknown.'); }
      finally { clearTimeout(timeout); pending = false; }
    }
    refresh();
    setInterval(refresh, Math.min(60, Math.max(15, Number(options.refresh) || 60)) * 1000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  });
})();
