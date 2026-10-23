(() => {
  const form = document.querySelector('[data-editor-form]');
  const source = form?.querySelector('[data-content]');
  if (!source) return;
  const format = form.elements.namedItem('content_format');
  const controls = document.createElement('div');
  controls.className = 'editor-image-mode';
  controls.innerHTML = '<button type="button" class="secondary-button" data-image-visual aria-pressed="true">图文编辑</button><button type="button" class="secondary-button" data-image-source aria-pressed="false">Markdown 源码</button>';
  source.before(controls);
  const view = document.createElement('div');
  view.className = 'editor-inline-images';
  view.setAttribute('aria-label', '正文图文编辑');
  source.after(view);
  let visual = true, syncing = false, pieces = [];
  const freshPictures = new Map();
  source.addEventListener('orange-image-ready', event => {
    const {url, file} = event.detail || {};
    if (typeof url !== 'string' || !(file instanceof Blob)) return;
    const previous = freshPictures.get(url);
    if (previous) URL.revokeObjectURL(previous);
    freshPictures.set(url, URL.createObjectURL(file));
  });
  const images = () => {
    const found = [];
    let offset = 0, fence = null;
    for (const line of source.value.split('\n')) {
      const marker = /^ {0,3}(`{3,}|~{3,})/.exec(line);
      if (marker) {
        if (!fence) fence = marker[1];
        else if (marker[1][0] === fence[0] && marker[1].length >= fence.length && /^ {0,3}(?:`+|~+)\s*$/.test(line)) fence = null;
      } else if (!fence) {
        // Match inline pictures as well as standalone ones, while leaving code literal.
        const tokens = /(`+).*?\1|!\[([^\]\n]*)\]\(<?(\/uploads\/[A-Za-z0-9._-]+|https?:\/\/[^\s)>]+|\/static\/[^\s)>]+)>?(?:[ \t]+"[^"\n]*")?\)(?:\{width="([1-9]\d|100)%"\})?/g;
        for (const match of line.matchAll(tokens)) {
          if (!match[3]) continue;
          const preceding = line.slice(0, match.index).match(/\\+$/)?.[0].length || 0;
          if (preceding % 2) continue;
          found.push({start: offset + match.index, end: offset + match.index + match[0].length, alt: match[2], url: match[3], width: match[4] ? Number(match[4]) : null, markdown: match[0].replace(/\{width="(?:[1-9]\d|100)%"\}$/, '')});
        }
      }
      offset += line.length + 1;
    }
    return found;
  };
  const resize = node => { node.style.height = 'auto'; node.style.height = Math.max(64, node.scrollHeight) + 'px'; };
  const notify = () => source.dispatchEvent(new Event('input', {bubbles: true}));
  const imageUndo = [], imageRedo = [];
  const deleteImage = index => {
    const item = images()[index];
    if (!item) return;
    const before = source.value;
    source.setRangeText('', item.start, item.end, 'end');
    imageUndo.push({before, after: source.value, position: item.start});
    if (imageUndo.length > 50) imageUndo.shift();
    imageRedo.length = 0;
    notify();
  };
  document.addEventListener('keydown', event => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || !(event.target === source || view.contains(event.target))) return;
    const key = event.key.toLowerCase();
    const redo = key === 'y' || (key === 'z' && event.shiftKey);
    if (key !== 'z' && !redo) return;
    const stack = redo ? imageRedo : imageUndo;
    const entry = stack[stack.length - 1];
    if (!entry || source.value !== (redo ? entry.before : entry.after)) return;
    event.preventDefault();
    stack.pop();(redo ? imageUndo : imageRedo).push(entry);
    source.value = redo ? entry.after : entry.before;
    source.setSelectionRange(entry.position, entry.position);
    notify();
  });
  const moveCaretToSource = node => source.setSelectionRange(node._start + node.selectionStart, node._start + node.selectionEnd, node.selectionDirection);
  const updateRanges = () => {
    const current = images();
    if (current.length !== pieces.length - 1) { render();return; }
    for (let i = 0; i < pieces.length; i++) {
      pieces[i]._start = i ? current[i - 1].end : 0;
      pieces[i]._end = i < current.length ? current[i].start : source.value.length;
    }
  };
  const render = () => {
    if (syncing) return;
    const isMarkdown = !format || format.value !== 'latex';
    controls.hidden = !isMarkdown;
    controls.querySelector('[data-image-visual]').setAttribute('aria-pressed', String(visual));
    controls.querySelector('[data-image-source]').setAttribute('aria-pressed', String(!visual));
    const matches = images();
    const enabled = isMarkdown && visual && matches.length > 0;
    const focus = document.activeElement === source || view.contains(document.activeElement);
    const start = source.selectionStart, end = source.selectionEnd;
    source.hidden = enabled;
    view.hidden = !enabled;
    if (!enabled) {
      view.replaceChildren();pieces = [];
      if (focus && document.activeElement !== source) { source.focus({preventScroll: true});source.setSelectionRange(start, end); }
      return;
    }
    const scroll = window.scrollY;
    view.replaceChildren();pieces = [];
    let position = 0;
    const addText = finish => {
      const node = document.createElement('textarea');
      node.className = 'editor-image-text';node.rows = 1;
      node.setAttribute('aria-label', '正文文字片段 ' + (pieces.length + 1));
      node.value = source.value.slice(position, finish);
      node._start = position;node._end = finish;
      node.placeholder = pieces.length ? '继续写正文，或 Ctrl+V 粘贴截图…' : '在这里编辑正文…';
      node.spellcheck = source.spellcheck;
      node.addEventListener('input', () => {
        const selectionStart = node.selectionStart, selectionEnd = node.selectionEnd;
        source.setRangeText(node.value, node._start, node._end, 'preserve');
        source.setSelectionRange(node._start + selectionStart, node._start + selectionEnd, node.selectionDirection);
        syncing = true;
        try { notify(); } finally { syncing = false; }
        updateRanges();resize(node);
      });
      for (const type of ['select', 'click', 'keyup', 'focus']) node.addEventListener(type, () => moveCaretToSource(node));
      node.addEventListener('keydown', event => {
        if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || node.selectionStart !== node.selectionEnd) return;
        const index = pieces.indexOf(node);
        if (event.key === 'Backspace' && node.selectionStart === 0 && index > 0) {
          event.preventDefault();deleteImage(index - 1);
        } else if (event.key === 'Delete' && node.selectionStart === node.value.length && index < pieces.length - 1) {
          event.preventDefault();deleteImage(index);
        }
      });
      node.addEventListener('paste', event => {
        const data = event.clipboardData;
        if (!data || !Array.from(data.items || []).some(item => item.kind === 'file' && item.type.startsWith('image/')) && !Array.from(data.files || []).some(file => file.type.startsWith('image/'))) return;
        event.preventDefault();moveCaretToSource(node);
        source.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
      });
      view.append(node);pieces.push(node);resize(node);
    };
    for (const item of matches) {
      addText(item.start);
      const figure = document.createElement('figure');figure.className = 'editor-inline-image';
      const image = document.createElement('img');image.src = freshPictures.get(item.url) || item.url;image.alt = item.alt || '正文图片';
      figure.tabIndex = 0;
      figure.setAttribute('role', 'group');
      figure.setAttribute('aria-label', image.alt);
      figure.addEventListener('click', event => { if (event.target.closest('.editor-image-size-controls')) return; event.preventDefault();figure.focus({preventScroll: true}); });
      figure.addEventListener('keydown', event => {
        if (event.target.closest('.editor-image-size-controls')) return;
        const index = Array.from(view.querySelectorAll('figure')).indexOf(figure);
        if (event.key === 'Delete' || event.key === 'Backspace') {
          event.preventDefault();deleteImage(index);
        } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault();
          const node = pieces[event.key === 'ArrowLeft' ? index : index + 1];
          if (node) { node.focus({preventScroll: true});const caret = event.key === 'ArrowLeft' ? node.value.length : 0;node.setSelectionRange(caret, caret);moveCaretToSource(node); }
        }
      });
      image.addEventListener('error', () => { image.alt = '图片暂时无法加载'; });
      const sizeControls = document.createElement('div');
      sizeControls.className = 'editor-image-size-controls';
      sizeControls.setAttribute('role', 'group');sizeControls.setAttribute('aria-label', '图片大小');
      sizeControls.innerHTML = '<span>图片宽度</span><input type="range" min="10" max="100" step="1" aria-label="图片宽度滑块"><input type="number" min="10" max="100" step="1" inputmode="numeric" aria-label="图片宽度百分比"><span>%</span><output aria-live="polite"></output><button type="button" class="secondary-button" data-image-size-reset>恢复自动</button>';
      const range = sizeControls.querySelector('[type=range]');
      const number = sizeControls.querySelector('[type=number]');
      const output = sizeControls.querySelector('output');
      let sizeBefore = null;
      const displaySize = width => {
        range.value = width || 100;number.value = width || 100;
        output.textContent = width ? width + '%' : '自动';
        image.style.width = width ? width + '%' : '';
        image.style.maxHeight = width ? 'none' : '';
        figure.dataset.imageWidth = width || 'auto';
      };
      const setSize = width => {
        const index = Array.from(view.querySelectorAll('figure')).indexOf(figure);
        const current = images()[index];if (!current) return;
        const replacement = current.markdown + (width ? '{width="' + width + '%"}' : '');
        if (sizeBefore === null) sizeBefore = source.value;
        source.setRangeText(replacement, current.start, current.end, 'preserve');
        syncing = true;try { notify(); } finally { syncing = false; }
        updateRanges();displaySize(width);
      };
      const finishSize = () => {
        if (sizeBefore !== null && sizeBefore !== source.value) {
          imageUndo.push({before: sizeBefore, after: source.value, position: item.start});
          if (imageUndo.length > 50) imageUndo.shift();imageRedo.length = 0;
        }
        sizeBefore = null;
      };
      range.addEventListener('input', () => setSize(Number(range.value)));
      range.addEventListener('change', finishSize);
      number.addEventListener('change', () => {
        const width = Number(number.value);
        if (!Number.isInteger(width) || width < 10 || width > 100) { displaySize(images()[Array.from(view.querySelectorAll('figure')).indexOf(figure)]?.width);return; }
        setSize(width);finishSize();
      });
      sizeControls.querySelector('[data-image-size-reset]').addEventListener('click', () => {setSize(null);finishSize();});
      displaySize(item.width);
      figure.append(image, sizeControls);view.append(figure);position = item.end;
    }
    addText(source.value.length);
    if (focus) {
      const node = pieces.find(piece => start >= piece._start && start <= piece._end) || pieces[pieces.length - 1];
      node.focus({preventScroll: true});node.setSelectionRange(Math.max(0, start - node._start), Math.max(0, Math.min(end - node._start, node.value.length)));
      window.scrollTo(window.scrollX, scroll);
    }
  };
  controls.querySelector('[data-image-visual]').addEventListener('click', () => { visual = true;render(); });
  controls.querySelector('[data-image-source]').addEventListener('click', () => { visual = false;render();source.focus({preventScroll: true}); });
  source.addEventListener('input', render);
  format?.addEventListener('change', render);
  render();
})();
