(() => {
  'use strict';
  const badge = document.querySelector('[data-pending-comment-badge]');
  if (!badge) return;
  let active = false;
  let stopped = false;
  let timer;
  const refresh = async () => {
    if (active || stopped) return;
    active = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(badge.dataset.countUrl, {
        credentials: 'same-origin', cache: 'no-store',
        headers: {Accept: 'application/json'}, signal: controller.signal
      });
      if (response.status === 401 || response.status === 403 || response.redirected) {
        stopped = true;
        clearInterval(timer);
        badge.hidden = true;
        return;
      }
      if (!response.ok) return;
      const data = await response.json();
      if (!data.ok || !Number.isSafeInteger(data.pending_count) || data.pending_count < 0) return;
      const count = data.pending_count;
      badge.textContent = count > 99 ? '99+' : String(count);
      badge.setAttribute('aria-label', `${count} 条评论待审核`);
      badge.hidden = count === 0;
    } catch (_) {
      // Retain the last known count on transient network errors; retry next minute.
    } finally {
      clearTimeout(timeout);
      active = false;
    }
  };
  // Existing moderation forms redirect after commit: the destination renders the
  // latest count on the server and this initial fetch immediately reconciles it.
  refresh();
  timer = setInterval(refresh, 60000);
  window.addEventListener('pageshow', event => { if (event.persisted) refresh(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  document.addEventListener('orange:comments-moderated', refresh);
})();
