(() => {
  const toast = document.querySelector('[data-admin-toast]');
  let toastTimer;
  const showToast = (message) => {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('is-visible');
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 1800);
  };

  document.querySelectorAll('[data-flash]').forEach((flash) => {
    window.setTimeout(() => {
      flash.classList.add('is-leaving');
      window.setTimeout(() => flash.remove(), 160);
    }, 4200);
  });

  const form = document.querySelector('[data-editor-form]');
  const content = document.querySelector('[data-content]');
  const count = document.querySelector('[data-word-count]');
  const readTime = document.querySelector('[data-read-time]');
  const imageUploads = new Set();
  if (form && content) {
    const format = form.elements.namedItem('content_format');
    const latexTools = form.querySelector('[data-latex-tools]');
    const example = form.querySelector('[data-latex-example]');
    const formatHint = form.querySelector('[data-format-hint]');
    const updateFormat = () => {
      const latex = format?.value === 'latex';
      content.dataset.format = latex ? 'latex' : 'markdown';
      if (latexTools) latexTools.hidden = !latex;
      if (example) example.disabled = !!content.value.trim();
      if (formatHint) formatHint.hidden = latex || !/\\(?:documentclass|begin\s*\{document\}|section\*?\s*\{)/.test(content.value);
      const help = form.querySelector('[data-content-help]');
      if (help) help.textContent = latex && form.hasAttribute('data-project-editor') ? '支持 LaTeX 章节、列表、公式和图片；未完成的源码可关闭公开展示后保存' : latex ? '可输入 LaTeX 正文或完整 document 环境；Ctrl+V 截图会插入 LaTeX 图片命令，未完成的源码可先存草稿' : '支持 Markdown、表格、代码块；可直接 Ctrl+V 粘贴截图，自动上传并插入图片链接';
    };
    updateFormat();
    format?.addEventListener('change', updateFormat);
    form.querySelector('[data-switch-latex]')?.addEventListener('click', () => {
      if (!format) return;
      format.value = 'latex';
      format.dispatchEvent(new Event('change', {bubbles: true}));
      content.focus();
    });
    content.addEventListener('input', updateFormat);
    const insertLatex = text => {
      content.focus();
      content.setRangeText(text, content.selectionStart, content.selectionEnd, 'end');
      content.dispatchEvent(new Event('input', {bubbles: true}));
    };
    form.querySelectorAll('[data-latex-insert]').forEach(button => button.addEventListener('click', () => {
      const selected = content.value.slice(content.selectionStart, content.selectionEnd);
      const snippets = {
        section: '\\section{' + (selected || '章节标题') + '}\n',
        math: '\n\\[\n' + (selected || '\\frac{a}{b} = \\sum_{i=1}^{n} x_i') + '\n\\]\n',
        list: '\n\\begin{itemize}\n  \\item ' + (selected || '第一项') + '\n  \\item 第二项\n\\end{itemize}\n',
      };
      insertLatex(snippets[button.dataset.latexInsert]);
    }));
    example?.addEventListener('click', () => {
      if (content.value.trim()) return;
      insertLatex('\\section{开始写作}\n\n这里填写正文。行内公式 $E=mc^2$。\n\n\\subsection{公式示例}\n\\[\n  \\int_0^1 x^2\\,dx = \\frac{1}{3}\n\\]\n\n\\begin{itemize}\n  \\item 第一项\n  \\item 第二项\n\\end{itemize}\n');
    });
    form.querySelector('[data-latex-download]')?.addEventListener('click', () => {
      const url = URL.createObjectURL(new Blob([content.value], {type: 'application/x-tex;charset=utf-8'}));
      const link = document.createElement('a');link.href = url;link.download = form.hasAttribute('data-project-editor') ? 'project.tex' : 'article.tex';link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    const update = () => {
      const text = content.value.trim();
      const chinese = (text.match(/[\u4e00-\u9fff]/g) || []).length;
      const words = (text.match(/[A-Za-z0-9]+/g) || []).length;
      const total = chinese + words;
      count.textContent = total.toLocaleString();
      readTime.textContent = Math.max(1, Math.round((chinese + words * 1.8) / 300));
    };
    update();
    content.addEventListener('input', update);
    const uploadPanel = document.createElement('div');
    uploadPanel.className = 'editor-image-uploads';
    uploadPanel.setAttribute('aria-label', '截图上传');
    content.closest('label').after(uploadPanel);
    let previousText = content.value;
    let insertingImage = false;
    let uploadQueue = Promise.resolve();
    // Track insertion points through edits so upload completion cannot replace new typing.
    content.addEventListener('input', () => {
      const next = content.value;
      let start = 0;
      while (start < previousText.length && start < next.length && previousText[start] === next[start]) start++;
      let end = previousText.length, nextEnd = next.length;
      while (end > start && nextEnd > start && previousText[end - 1] === next[nextEnd - 1]) { end--; nextEnd--; }
      for (const job of imageUploads) {
        if (job.offset < start || (job.offset === start && !insertingImage)) continue;
        job.offset = job.offset >= end ? job.offset + nextEnd - end : nextEnd;
      }
      previousText = next;
    });
    const uploadImage = async job => {
      if (job.removed) return;
      job.controller = new AbortController();
      job.status.textContent = '正在上传截图…';
      job.retry.hidden = true;
      const timeout = window.setTimeout(() => job.controller.abort(), 30000);
      try {
        const data = new FormData();
        data.set('csrf_token', form.elements.namedItem('csrf_token')?.value || '');
        data.set('file', job.file, job.filename);
        const response = await fetch(form.dataset.imageUploadUrl, {
          method: 'POST', body: data, credentials: 'same-origin', signal: job.controller.signal,
          headers: {'X-Orange-Upload': '1', 'Accept': 'application/json'}
        });
        if (response.status === 413) throw new Error('截图超过上传大小限制，请压缩后重新粘贴。');
        if (response.status === 400) throw new Error('表单已失效，请先备份正文，再重新打开编辑页。');
        if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('上传未完成，请检查网络或登录状态后重试。');
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.message || '上传失败，请重试。');
        if (typeof result.url !== 'string' || !/^\/uploads\/[A-Za-z0-9._-]+$/.test(result.url)) throw new Error('服务器返回了无效图片地址，请重试。');
        if (job.removed) return;
        imageUploads.delete(job);
        content.dispatchEvent(new CustomEvent('orange-image-ready', {detail: {url: result.url, file: job.file}}));
        const scroll = content.scrollTop;
        const markdown = format?.value === 'latex' ? '\n\n\\includegraphics{' + result.url + '}\n\n' : '\n\n![截图](' + result.url + ')\n\n';
        const offset = Math.min(job.offset, content.value.length);
        const atInsertion = content.selectionStart === offset && content.selectionEnd === offset;
        content.setRangeText(markdown, offset, offset, 'preserve');
        if (atInsertion) content.setSelectionRange(offset + markdown.length, offset + markdown.length);
        insertingImage = true;
        try { content.dispatchEvent(new Event('input', {bubbles: true})); }
        finally { insertingImage = false; }
        content.scrollTop = scroll;
        job.status.closest('.editor-image-upload').remove();
        URL.revokeObjectURL(job.previewUrl);
      } catch (error) {
        if (job.removed) return;
        job.status.textContent = (error.name === 'AbortError' ? '上传超时。' : error instanceof TypeError ? '网络异常，截图未插入。' : error.message) + '截图仍保留在当前页，可重试或下载。';
        job.retry.hidden = false;
        showToast('截图上传未完成，正文保持不变');
      } finally {
        window.clearTimeout(timeout);
      }
    };
    content.addEventListener('paste', event => {
      const clipboard = event.clipboardData;
      if (!clipboard) return;
      let images = Array.from(clipboard.items || []).filter(item => item.kind === 'file' && item.type.startsWith('image/')).map(item => item.getAsFile()).filter(Boolean);
      if (!images.length) images = Array.from(clipboard.files || []).filter(file => file.type.startsWith('image/'));
      if (!images.length) return; // Plain text keeps the browser's native paste behavior.
      event.preventDefault();
      const extensions = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'};
      for (const file of images) {
        const extension = extensions[file.type];
        if (!extension || file.size >= 8 * 1024 * 1024) {
          showToast(!extension ? '请粘贴 PNG、JPG、GIF 或 WebP 图片' : '截图过大，请压缩到 8 MB 以下再粘贴');
          continue;
        }
        const row = document.createElement('div');
        row.className = 'editor-image-upload';
        row.tabIndex = 0;
        row.addEventListener('keydown', event => {
          if (event.target === row && (event.key === 'Delete' || event.key === 'Backspace')) {
            event.preventDefault();row.querySelector('[data-image-remove]').click();
          }
        });
        row.innerHTML = '<img alt="待上传截图预览"><p role="status" aria-live="polite"></p><div><button type="button" class="secondary-button" data-image-retry hidden>重试上传</button><button type="button" class="secondary-button" data-image-download>下载截图</button><button type="button" class="secondary-button" data-image-remove hidden>移除此截图</button></div>';
        const previewUrl = URL.createObjectURL(file);
        row.querySelector('img').src = previewUrl;
        const job = {file, previewUrl, filename: 'screenshot-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8) + '.' + extension, offset: content.selectionStart, status: row.querySelector('p'), retry: row.querySelector('[data-image-retry]'), remove: row.querySelector('[data-image-remove]')};
        job.status.textContent = '截图等待上传…';
        job.retry.addEventListener('click', () => {
          job.retry.hidden = true;
          job.status.textContent = '等待重新上传…';
          uploadQueue = uploadQueue.then(() => uploadImage(job));
        });
        job.remove.addEventListener('click', () => {
          job.removed = true;
          job.controller?.abort();
          imageUploads.delete(job);
          URL.revokeObjectURL(previewUrl);
          row.remove();
        });
        row.querySelector('[data-image-download]').addEventListener('click', () => {
          const link = document.createElement('a');
          link.href = previewUrl;
          link.download = job.filename;
          link.click();
        });
        imageUploads.add(job);
        uploadPanel.append(row);
        uploadQueue = uploadQueue.then(() => uploadImage(job));
      }
    });
    document.addEventListener('keydown', (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        form.requestSubmit(form.querySelector('.save-button'));
      }
    });
  }

  let dirtyForm = null;
  document.querySelectorAll('[data-track-changes]').forEach((trackedForm) => {
    const state = document.querySelector('[data-form-state]');
    let dirty = false;
    const setState = (value, label) => {
      if (!state) return;
      state.dataset.state = value;
      state.textContent = label;
    };
    const editor = trackedForm.matches('[data-editor-form]');
    const fields = trackedForm.hasAttribute('data-project-editor') ? ['title', 'summary', 'content', 'slug', 'year', 'stack', 'status', 'link', 'sort_order', 'is_featured', 'is_visible', 'content_format'] : ['title', 'excerpt', 'content', 'slug', 'category', 'tags', 'status', 'is_featured', 'content_format'];
    const snapshot = () => Object.fromEntries(fields.map(name => {
      const node = trackedForm.elements.namedItem(name);
      return [name, node?.type === 'checkbox' ? (node.checked ? '1' : '') : (node?.value || (name === 'content_format' ? 'markdown' : ''))];
    }));
    const equal = (a, b) => fields.every(name => a[name] === b[name]);
    const prefix = path => 'orange-editor-draft:v1:' + path + ':';
    const newDraftId = () => window.crypto?.randomUUID?.() || Date.now() + '-' + Math.random().toString(36).slice(2);
    let draftId = newDraftId();
    let draftKey = prefix(location.pathname) + draftId;
    let draftStored = false;
    let storageFailed = false;
    const recoveredKeys = new Set();
    let draftStatus;
    let recovery;
    const ownedKeys = new Set([draftKey]);
    const removeDraft = key => {
      try { localStorage.removeItem(key); } catch { /* Retain a recoverable copy if storage is unavailable. */ }
    };
    const persistDraft = () => {
      if (!editor) return false;
      try {
        const nextKey = prefix(new URL(trackedForm.action).pathname) + draftId;
        localStorage.setItem(nextKey, JSON.stringify({version: 1, fields: snapshot(), updatedAt: Date.now()}));
        ownedKeys.add(nextKey);
        if (nextKey !== draftKey) removeDraft(draftKey);
        draftKey = nextKey;
        draftStored = true;
        storageFailed = false;
        draftStatus.textContent = '本机草稿已备份，尚未确认保存到服务器';
        return true;
      } catch {
        draftStored = false;
        storageFailed = true;
        draftStatus.textContent = '本机备份失败，请保持页面打开并下载草稿备份';
        return false;
      }
    };
    if (editor) {
      const panel = document.createElement('section');
      panel.className = 'editor-draft-panel';
      panel.setAttribute('aria-label', '草稿保护');
      panel.innerHTML = '<p data-draft-status role="status" aria-live="polite">编辑时自动备份到本机浏览器</p><button type="button" class="secondary-button" data-draft-download>下载草稿备份</button><div data-draft-recovery hidden><label>发现本机未保存草稿<select data-draft-choice></select></label><div><button type="button" class="secondary-button" data-draft-restore>恢复所选草稿</button><button type="button" class="secondary-button" data-draft-discard>丢弃所选草稿</button></div></div>';
      trackedForm.prepend(panel);
      draftStatus = panel.querySelector('[data-draft-status]');
      recovery = panel.querySelector('[data-draft-recovery]');
      const choice = panel.querySelector('[data-draft-choice]');
      let candidates = [];
      const serverFields = snapshot();
      try {
        for (const key of Object.keys(localStorage)) {
          if (!key.startsWith(prefix(location.pathname))) continue;
          let saved;
          try { saved = JSON.parse(localStorage.getItem(key)); } catch { continue; }
          if (saved?.version !== 1 || !saved.fields || !Number.isFinite(saved.updatedAt)) continue;
          if (saved.fields.content_format === undefined) saved.fields.content_format = 'markdown';
          if (!fields.every(name => typeof saved.fields[name] === 'string')) continue;
          if (equal(saved.fields, serverFields)) { removeDraft(key); continue; }
          candidates.push({key, ...saved});
        }
        candidates.sort((a, b) => b.updatedAt - a.updatedAt);
      } catch {
        storageFailed = true;
        draftStatus.textContent = '浏览器无法读取本机草稿，请下载备份并保持页面打开';
      }
      const showRecovery = () => {
        choice.replaceChildren();
        for (const candidate of candidates) {
          const option = document.createElement('option');
          option.value = candidate.key;
          option.textContent = new Date(candidate.updatedAt).toLocaleString() + ' · ' + (candidate.fields.title || '未命名文章').slice(0, 60);
          choice.append(option);
        }
        recovery.hidden = !candidates.length;
      };
      showRecovery();
      panel.querySelector('[data-draft-restore]').addEventListener('click', () => {
        const candidate = candidates.find(item => item.key === choice.value);
        if (!candidate) return;
        // Preserve this tab's current edits before explicitly restoring another version.
        if (dirty && !persistDraft()) return;
        if (dirty) {
          candidates.push({key: draftKey, version: 1, fields: snapshot(), updatedAt: Date.now()});
          ownedKeys.delete(draftKey);
          draftId = newDraftId();
          draftKey = prefix(location.pathname) + draftId;
        }
        for (const name of fields) {
          const node = trackedForm.elements.namedItem(name);
          if (!node) continue;
          if (node.type === 'checkbox') node.checked = candidate.fields[name] === '1';
          else node.value = candidate.fields[name];
        }
        recoveredKeys.add(candidate.key);
        candidates = candidates.filter(item => item !== candidate);
        showRecovery();
        content.dispatchEvent(new Event('input', {bubbles: true}));
        setState('dirty', '已恢复本机草稿，请点击保存');
        draftStatus.textContent = storageFailed ? '本机备份失败，请先下载草稿' : '已恢复本机草稿，尚未保存到服务器';
      });
      panel.querySelector('[data-draft-discard]').addEventListener('click', () => {
        const key = choice.value;
        removeDraft(key);
        candidates = candidates.filter(item => item.key !== key);
        showRecovery();
      });
      panel.querySelector('[data-draft-download]').addEventListener('click', () => {
        const current = snapshot();
        const blob = new Blob([JSON.stringify({version: 1, fields: current, updatedAt: Date.now()}, null, 2), '\n\n', current.content], {type: 'text/plain;charset=utf-8'});
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = 'orange-draft-' + new Date().toISOString().replace(/[:.]/g, '-') + '.txt';
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
      const backupOnExit = () => { if (dirty) persistDraft(); };
      window.addEventListener('pagehide', backupOnExit);
      document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') backupOnExit(); });
      window.addEventListener('offline', () => {
        if (dirty) persistDraft();
        if (dirty) setState('dirty', '网络已断开，内容尚未保存到服务器');
      });
      window.addEventListener('online', () => {
        if (dirty) setState('dirty', '网络已恢复，请再次点击保存');
      });
    }
    const isFieldEvent = event => !editor || fields.includes(event.target.name);
    trackedForm.addEventListener('input', (event) => {
      if (!isFieldEvent(event)) return;
      dirty = true;
      dirtyForm = trackedForm;
      setState('dirty', '有未保存更改');
      persistDraft();
    });
    trackedForm.addEventListener('change', (event) => {
      if (!isFieldEvent(event)) return;
      dirty = true;
      dirtyForm = trackedForm;
      setState('dirty', '有未保存更改');
      persistDraft();
    });
    let saving = false;
    let revision = 0;
    trackedForm.addEventListener('input', event => { if (isFieldEvent(event)) revision++; });
    trackedForm.addEventListener('change', event => { if (isFieldEvent(event)) revision++; });
    trackedForm.addEventListener('submit', async (event) => {
      const target = event.submitter?.getAttribute('formtarget');
      if (target === '_blank') {
        if (imageUploads.size) {
          event.preventDefault();
          showToast('截图尚未上传完成，请重试或移除后再预览');
        }
        return;
      }
      if (!trackedForm.matches('[data-editor-form]')) {
        dirty = false;
        dirtyForm = null;
        setState('saving', '正在保存…');
        return;
      }
      event.preventDefault();
      if (saving) return;
      if (imageUploads.size) {
        setState('dirty', '请先完成截图上传，或移除未上传的截图后保存');
        showToast('还有截图未上传完成，可重试或下载截图');
        return;
      }
      saving = true;
      const savedRevision = revision;
      const submittedSlug = trackedForm.elements.namedItem('slug')?.value;
      const button = trackedForm.querySelector('.save-button');
      button.disabled = true;
      persistDraft();
      setState('saving', '正在保存…');
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 20000);
      try {
        if(trackedForm.hasAttribute('data-project-editor') && trackedForm.elements.namedItem('content_format')?.value==='latex' && window.orangeProjectLatexFlush) await window.orangeProjectLatexFlush();
        const response = await fetch(trackedForm.action, {
          method: 'POST', body: new FormData(trackedForm),
          headers: {'X-Orange-Save': '1', 'Accept': 'application/json'},
          credentials: 'same-origin', signal: controller.signal
        });
        const type = response.headers.get('content-type') || '';
        if (!type.includes('application/json')) throw new Error('保存未完成，请检查登录状态后重试。');
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.message || '保存失败，请重试。');
        // The DOM remains untouched: focus, selection and both scroll positions stay put.
        const slugField = trackedForm.elements.namedItem('slug');
        if (slugField && slugField.value === submittedSlug && typeof result.slug === 'string') slugField.value = result.slug;
        trackedForm.action = result.edit_url;
        history.replaceState(history.state, '', result.edit_url);
        dirty = revision !== savedRevision;
        dirtyForm = dirty ? trackedForm : null;
        if (dirty) {
          persistDraft();
        } else {
          ownedKeys.forEach(removeDraft);
          draftStored = false;
          draftStatus.textContent = '已确认保存到服务器';
        }
        recoveredKeys.forEach(removeDraft);
        recoveredKeys.clear();
        setState(dirty ? 'dirty' : 'saved', dirty ? '已保存，仍有新更改待保存' : '已保存 · ' + new Date().toLocaleTimeString());
        showToast(dirty ? '已保存提交时的内容，请继续保存新更改' : (trackedForm.hasAttribute('data-project-editor') ? '项目已保存' : '文章已保存'));
      } catch (error) {
        dirty = true;
        dirtyForm = trackedForm;
        persistDraft();
        const failure = error.name === 'AbortError' ? '保存超时，未收到服务器确认。' : error instanceof TypeError ? '网络异常，未收到保存确认。' : error.message || '保存失败。';
        const message = failure + (draftStored ? '内容已保留在本机草稿，可恢复后重试。' : '请保持页面打开，并下载草稿备份。');
        setState('dirty', message);
        showToast(message);
      } finally {
        window.clearTimeout(timeout);
        saving = false;
        button.disabled = false;
      }
    });
    trackedForm.dataset.hasDirtyState = 'true';
  });

  window.addEventListener('beforeunload', (event) => {
    if (!dirtyForm || dirtyForm.matches('[data-editor-form]')) return;
    event.preventDefault();
    event.returnValue = '';
  });

  document.querySelectorAll('[data-copy-markdown]').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copyMarkdown);
        showToast('Markdown 已复制');
      } catch {
        showToast('复制失败，请手动复制');
      }
    });
  });

  document.querySelectorAll('[data-bulk-toolbar]').forEach((toolbar) => {
    const formId = toolbar.dataset.bulkToolbar;
    const bulkForm = document.getElementById(formId);
    const selectAll = toolbar.querySelector('[data-select-all]');
    const countLabel = toolbar.querySelector('.bulk-count');
    const submitButton = toolbar.querySelector('[data-bulk-submit]');
    const items = Array.from(document.querySelectorAll(`[data-bulk-item][form="${formId}"]`));
    if (!bulkForm || !selectAll || !countLabel || !submitButton || !items.length) return;

    const updateSelection = () => {
      const selectedCount = items.filter((item) => item.checked).length;
      selectAll.checked = selectedCount === items.length;
      selectAll.indeterminate = selectedCount > 0 && selectedCount < items.length;
      countLabel.textContent = `已选择 ${selectedCount} 项`;
      submitButton.disabled = selectedCount === 0;
      toolbar.classList.toggle('has-selection', selectedCount > 0);
    };

    selectAll.addEventListener('change', () => {
      items.forEach((item) => {
        item.checked = selectAll.checked;
      });
      updateSelection();
    });
    items.forEach((item) => item.addEventListener('change', updateSelection));

    bulkForm.addEventListener('submit', (event) => {
      const selectedCount = items.filter((item) => item.checked).length;
      if (!selectedCount) {
        event.preventDefault();
        return;
      }
      const label = bulkForm.dataset.itemLabel || '项内容';
      const warning = bulkForm.dataset.bulkWarning || '删除后可以在回收站恢复。';
      if (!window.confirm(`确定删除选中的 ${selectedCount} ${label}吗？\n${warning}`)) {
        event.preventDefault();
      }
    });
    updateSelection();
  });
})();
