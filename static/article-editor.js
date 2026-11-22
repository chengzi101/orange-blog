(() => {
  const form = document.querySelector('[data-editor-form]');
  const cover = form?.querySelector('[data-cover-editor]');
  if (!cover) return;
  const field = form.elements.namedItem('cover_image');
  const position = form.elements.namedItem('cover_position');
  const positioning = cover.querySelector('[data-cover-position]');
  const xSlider = cover.querySelector('[data-cover-x]');
  const ySlider = cover.querySelector('[data-cover-y]');
  const listImage = cover.querySelector('[data-cover-list-preview]');
  const frame = cover.querySelector('.editor-cover-preview');
  let drag = null;
  const coordinates = () => /^([0-9]{1,3})% ([0-9]{1,3})%$/.exec(position.value)?.slice(1).map(Number) || [50, 50];
  const renderPosition = () => {
    const [x, y] = coordinates();
    image.style.objectPosition = listImage.style.objectPosition = `${x}% ${y}%`;
    xSlider.value = x; ySlider.value = y;
    cover.querySelector('[data-cover-x-value]').value = `${x}%`;
    cover.querySelector('[data-cover-y-value]').value = `${y}%`;
  };
  const setPosition = (x, y) => {
    const value = `${Math.max(0, Math.min(100, Math.round(x)))}% ${Math.max(0, Math.min(100, Math.round(y)))}%`;
    if (position.value === value) return;
    position.value = value; renderPosition();
    position.dispatchEvent(new Event('input', {bubbles: true}));
  };
  const source = form.querySelector('[data-content]');
  const fileInput = cover.querySelector('[data-cover-file]');
  const image = cover.querySelector('[data-cover-preview]');
  const empty = cover.querySelector('[data-cover-empty]');
  const status = cover.querySelector('[data-cover-status]');
  const choose = cover.querySelector('[data-cover-choose]');
  let controller = null;
  let generation = 0;
  const update = () => {
    const selected = field.value;
    const first = /!\[[^\]]*\]\(([^\s)]+)/.exec(source.value)?.[1] || '';
    const automatic = first.startsWith('/') && !first.startsWith('//') || first.startsWith('https://') ? first : '';
    const url = selected === 'none' ? '' : selected || automatic;
    positioning.hidden = !url;
    image.hidden = !url;
    listImage.hidden = !url;
    if (url) listImage.src = url;
    else listImage.removeAttribute('src');
    renderPosition();
    image.hidden = !url;
    if (url) image.src = url;
    else image.removeAttribute('src');
    empty.hidden = !!url;
    empty.textContent = selected === 'none' ? '这篇文章不显示封面' : '选择一张图片作为文章封面';
    status.textContent = selected === 'none' ? '已关闭封面显示' : selected ? '已选择独立封面，保存文章后生效' : '自动使用正文中的第一张图片';
    choose.textContent = selected && selected !== 'none' ? '更换封面图' : '上传封面图';
    cover.querySelector('[data-cover-auto]').disabled = selected === '';
    cover.querySelector('[data-cover-remove]').disabled = selected === 'none';
  };
  const cancel = () => {
    generation++;
    controller?.abort();
    controller = null;
    form.dataset.coverUploading = '0';
    cover.removeAttribute('aria-busy');
  };
  const select = value => {
    cancel();
    field.value = value;
    update();
    field.dispatchEvent(new Event('input', {bubbles: true}));
  };
  xSlider.addEventListener('input', () => setPosition(+xSlider.value, +ySlider.value));
  ySlider.addEventListener('input', () => setPosition(+xSlider.value, +ySlider.value));
  cover.querySelector('[data-cover-center]').addEventListener('click', () => setPosition(50, 50));
  frame.addEventListener('pointerdown', event => {
    if (event.button !== 0 || image.hidden || !image.naturalWidth) return;
    const rect = image.getBoundingClientRect();
    const scale = Math.max(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
    drag = {id: event.pointerId, startX: event.clientX, startY: event.clientY, position: coordinates(), overflowX: image.naturalWidth * scale - rect.width, overflowY: image.naturalHeight * scale - rect.height};
    frame.setPointerCapture(event.pointerId);
    frame.classList.add('is-dragging');
    event.preventDefault();
  });
  frame.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    setPosition(drag.position[0] - (drag.overflowX > 1 ? (event.clientX - drag.startX) * 100 / drag.overflowX : 0), drag.position[1] - (drag.overflowY > 1 ? (event.clientY - drag.startY) * 100 / drag.overflowY : 0));
  });
  const endDrag = () => { drag = null; frame.classList.remove('is-dragging'); };
  frame.addEventListener('lostpointercapture', endDrag);
  frame.addEventListener('pointerup', endDrag);
  frame.addEventListener('pointercancel', endDrag);
  choose.addEventListener('click', () => fileInput.click());
  cover.querySelector('[data-cover-auto]').addEventListener('click', () => select(''));
  cover.querySelector('[data-cover-remove]').addEventListener('click', () => select('none'));
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files?.[0];
    fileInput.value = '';
    if (!file) return;
    if (!/\.(png|jpe?g|gif|webp)$/i.test(file.name) || !/^image\/(png|jpeg|gif|webp)$/.test(file.type)) {
      status.textContent = '请选择 PNG、JPG、GIF 或 WebP 图片';
      return;
    }
    if (file.size >= 8 * 1024 * 1024) {
      status.textContent = '图片需小于 8 MB，请压缩后重试';
      return;
    }
    cancel();
    const current = generation;
    controller = new AbortController();
    const signal = controller.signal;
    form.dataset.coverUploading = '1';
    cover.setAttribute('aria-busy', 'true');
    status.textContent = '正在上传封面图…';
    const timeout = window.setTimeout(() => controller?.abort(), 30000);
    try {
      const data = new FormData();
      data.set('csrf_token', form.elements.namedItem('csrf_token')?.value || '');
      data.set('file', file, file.name);
      const response = await fetch(form.dataset.imageUploadUrl, {
        method: 'POST', body: data, credentials: 'same-origin', signal,
        headers: {'X-Orange-Upload': '1', 'Accept': 'application/json'},
      });
      if (response.status === 413) throw new Error('图片过大，请压缩后重试');
      if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('上传未完成，请检查登录状态后重试');
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.message || '封面上传失败，请重试');
      if (!/^\/uploads\/[A-Za-z0-9._-]+\.(png|jpe?g|gif|webp)$/i.test(result.url)) throw new Error('图片地址无效，请重试');
      if (current !== generation) return;
      select(result.url);
    } catch (error) {
      if (current !== generation) return;
      update();
      status.textContent = error.name === 'AbortError' ? '封面上传超时，请重试' : error instanceof TypeError ? '网络异常，请重试上传封面' : error.message;
    } finally {
      window.clearTimeout(timeout);
      if (current === generation) {
        controller = null;
        form.dataset.coverUploading = '0';
        cover.removeAttribute('aria-busy');
      }
    }
  });
  form.addEventListener('orange-draft-restored', () => { cancel();update(); });
  source.addEventListener('input', () => { if (!Number(form.dataset.coverUploading)) update(); });
  form.querySelector('[data-insert-code]')?.addEventListener('click', () => {
    const start = source.selectionStart, end = source.selectionEnd;
    const snippet = '\n\n```cpp\n' + (source.value.slice(start, end) || '// 在这里输入代码') + '\n```\n\n';
    source.setRangeText(snippet, start, end, 'end');
    source.setSelectionRange(start + 9, start + snippet.length - 6);
    source.dispatchEvent(new Event('input', {bubbles: true}));
  });
  const format = form.elements.namedItem('content_format');
  const codeTools = form.querySelector('.editor-code-tools');
  const updateFormat = () => { codeTools.hidden = format?.value === 'latex'; };
  format?.addEventListener('change', updateFormat);
  updateFormat();
  update();
})();
