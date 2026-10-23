(() => {
  const loginDialog = () => document.querySelector('#reader-login-dialog');
  function requireLogin(section, url) {
    const dialog = loginDialog();
    if (!dialog) { location.assign(url || section.dataset.loginUrl); return; }
    dialog.querySelector('[data-reader-login-link]').href = url || section.dataset.loginUrl;
    if (!dialog.open) dialog.showModal();
  }
  function status(section, message, error = false) {
    const node = section.querySelector('[data-reader-status]');
    node.textContent = message;
    node.dataset.error = String(error);
  }
  function link(text, href) {
    const a = document.createElement('a'); a.textContent = text; a.href = href;
    a.dataset.noPjax = ''; return a;
  }
  function syncProjectCounts(section, {likes, comments} = {}) {
    if (!section.matches('[data-project-community]')) return;
    if (likes !== undefined) document.querySelectorAll('[data-project-like-count]').forEach(node => { node.textContent = likes; });
    if (comments !== undefined) document.querySelectorAll('[data-project-comment-count]').forEach(node => { node.textContent = comments; });
  }
  function mountProjectShortcut() {
    const fresh = document.querySelector('#main-content .project-discussion-mobile-jump');
    const mounted = document.querySelector('[data-mounted-project-jump]');
    const tools = document.querySelector('.right-side-tools-container');
    // The reading panel has a transform, which otherwise traps fixed positioning.
    if (fresh && tools) {
      mounted?.remove(); fresh.dataset.mountedProjectJump = ''; tools.append(fresh);
    } else if (!document.querySelector('[data-project-community]')) mounted?.remove();
  }
  document.addEventListener('click', event => {
    const shortcut = event.target.closest('[data-project-discussion-jump]');
    if (!shortcut || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const section = document.querySelector('[data-project-community]');
    if (!section) return;
    event.preventDefault();
    if (location.hash !== '#comments') history.pushState({...history.state,orangeScroll:[scrollX,scrollY],orangeScrollURL:location.pathname+location.search+'#comments'}, '', '#comments');
    section.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
  });
  let sessionRequest;
  async function syncSession() {
    sessionRequest?.abort(); sessionRequest = new AbortController();
    try {
      const response = await fetch('/api/reader/session', {cache: 'no-store', signal: sessionRequest.signal});
      if (!response.ok) return;
      const data = await response.json();
      const next = location.pathname + location.search;
      document.querySelectorAll('[data-reader-account]').forEach(account => {
        if (data.user) {
          const name = document.createElement('span'); name.className = 'reader-nickname';
          name.textContent = name.title = data.user.nickname;
          const profile = link('', '/account'); profile.className = 'reader-profile-link'; profile.title = '个人中心';
          profile.append(name);
          const form = document.createElement('form'); form.method = 'post'; form.action = '/auth/logout';
          for (const [key, value] of Object.entries({csrf_token: data.csrf_token, next})) {
            const input = document.createElement('input'); input.type = 'hidden'; input.name = key; input.value = value;
            if (key === 'csrf_token') input.dataset.readerCsrf = ''; form.append(input);
          }
          const button = document.createElement('button'); button.type = 'submit'; button.textContent = '退出';
          form.append(button); account.replaceChildren(profile, form);
        } else {
          account.replaceChildren(link('登录', '/auth/login?next=' + encodeURIComponent(next)),
            document.createTextNode(' / '), link('注册', '/auth/register?next=' + encodeURIComponent(next)));
        }
      });
      document.querySelectorAll('[data-reader-csrf]').forEach(input => { input.value = data.csrf_token; });
      document.querySelectorAll('[data-delete-comment]').forEach(button => {
        const article = button.closest('[data-comment-id]');
        button.hidden = !data.user || article.dataset.commentDeleted === '1' || (!data.user.is_owner && article.dataset.commentUserId !== String(data.user.id));
      });
      document.querySelectorAll('[data-reader-community]').forEach(section => {
        section.dataset.authenticated = data.user ? '1' : '0';
        const textarea = section.querySelector('.reader-comment-form textarea');
        textarea.readOnly = !data.user; textarea.required = !!data.user;
        textarea.placeholder = data.user ? '写下评论，友好交流……' : '登录后即可发表评论，点击前往登录';
        section.querySelector('[data-comment-identity]').textContent = data.user ? data.user.nickname + ' · 最多 2000 字' : '登录后参与讨论';
      });
    } catch (error) { /* Server-rendered controls remain available when offline. */ }
  }
  function renderComment(comment) {
    const article = document.createElement('article'); article.className = 'reader-comment'; article.dataset.commentId = comment.id;
    article.id = 'comment-' + comment.id;
    article.dataset.commentUserId = comment.user_id;
    article.dataset.commentDeleted = comment.deleted_at ? '1' : '0';
    const avatar = document.createElement('div'); avatar.className = 'reader-comment-avatar'; avatar.setAttribute('aria-hidden', 'true'); avatar.textContent = [...comment.nickname][0];
    if (comment.avatar_url) { const portrait = document.createElement('img'); portrait.src = comment.avatar_url; portrait.alt = ''; portrait.width = 36; portrait.height = 36; avatar.replaceChildren(portrait); }
    const body = document.createElement('div'); body.className = 'reader-comment-body';
    const meta = document.createElement('div'); meta.className = 'reader-comment-meta';
    const name = document.createElement('strong'); name.textContent = comment.nickname;
    const time = document.createElement('time'); time.dateTime = comment.created_at;
    time.textContent = new Date(comment.created_at).toLocaleString('zh-CN', {hour12: false});
    const text = document.createElement('p'); text.textContent = comment.content;
    meta.append(name);
    if (comment.is_owner || comment.custom_title) { const badge = document.createElement('span'); badge.className = 'reader-owner-badge' + (comment.is_owner ? ' is-owner' : ''); badge.textContent = comment.is_owner ? '博主' : comment.custom_title; meta.append(badge); }
    meta.append(time); body.append(meta);
    if (comment.reply_to_nickname) {
      const target = document.createElement('div'); target.className = 'reader-reply-target';
      target.textContent = '回复 @' + comment.reply_to_nickname; body.append(target);
    }
    const reply = document.createElement('button'); reply.type = 'button'; reply.className = 'reader-reply-button';
    reply.dataset.replyTo = comment.id; reply.dataset.replyNickname = comment.nickname; reply.textContent = '回复';
    const actions = document.createElement('div'); actions.className = 'reader-comment-actions';
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'reader-delete-button';
    remove.dataset.deleteComment = comment.id; remove.textContent = '删除'; remove.hidden = !comment.can_delete;
    const like = document.createElement('button'); like.type = 'button'; like.className = 'reader-comment-like';
    like.dataset.commentLike = comment.id; like.setAttribute('aria-pressed', String(comment.liked));
    const label = document.createElement('span'); label.dataset.commentLikeLabel = ''; label.textContent = comment.liked ? '已点赞' : '点赞';
    const count = document.createElement('b'); count.dataset.commentLikeCount = ''; count.textContent = comment.like_count;
    like.append(label, document.createTextNode(' '), count);
    actions.append(reply, like, remove);
    body.append(text);
    if (!comment.deleted_at) body.append(actions);
    if (comment.floor) { const floor = document.createElement('div'); floor.className = 'reader-comment-floor'; floor.textContent = comment.floor + ' 楼'; body.append(floor); }
    article.append(avatar, body); return article;
  }
  async function reloadComments(section, page = section.dataset.commentPage) {
    const endpoint = new URL(section.querySelector('.reader-comment-form').action);
    endpoint.searchParams.set('comments_page', page);
    endpoint.searchParams.set('comments_sort', section.dataset.commentSort || 'newest');
    const response = await fetch(endpoint, {cache: 'no-store'});
    if (!response.ok) throw Error('评论加载失败，请刷新重试。');
    const data = await response.json();
    // Return the form home before replacing a thread that may contain it.
    resetReply(section);
    const list = section.querySelector('[data-comment-list]'); list.replaceChildren();
    for (const comment of data.comments) {
      const thread = document.createElement('div'); thread.className = 'reader-thread'; thread.dataset.threadId = comment.id;
      const replies = document.createElement('div'); replies.className = 'reader-thread-replies'; replies.dataset.threadReplies = '';
      for (const reply of comment.replies) replies.append(renderComment(reply));
      thread.append(renderComment(comment), replies); list.append(thread);
      if (comment.next_reply_cursor) {
        const url = new URL(section.dataset.postUrl, location.origin);
        url.searchParams.set('comments_sort', section.dataset.commentSort || 'newest');
        url.searchParams.set('comments_page', data.page);
        url.searchParams.set('reply_thread', comment.id);
        url.searchParams.set('reply_after', comment.next_reply_cursor);
        url.hash = 'comment-' + comment.id;
        const more = link('更多回复', url.href); more.className = 'reader-reply-button';
        more.dataset.moreReplies = ''; more.dataset.repliesUrl = comment.replies_url; more.dataset.repliesCursor = comment.next_reply_cursor;
        thread.append(more);
      }
    }
    if (!data.comments.length) { const empty = document.createElement('p'); empty.dataset.noComments = ''; empty.textContent = '还没有评论，来留下第一条想法吧。'; list.append(empty); }
    section.dataset.commentPage = data.page;
    section.querySelector('[data-comment-count]').textContent = data.total;
    syncProjectCounts(section, {comments: data.total});
    const pages = section.querySelector('[data-comments-pagination]'); pages.replaceChildren();
    for (const [page, text] of [[data.page - 1, '上一页'], [data.page + 1, '下一页 →']]) {
      if (page < 1 || page > data.pages) continue;
      const url = new URL(section.dataset.postUrl, location.origin); url.searchParams.set('comments_sort', section.dataset.commentSort || 'newest'); url.searchParams.set('comments_page', page); url.hash = 'comments'; pages.append(link(text, url.href));
    }
  }
  let sortURLTimer;
  function rememberSort(section) {
    clearTimeout(sortURLTimer);
    const pageURL = location.href;
    sortURLTimer = setTimeout(() => {
      if (!section.isConnected || location.href !== pageURL) return;
      const url = new URL(pageURL);
      url.searchParams.set('comments_sort', section.dataset.commentSort);
      url.searchParams.delete('comments_page');
      if (url.href === location.href) return;
      try {
        history.replaceState({...history.state, orangeScroll: [scrollX, scrollY], orangeScrollURL: url.href}, '', url.href);
      } catch (error) {
        // URL persistence is optional; browser history limits must not break sorting.
        if (error.name !== 'SecurityError') throw error;
      }
    }, 300);
  }
  document.addEventListener('orange:page', () => clearTimeout(sortURLTimer));
  window.addEventListener('popstate', () => clearTimeout(sortURLTimer));
  document.addEventListener('change', async event => {
    if (!event.target.matches('[data-comment-sort-select]')) return;
    const section = event.target.closest('[data-reader-community]');
    section.dataset.commentSort = event.target.value; event.target.disabled = true;
    try {
      await reloadComments(section, 1);
      rememberSort(section);
    } catch (error) { status(section, error.message, true); }
    finally { event.target.disabled = false; }
  });
  async function likeComment(button) {
    const section = button.closest('[data-reader-community]');
    if (section.dataset.authenticated !== '1') { requireLogin(section); return; }
    if (button.disabled) return;
    button.disabled = true;
    try {
      const data = new FormData(); data.set('csrf_token', section.querySelector('[data-reader-csrf]').value); data.set('liked', button.getAttribute('aria-pressed') === 'true' ? '0' : '1');
      const response = await fetch(section.querySelector('.reader-comment-form').action + '/' + button.dataset.commentLike + '/like', {method: 'POST', body: data, headers: {'Accept': 'application/json'}});
      const result = await response.json();
      if (response.status === 401) { requireLogin(section, result.login_url); syncSession(); }
      if (!response.ok || !result.ok) throw Error(result.message || '点赞失败，请稍后重试。');
      button.setAttribute('aria-pressed', String(result.liked));
      button.querySelector('[data-comment-like-label]').textContent = result.liked ? '已点赞' : '点赞';
      button.querySelector('[data-comment-like-count]').textContent = result.like_count;
      status(section, result.liked ? '评论已点赞。' : '已取消评论点赞。');
      // Reorder after voting only when no reply is being drafted.
      if (section.dataset.commentSort === 'likes' && !section.querySelector('.reader-comment-form textarea').value.trim()) await reloadComments(section);
    } catch (error) { status(section, error.message || '网络异常，请重试。', true); }
    finally { button.disabled = false; }
  }
  function confirmDelete() {
    const dialog = document.querySelector('#reader-delete-dialog');
    return new Promise(resolve => {
      const done = value => {
        dialog.removeEventListener('click', click); dialog.removeEventListener('cancel', cancel);
        dialog.close(); resolve(value);
      };
      const click = event => {
        if (event.target.closest('[data-confirm-delete]')) done(true);
        if (event.target.closest('[data-cancel-delete]')) done(false);
      };
      const cancel = event => { event.preventDefault(); done(false); };
      dialog.addEventListener('click', click); dialog.addEventListener('cancel', cancel); dialog.showModal();
    });
  }
  async function deleteComment(button) {
    const section = button.closest('[data-reader-community]');
    const form = section.querySelector('.reader-comment-form');
    if (section.dataset.deleting || form.dataset.submitting === '1') return;
    section.dataset.deleting = '1'; button.disabled = true;
    let deleted = false;
    try {
      if (!await confirmDelete() || !section.isConnected) return;
      const response = await fetch(form.action + '/' + button.dataset.deleteComment + '/delete', {
        method: 'POST', headers: {'Accept': 'application/json', 'X-CSRF-Token': form.elements.csrf_token.value}
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        if (response.status === 401) syncSession();
        throw Error(result.message || '删除失败，请重试。');
      }
      deleted = true;
      // Render the current page again to keep thread placeholders and pagination consistent.
      const draft = form.elements.content.value;
      const target = form.elements.reply_to_id.value;
      const html = await fetch(location.pathname + location.search, {cache: 'no-store'});
      if (!html.ok) throw Error('页面刷新失败');
      const replacement = new DOMParser().parseFromString(await html.text(), 'text/html').querySelector('[data-reader-community]');
      if (!replacement) throw Error('页面刷新失败');
      section.replaceWith(replacement);
      syncProjectCounts(replacement, {comments: replacement.querySelector('[data-comment-count]').textContent});
      const newForm = replacement.querySelector('.reader-comment-form');
      newForm.elements.content.value = draft;
      if (draft && target) replacement.querySelector('[data-reply-to="' + target + '"]')?.click();
      status(replacement, draft ? '评论已删除，未发送的文字已保留。' : '评论已删除。');
      formatTimes(); syncSession();
    } catch (error) {
      status(section, deleted ? '评论已删除，页面刷新失败，请刷新查看；输入已保留。' : (error.message || '网络异常，删除未确认，请重试。'), true);
    } finally { delete section.dataset.deleting; button.disabled = false; }
  }
  function resetReply(section) {
    const form = section.querySelector('.reader-comment-form');
    section.querySelector('[data-comment-form-home]').after(form);
    form.elements.reply_to_id.value = '';
    form.querySelector('.reader-reply-context').hidden = true;
    form.querySelector('button[type=submit]').textContent = '发表评论';
  }
  async function moreReplies(button) {
    if(button.dataset.loading==='1') return;
    button.dataset.loading='1';button.setAttribute('aria-disabled','true');
    const section=button.closest('[data-reader-community]');
    const thread=button.closest('[data-thread-id]');
    try {
      const url=new URL(button.dataset.repliesUrl,location.origin);
      url.searchParams.set('after',button.dataset.repliesCursor);
      const response=await fetch(url,{cache:'no-store',headers:{Accept:'application/json'}});
      const result=await response.json();
      if(!response.ok||!result.ok) throw Error(result.message||'回复加载失败，请重试。');
      if(!thread.isConnected) return;
      for(const comment of result.replies) {
        if(!thread.querySelector('[data-comment-id="'+comment.id+'"]')) thread.querySelector('[data-thread-replies]').append(renderComment(comment));
      }
      const container=thread.querySelector('[data-thread-replies]');
      [...container.children].sort((a,b)=>a.querySelector('time').dateTime.localeCompare(b.querySelector('time').dateTime)||Number(a.dataset.commentId)-Number(b.dataset.commentId)).forEach(item=>container.append(item));
      if(result.next_cursor) {
        button.dataset.repliesCursor=result.next_cursor;
        const fallback=new URL(location.href);fallback.searchParams.set('reply_thread',thread.dataset.threadId);fallback.searchParams.set('reply_after',result.next_cursor);button.href=fallback.href;
      } else button.remove();
      formatTimes();
    } catch(error) {status(section,error.message||'回复加载失败，请重试。',true);}
    finally {delete button.dataset.loading;button.removeAttribute('aria-disabled');}
  }
  function formatTimes() {
    document.querySelectorAll('.reader-comment-meta time[datetime]').forEach(time => {
      const date = new Date(time.dateTime);
      if (!Number.isNaN(date.getTime())) time.textContent = date.toLocaleString('zh-CN', {hour12: false});
    });
  }
  document.addEventListener('click', event => {
    const more=event.target.closest('[data-more-replies]');
    if(more) {event.preventDefault();moreReplies(more);return;}
    const like = event.target.closest('[data-comment-like]');
    if (like) { likeComment(like); return; }
    const remove = event.target.closest('[data-delete-comment]');
    if (remove) { deleteComment(remove); return; }
    if (event.target.closest('[data-reader-dialog-close]')) loginDialog()?.close();
    const reply = event.target.closest('[data-reply-to]');
    const cancel = event.target.closest('[data-cancel-reply]');
    if (reply || cancel) {
      const section = (reply || cancel).closest('[data-reader-community]');
      const form = section.querySelector('.reader-comment-form');
      if (form.dataset.submitting === '1' || section.dataset.deleting) return;
      if (cancel) { resetReply(section); return; }
      if (section.dataset.authenticated !== '1') { requireLogin(section); return; }
      form.elements.reply_to_id.value = reply.dataset.replyTo;
      form.querySelector('[data-reply-context]').textContent = '回复 @' + reply.dataset.replyNickname;
      form.querySelector('.reader-reply-context').hidden = false;
      form.querySelector('button[type=submit]').textContent = '发表回复';
      reply.closest('.reader-comment-body').append(form);
      form.elements.content.focus();
    }
    const area = event.target.closest('.reader-comment-form textarea');
    if (area && area.closest('[data-reader-community]').dataset.authenticated !== '1') requireLogin(area.closest('[data-reader-community]'));
  });
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!form.matches('.reader-like-form,.reader-comment-form')) return;
    event.preventDefault();
    const section = form.closest('[data-reader-community]');
    if (section.dataset.deleting) return;
    if (section.dataset.authenticated !== '1') { requireLogin(section); return; }
    if (form.dataset.submitting === '1') return;
    form.dataset.submitting = '1';
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    const isComment = form.matches('.reader-comment-form');
    const text = isComment ? form.elements.content.value : '';
    status(section, isComment ? '正在发表评论…' : '正在更新点赞…');
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form), headers: {'Accept': 'application/json'}});
      if (!(response.headers.get('content-type') || '').includes('application/json')) throw Error('页面已过期，请刷新后重试。');
      const result = await response.json();
      if (response.status === 401) { requireLogin(section, result.login_url); syncSession(); }
      if (!response.ok || !result.ok) throw Error(result.message || '提交失败，请稍后重试。');
      if (!isComment) {
        button.setAttribute('aria-pressed', String(result.liked));
        form.elements.liked.value = result.liked ? '0' : '1';
        form.querySelector('[data-like-label]').textContent = result.liked ? '已点赞' : (section.matches('[data-project-community]') ? '点赞这个项目' : '点赞');
        form.querySelector('[data-like-count]').textContent = result.like_count;
        syncProjectCounts(section, {likes: result.like_count});
        status(section, result.liked ? '已点赞，感谢你的支持。' : '已取消点赞。');
      } else {
        syncProjectCounts(section, {comments: result.comment_count});
        if (result.status && result.status !== 'approved') {
          if (form.elements.content.value === text) form.elements.content.value = '';
          form.elements.submission_id.value = result.submission_id;
          resetReply(section);
          status(section, result.message, result.status === 'rejected');
          return;
        }
        if (form.elements.content.value === text) form.elements.content.value = '';
        form.elements.submission_id.value = result.submission_id;
        resetReply(section);
        await reloadComments(section, result.comment.parent_id ? section.dataset.commentPage : 1);
        status(section, result.comment.parent_id ? '回复已发表。' : '评论已发表。');
      }
    } catch (error) { status(section, error.message || '网络异常，请重试；输入内容已保留。', true); }
    finally { form.dataset.submitting = '0'; button.disabled = false; }
  });
  const refresh = () => { mountProjectShortcut(); formatTimes(); syncSession(); };
  document.addEventListener('orange:page', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) syncSession(); });
  refresh();
})();
