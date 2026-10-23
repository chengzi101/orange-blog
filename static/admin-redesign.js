(() => {
  const root = document.documentElement;
  const preference = matchMedia('(prefers-reduced-motion: reduce)');
  const motions = new Set();
  const entrances = new Set();
  const ease = 'cubic-bezier(.22,1,.36,1)';
  const play = (node, frames, options = {}) => {
    if (preference.matches || !node?.animate) return null;
    const animation = node.animate(frames, {duration: 260, easing: ease, ...options});
    motions.add(animation);
    const cleanup = () => { motions.delete(animation); entrances.delete(animation); };
    animation.finished.then(cleanup, cleanup);
    return animation;
  };
  preference.addEventListener('change', () => {
    if (preference.matches) motions.forEach(animation => animation.cancel());
  });
  const finishEntrances = () => entrances.forEach(animation => animation.cancel());
  document.addEventListener('pointerdown', finishEntrances, {capture: true, once: true});
  document.addEventListener('keydown', finishEntrances, {capture: true, once: true});
  document.addEventListener('focusin', finishEntrances, {capture: true, once: true});

  const applyTheme = dark => { root.dataset.adminTheme = dark ? 'dark' : 'light'; };
  try { applyTheme(localStorage.getItem('orange-theme') === 'dark'); } catch { applyTheme(false); }
  document.querySelectorAll('[data-admin-theme-toggle]').forEach(button => {
    const update = () => { button.textContent = root.dataset.adminTheme === 'dark' ? '☀ 浅色模式' : '☾ 深色模式'; };
    update();
    button.addEventListener('click', () => {
      const dark = root.dataset.adminTheme !== 'dark';
      applyTheme(dark);
      try { localStorage.setItem('orange-theme', dark ? 'dark' : 'light'); } catch {}
      update();
    });
  });

  // Keep content visible without JS and finish decorative entrances on first interaction.
  const candidates = Array.from(document.querySelectorAll(
    '.dashboard-title,.editor-title,.simple-analytics-heading,.stat-grid,.post-table,' +
    '.simple-metrics,.simple-panel,.visitor-directory,.upload-panel,.media-grid,' +
    '.trash-stats,.trash-list,.settings-form,.editor-form,.login-card'
  ));
  const visible = candidates.filter(node => {
    if (candidates.some(parent => parent !== node && parent.contains(node))) return false;
    const bounds = node.getBoundingClientRect();
    return bounds.width > 0 && bounds.height > 0 && bounds.top < innerHeight && bounds.bottom > 0;
  }).slice(0, 8);
  visible.forEach((node, index) => {
    const animation = play(node, [{opacity: .45, transform: 'translateY(8px)'}, {opacity: 1, transform: 'translateY(0)'}], {
      duration: 300, delay: Math.min(index * 28, 112), fill: 'backwards'
    });
    if (animation) entrances.add(animation);
  });

  const uploads = document.querySelector('.editor-image-uploads');
  if (uploads) new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) {
      if (node.nodeType === 1 && node.matches('.editor-image-upload')) {
        play(node, [{opacity: .4, transform: 'translateY(6px)'}, {opacity: 1, transform: 'translateY(0)'}], {duration: 220});
      }
    }
  }).observe(uploads, {childList: true});
  const saveState = document.querySelector('[data-form-state]');
  if (saveState) new MutationObserver(() => {
    if (saveState.dataset.state === 'saved') play(saveState, [{opacity: .55}, {opacity: 1}], {duration: 180});
  }).observe(saveState, {attributes: true, attributeFilter: ['data-state']});

  const dialog = document.getElementById('admin-confirm-dialog');
  if (!dialog) return;
  const confirm = dialog.querySelector('[data-confirm-submit]');
  const cancel = dialog.querySelector('[data-confirm-cancel]');
  const approved = new WeakSet();
  let pending = null, previousFocus = null, dialogMotion = null, closing = null;
  const closeDialog = () => {
    if (closing) return closing;
    if (!dialog.open) return Promise.resolve();
    // Reverse from the visible frame, even if the entrance has not finished.
    const current = getComputedStyle(dialog);
    const from = {opacity: current.opacity, transform: current.transform};
    dialogMotion?.cancel();
    dialog.classList.add('is-closing');
    dialogMotion = play(dialog, [from, {opacity: 0, transform: 'translateY(5px) scale(.985)'}], {
      duration: 140, easing: 'cubic-bezier(.4,0,1,1)', fill: 'forwards'
    });
    closing = Promise.resolve(dialogMotion?.finished).catch(() => {}).then(() => {
      dialog.close();
      dialogMotion?.cancel();
      closing = null;
    });
    return closing;
  };
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!form.matches('form[data-admin-confirm]')) return;
    if (approved.has(form)) { approved.delete(form); return; }
    event.preventDefault();
    if (dialog.open || closing) return;
    pending = {form, submitter: event.submitter};
    previousFocus = event.submitter || document.activeElement;
    dialog.querySelector('#admin-confirm-title').textContent = form.dataset.confirmTitle || '确认操作';
    dialog.querySelector('#admin-confirm-message').textContent = form.dataset.confirmMessage || '请确认是否继续。';
    const name = dialog.querySelector('[data-confirm-item-name]');
    name.textContent = form.dataset.confirmItem || '';
    name.hidden = !name.textContent;
    confirm.textContent = form.dataset.confirmLabel || '确认';
    confirm.disabled = false;
    dialog.classList.remove('is-closing');
    dialog.showModal();
    root.classList.add('admin-confirm-open');
    cancel.focus({preventScroll: true});
    dialogMotion?.cancel();
    dialogMotion = play(dialog, [
      {opacity: 0, transform: 'translateY(10px) scale(.98)'},
      {opacity: 1, transform: 'translateY(0) scale(1)'}
    ], {duration: 240});
  });
  cancel.addEventListener('click', () => { pending = null; closeDialog(); });
  dialog.addEventListener('cancel', event => { event.preventDefault(); pending = null; closeDialog(); });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
      pending = null;
      closeDialog();
    }
  });
  dialog.addEventListener('close', () => {
    pending = null;
    dialog.classList.remove('is-closing');
    root.classList.remove('admin-confirm-open');
    if (previousFocus?.isConnected) previousFocus.focus({preventScroll: true});
  });
  confirm.addEventListener('click', async () => {
    if (!pending || closing) return;
    const intent = pending;
    pending = null;
    confirm.disabled = true;
    await closeDialog();
    if (!intent.form.isConnected) return;
    approved.add(intent.form);
    if (intent.submitter?.form === intent.form) intent.form.requestSubmit(intent.submitter);
    else intent.form.requestSubmit();
  });
})();
