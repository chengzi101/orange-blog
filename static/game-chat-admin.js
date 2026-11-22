(() => {
    'use strict';
    const $ = s => document.querySelector(s), root = $('#admin-content'), isRules = root.dataset.section === 'rules';
    const el = (tag, text, className) => { const n = document.createElement(tag); if (text !== undefined)
        n.textContent = text; if (className)
        n.className = className; return n; };
    const categories = { sexual: '色情', advertising: '广告', 'illegal-trade': '违法交易', url: '网址', abuse: '辱骂', harassment: '骚扰', other: '其他' }, levels = { low: '轻度', medium: '中度', high: '严重', uncertain: '易误判' }, actions = { allow: '正常放行', observe: '仅记录', mask: '星号替换', block: '拒绝发送', review: '拦截并审核' }, states = { normal: '正常', open: '待处理', confirmed: '人工确认违规', 'false-positive': '已纠正误判', withdrawn: '已撤回' }, penalties = { warn: '警告', withdraw: '撤回消息', mute: '临时禁言', permanent: '永久禁言', unmute: '解除禁言' };
    let csrf = '', selectedId = '', messageOffset = 0, ruleOffset = 0, whiteOffset = 0, historyOffset = 0, rulePage = [], selectedRules = new Set(), messageRequest = 0, ruleRequest = 0, whiteRequest = 0, loadingMessages = false;
    const time = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—';
    const until = value => value === 8640000000000000 ? '永久' : time(value);
    const status = (text, error = false) => { const n = $('#status'); n.textContent = text; n.dataset.error = String(error); };
    const run = fn => async (e) => { e?.preventDefault(); const trigger = e?.currentTarget; if (trigger?.dataset.pending === 'true')
        return; try {
        if (trigger)
            trigger.dataset.pending = 'true';
        await fn(e);
    }
    catch (error) {
        status(error.message, true);
    }
    finally {
        if (trigger)
            delete trigger.dataset.pending;
    } };
    async function api(route, method = 'GET', body) { const r = await fetch($('#dashboard').dataset.api + route, { method, headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) }); const data = await r.json().catch(() => ({ error: '请求失败（' + r.status + '）' })); if (!r.ok) {
        if (r.status === 401)
            throw Error('管理会话已失效，请重新登录后台。');
        throw Error(data.error || '请求失败');
    } return data; }
    const button = (label, fn, className) => { const b = el('button', label, className); b.type = 'button'; b.onclick = run(fn); return b; };
    const badge = (label, tone) => { const n = el('span', label, 'gc-badge'); n.dataset.tone = tone; return n; };
    const formQuery = id => Object.fromEntries(new FormData($(id)));
    function pager(prefix, data) { const singular = { messages: 'message', rules: 'rule', whites: 'white', history: 'history' }[prefix]; $('#' + singular + '-count').textContent = data.total ? `${data.offset + 1}–${data.offset + data.items.length} / 共 ${data.total.toLocaleString()} 条` : '共 0 条'; $('#prev-' + prefix).disabled = data.offset === 0; $('#next-' + prefix).disabled = data.offset + data.limit >= data.total; }
    function empty(container, text) { container.replaceChildren(el('p', text, 'gc-empty')); }
    function options() { for (const [name, values] of Object.entries({ category: categories, level: levels, action: actions }))
        for (const select of root.querySelectorAll(`select[name="${name}"]`)) {
            if (!isRules && name === 'action')
                continue;
            for (const [value, label] of Object.entries(values)) {
                const option = el('option', label);
                option.value = value;
                select.append(option);
            }
        } }
    async function metadata() { const data = await api('/session'); csrf = data.csrf; $('#service-state').textContent = '审核服务已连接 · v' + data.version; $('#service-state').dataset.ready = 'true'; $('#scope-footer').textContent = (data.scope || []).join('、'); if (isRules) {
        $('#rule-total').textContent = data.ruleCount.toLocaleString();
        $('#white-total').textContent = '共 ' + data.whiteCount + ' 条';
        $('#metadata').textContent = `版本 v${data.version} · 最近更新 ${time(data.lastUpdate)} · 来源 ${data.source.upstream.commit.slice(0, 12)}`;
        $('#scope').textContent = '当前适用范围：' + data.scope.join('、') + '。网站其他聊天按已有业务配置接入。';
    } return data; }
    async function overview() { const d = await api('/overview'); $('#service-state').textContent = '审核服务已连接 · v' + d.version; $('#stat-total').textContent = (d.counts.total || 0).toLocaleString(); $('#stat-pending').textContent = d.counts.pending || 0; $('#stat-handled').textContent = (Number(d.counts.masked || 0) + Number(d.counts.blocked || 0)).toLocaleString(); $('#stat-muted').textContent = d.muted; }
    function effectiveHits(hits) { const h = hits.filter(h => !h.exempt && h.action !== 'allow'); return h.length ? h : hits; }
    function detection(container, result) { container.append(badge(actions[result.action] || result.action, result.action)); const hits = typeof result.hits === 'string' ? JSON.parse(result.hits) : result.hits; if (!hits.length) {
        container.append(el('p', '未命中敏感词规则，正常放行。'));
        return;
    } const active = effectiveHits(hits); container.append(el('p', '判断原因：' + (active.some(h => !h.exempt) ? '依据命中规则的处理优先级：审核拦截 > 拒绝 > 替换 > 仅记录。' : '命中均已被白名单或人工纠正豁免。'))); for (const h of hits) {
        const item = el('div', undefined, 'gc-hit');
        item.append(el('b', h.term + ' '), badge(levels[h.level] || h.level, h.level), el('small', `${categories[h.category] || h.category} · ${actions[h.action]}${h.exempt ? ' · 已豁免' : ''}`), el('small', '规则 ID：' + h.ruleId));
        container.append(item);
    } }
    function renderEvents(container, data) { container.replaceChildren(); if (!data.items.length)
        return empty(container, '暂无处理记录。'); for (const event of data.items) {
        const d = JSON.parse(event.details), action = event.action.startsWith('penalty:') ? penalties[event.action.slice(8)] : event.action === 'record:resolve' ? (d.falsePositive ? '纠正误判' : '确认违规') : event.action === 'mute' ? '禁言' : '解除禁言';
        const card = el('article', undefined, 'gc-event');
        card.append(el('b', action + ' · ' + (d.userId || '关联消息')), el('p', d.reason || d.note || '未填写备注'), el('small', `操作人：${d.actor || '旧版本未记录'} · ${time(event.time)}${d.until ? ' · 到期：' + until(d.until) : ''}`));
        if (d.recordId || d.id)
            card.append(el('small', '消息 ID：' + (d.recordId || d.id)));
        container.append(card);
    } }
    async function loadMessages() { const request = ++messageRequest; loadingMessages = true; try {
        const q = formQuery('#message-search');
        for (const key of ['from', 'to'])
            if (q[key])
                q[key] = new Date(q[key]).toISOString();
        if (q.from && q.to && q.from > q.to)
            throw Error('开始时间不能晚于结束时间');
        const data = await api('/messages?' + new URLSearchParams({ ...q, offset: messageOffset, limit: 50 }));
        if (request !== messageRequest)
            return;
        const list = $('#message-list');
        list.replaceChildren();
        for (const r of data.items) {
            const row = el('tr');
            row.dataset.id = r.id;
            row.dataset.selected = String(r.id === selectedId);
            const user = el('td', time(r.time));
            user.append(el('small', r.nickname), el('small', r.user_id));
            const content = el('td');
            content.append(el('p', r.text, 'gc-message-text'));
            if (r.filtered !== r.text)
                content.append(el('small', '发送内容：' + r.filtered));
            const auto = el('td');
            auto.append(badge(actions[r.action], r.action));
            const hits = effectiveHits(JSON.parse(r.hits));
            if (hits.length)
                auto.append(el('small', hits.slice(0, 3).map(h => h.term).join('、') + (hits.length > 3 ? '…' : '')));
            const result = el('td');
            result.append(badge(states[r.status] || r.status, r.status));
            const controls = el('td');
            controls.append(button('详情', () => showDetail(r.id, true)));
            row.append(user, content, auto, result, controls);
            list.append(row);
        }
        if (!data.items.length) {
            const row = el('tr'), cell = el('td', '没有符合条件的消息。');
            cell.colSpan = 5;
            cell.className = 'gc-empty';
            row.append(cell);
            list.append(row);
        }
        pager('messages', data);
    }
    finally {
        if (request === messageRequest)
            loadingMessages = false;
    } }
    async function showDetail(id, scroll = false) { selectedId = id; const r = await api('/messages/' + encodeURIComponent(id)); if (selectedId !== id)
        return; const box = $('#message-detail'); box.replaceChildren(); $('.gc-detail').dataset.active = 'true'; $('#close-detail').hidden = false; for (const row of root.querySelectorAll('#message-list tr'))
        row.dataset.selected = String(row.dataset.id === id); box.append(el('div', `${r.nickname} · ${r.user_id}`, 'gc-detail-meta'), el('div', time(r.time) + ' · 消息 ID ' + r.id, 'gc-detail-meta'), el('h3', '原始消息'), el('blockquote', r.text), el('h3', '自动判断 · 词库 v' + r.version)); detection(box, r); box.append(el('p', '发送结果：' + (['block', 'review'].includes(r.action) ? '未发送（已拦截）' : r.status === 'withdrawn' ? '已撤回' : r.filtered)), badge(states[r.status] || r.status, r.status)); if (r.note)
        box.append(el('p', '处理备注：' + r.note)); box.append(el('h3', '人工复核')); const note = el('textarea'); note.maxLength = 500; note.placeholder = '填写复核原因（必填）'; note.setAttribute('aria-label', '复核原因'); box.append(note); const review = el('div', undefined, 'gc-actions'); for (const [label, falsePositive] of [['纠正误判', true], ['确认违规', false]]) {
        const b = button(label, async () => { if (!note.value.trim())
            throw Error('请填写复核原因'); await api('/records/' + r.id + '/resolve', 'POST', { falsePositive, note: note.value }); await Promise.all([showDetail(r.id), loadMessages(), overview()]); status('人工判断已保存；原始自动判断与历次处理记录均保留。'); });
        b.disabled = r.status === 'withdrawn';
        review.append(b);
    } box.append(review, el('small', '纠正误判仅放行该玩家重发的相同内容及对应规则，不重播原消息。'), el('h3', '玩家处理')); box.append(el('p', r.mute ? '当前禁言：' + until(r.mute.until) + ' · ' + r.mute.reason : '当前未禁言')); const form = el('form'), select = el('select'); select.name = 'action'; select.setAttribute('aria-label', '处罚方式'); for (const [value, label] of Object.entries(penalties)) {
        if (value === 'withdraw' && !r.canWithdraw)
            continue;
        const option = el('option', label);
        option.value = value;
        select.append(option);
    } const duration = el('input'); duration.type = 'number'; duration.value = '30'; duration.min = '1'; duration.max = '43200'; duration.name = 'minutes'; duration.setAttribute('aria-label', '临时禁言分钟'); const reason = el('textarea'); reason.name = 'reason'; reason.required = true; reason.maxLength = 200; reason.placeholder = '填写处罚或解除原因'; reason.setAttribute('aria-label', '处罚原因'); const submit = el('button', '执行处理'); submit.className = 'gc-primary'; form.append(select, duration, reason, submit); const updateDuration = () => { duration.hidden = select.value !== 'mute'; duration.required = select.value === 'mute'; }; select.onchange = updateDuration; updateDuration(); form.onsubmit = run(async () => { if (!confirm(`对 ${r.nickname}（${r.user_id}）执行「${penalties[select.value]}」？`))
        return; await api('/penalties', 'POST', { userId: r.user_id, recordId: r.id, action: select.value, minutes: Number(duration.value), reason: reason.value }); await Promise.all([showDetail(r.id), loadMessages(), overview(), loadHistory(), loadMutes()]); status('处理已生效并记录操作人及原因。'); }); box.append(form, el('h3', '此消息的处理记录')); const events = el('div'); renderEvents(events, r.events); box.append(events); if (r.events.total > r.events.items.length)
        box.append(button('查询此玩家全部处理历史', () => { const panel = $('#history-panel'); panel.open = true; $('#history-search').elements.userId.value = r.user_id; historyOffset = 0; return loadHistory(); })); if (scroll && matchMedia('(max-width:1250px)').matches)
        $('.gc-detail').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    async function loadHistory() { const data = await api('/events?' + new URLSearchParams({ ...formQuery('#history-search'), offset: historyOffset, limit: 20 })); renderEvents($('#history-list'), data); pager('history', data); }
    async function loadMutes() { const list = await api('/mutes'), box = $('#mute-list'); box.replaceChildren(); if (!list.length)
        return empty(box, '当前没有禁言用户。'); for (const m of list) {
        const row = el('div', undefined, 'gc-item');
        row.append(el('span', `${m.user_id} · 到期：${until(m.until)} · ${m.reason}`), button('解除禁言', async () => { const reason = prompt('请填写解除原因'); if (reason === null)
            return; if (!reason.trim())
            throw Error('请填写解除原因'); await api('/penalties', 'POST', { userId: m.user_id, action: 'unmute', reason }); await Promise.all([loadMutes(), loadHistory(), overview()]); if (selectedId)
            await showDetail(selectedId); status('已解除禁言。'); }));
        box.append(row);
    } }
    function editRule(r) { const form = $('#rule-form'); form.reset(); form.elements.id.value = ''; delete form.dataset.source; if (r) {
        for (const key of ['id', 'term', 'category', 'level', 'action', 'boundary'])
            form.elements[key].value = r[key] ?? '';
        form.elements.enabled.checked = r.enabled;
        form.dataset.source = r.source;
    }
    else {
        form.elements.level.value = 'uncertain';
        form.elements.action.value = 'observe';
    } $('#edit-title').textContent = r ? '编辑敏感词' : '新增敏感词'; $('#rule-source').textContent = r ? '规则 ID：' + r.id + ' · 来源：' + r.source : '新增规则默认仅记录，可按需调整处理方式。'; $('#rule-editor').showModal(); }
    function selection() { const inputs = [...root.querySelectorAll('[data-rule-select]')]; for (const input of inputs)
        input.checked = selectedRules.has(input.value); $('#selection-count').textContent = '已选 ' + selectedRules.size + ' 条'; $('#select-page').checked = !!inputs.length && inputs.every(i => i.checked); $('#select-page').indeterminate = selectedRules.size > 0 && !$('#select-page').checked; for (const b of root.querySelectorAll('[data-batch]'))
        b.disabled = !selectedRules.size; }
    async function loadRules() { const request = ++ruleRequest; const data = await api('/rules?' + new URLSearchParams({ ...formQuery('#rule-search'), offset: ruleOffset, limit: 50 })); if (request !== ruleRequest)
        return; rulePage = data.rules; selectedRules.clear(); const list = $('#rule-list'); list.replaceChildren(); for (const r of data.rules) {
        const row = el('tr'), check = el('input');
        check.type = 'checkbox';
        check.value = r.id;
        check.dataset.ruleSelect = '';
        check.setAttribute('aria-label', '选择 ' + r.term);
        check.onchange = () => { if (check.checked)
            selectedRules.add(r.id);
        else
            selectedRules.delete(r.id); selection(); };
        const c = el('td');
        c.append(check);
        const term = el('td', r.term);
        term.append(el('small', r.id));
        const risk = el('td', categories[r.category] + ' / ');
        risk.append(badge(levels[r.level], r.level));
        const action = el('td');
        action.append(badge(actions[r.action], r.action));
        const controls = el('td');
        controls.append(button('编辑', () => editRule(r)), button('删除', async () => { if (!confirm('删除敏感词「' + r.term + '」？'))
            return; await api('/rules/' + encodeURIComponent(r.id), 'DELETE'); await Promise.all([loadRules(), metadata()]); status('规则已删除并即时生效。'); }, 'gc-danger'));
        row.append(c, term, risk, action, el('td', r.enabled ? '启用' : '停用'), controls);
        list.append(row);
    } if (!data.rules.length) {
        const row = el('tr'), cell = el('td', '没有符合条件的词条。');
        cell.colSpan = 6;
        row.append(cell);
        list.append(row);
    } pager('rules', { ...data, items: data.rules }); selection(); }
    async function loadWhites() { const request = ++whiteRequest; const data = await api('/whitelist?' + new URLSearchParams({ ...formQuery('#white-search'), paged: 1, offset: whiteOffset, limit: 20 })); if (request !== whiteRequest)
        return; const box = $('#white-list'); box.replaceChildren(); for (const w of data.items) {
        const row = el('div', undefined, 'gc-item');
        row.append(el('span', `${w.term} · ${w.ruleId || '全部规则'} · ${w.enabled ? '启用' : '停用'}`), button('编辑', () => { const f = $('#white-form'); for (const key of ['id', 'term', 'ruleId'])
            f.elements[key].value = w[key] || ''; f.elements.enabled.checked = w.enabled; f.elements.term.focus(); }), button('删除', async () => { if (!confirm('删除白名单「' + w.term + '」？'))
            return; await api('/whitelist/' + encodeURIComponent(w.id), 'DELETE'); await Promise.all([loadWhites(), metadata()]); status('白名单已删除。'); }));
        box.append(row);
    } if (!data.items.length)
        empty(box, '暂无符合条件的白名单。'); pager('whites', data); }
    function resetWhite() { $('#white-form').reset(); $('#white-form').elements.id.value = ''; }
    options();
    if (isRules) {
        $('#rule-search').onsubmit = run(async () => { ruleOffset = 0; await loadRules(); });
        $('#prev-rules').onclick = run(async () => { ruleOffset = Math.max(0, ruleOffset - 50); await loadRules(); });
        $('#next-rules').onclick = run(async () => { ruleOffset += 50; await loadRules(); });
        $('#new-rule').onclick = () => editRule();
        $('#close-rule').onclick = () => $('#rule-editor').close();
        $('#rule-form').elements.level.onchange = e => { $('#rule-form').elements.action.value = { low: 'mask', medium: 'block', high: 'review', uncertain: 'observe' }[e.target.value]; };
        $('#rule-form').onsubmit = run(async () => { const f = $('#rule-form'), body = { ...formQuery('#rule-form'), enabled: f.elements.enabled.checked, source: f.dataset.source || 'administrator' }; if (!body.boundary)
            delete body.boundary; await api('/rules', 'POST', body); $('#rule-editor').close(); await Promise.all([loadRules(), metadata()]); status('规则已保存，检测引擎已热更新。'); });
        $('#select-page').onchange = e => { selectedRules = new Set(e.target.checked ? rulePage.map(r => r.id) : []); selection(); };
        for (const b of root.querySelectorAll('[data-batch]'))
            b.onclick = run(async () => { if (!selectedRules.size)
                throw Error('请先选择规则'); if (!confirm(`对选中的 ${selectedRules.size} 条规则执行「${b.textContent}」？`))
                return; await api('/rules/batch', 'POST', { ids: [...selectedRules], operation: b.dataset.batch }); await Promise.all([loadRules(), metadata()]); status('批量操作已生效。'); });
        $('#reload').onclick = run(async () => { await api('/reload', 'POST', {}); await Promise.all([metadata(), loadRules(), loadWhites()]); status('词库重新加载完成。'); });
        $('#import-level').value = 'uncertain';
        $('#import-action').value = 'observe';
        $('#import-level').onchange = e => { $('#import-action').value = { low: 'mask', medium: 'block', high: 'review', uncertain: 'observe' }[e.target.value]; };
        $('#import-file').onchange = run(async (e) => { const file = e.target.files[0]; if (!file)
            return; if (file.size > 12 * 1024 * 1024)
            throw Error('文件不能超过 12MB'); $('#import-text').value = await file.text(); });
        $('#import').onclick = run(async () => { const raw = $('#import-text').value.replace(/^\uFEFF/, '').trim(); let rules, whitelist; if (raw.startsWith('{') || raw.startsWith('[')) {
            const data = JSON.parse(raw);
            rules = Array.isArray(data) ? data : data.rules;
            whitelist = data.whitelist;
        }
        else
            rules = [...new Set(raw.split(/[,，\r\n]+/).map(t => t.trim()).filter(Boolean))].map(term => ({ term, category: $('#import-category').value, level: $('#import-level').value, action: $('#import-action').value, enabled: true, source: 'administrator text import' })); const data = await api('/import', 'POST', { rules, whitelist }); await Promise.all([loadRules(), loadWhites(), metadata()]); status('已导入 ' + data.ids.length + ' 条规则。'); });
        $('#white-search').onsubmit = run(async () => { whiteOffset = 0; await loadWhites(); });
        $('#prev-whites').onclick = run(async () => { whiteOffset = Math.max(0, whiteOffset - 20); await loadWhites(); });
        $('#next-whites').onclick = run(async () => { whiteOffset += 20; await loadWhites(); });
        $('#reset-white').onclick = resetWhite;
        $('#white-form').onsubmit = run(async () => { const f = $('#white-form'); await api('/whitelist', 'POST', { ...formQuery('#white-form'), ruleId: f.elements.ruleId.value || null, enabled: f.elements.enabled.checked }); resetWhite(); await Promise.all([loadWhites(), metadata()]); status('白名单已保存并即时生效。'); });
    }
    else {
        $('#message-search').onsubmit = run(async () => { messageOffset = 0; await loadMessages(); });
        $('#message-search').onreset = () => queueMicrotask(() => { messageOffset = 0; run(loadMessages)(); });
        $('#refresh-records').onclick = run(async () => { await Promise.all([loadMessages(), overview()]); if (selectedId)
            await showDetail(selectedId); status('已刷新。'); });
        $('#prev-messages').onclick = run(async () => { messageOffset = Math.max(0, messageOffset - 50); await loadMessages(); });
        $('#next-messages').onclick = run(async () => { messageOffset += 50; await loadMessages(); });
        $('#close-detail').onclick = () => { $('.gc-detail').dataset.active = 'false'; selectedId = ''; empty($('#message-detail'), '选择一条消息，查看检测结果与处理记录。'); $('#close-detail').hidden = true; for (const row of root.querySelectorAll('#message-list tr'))
            row.dataset.selected = 'false'; };
        $('#history-search').onsubmit = run(async () => { historyOffset = 0; await loadHistory(); });
        $('#prev-history').onclick = run(async () => { historyOffset = Math.max(0, historyOffset - 20); await loadHistory(); });
        $('#next-history').onclick = run(async () => { historyOffset += 20; await loadHistory(); });
        $('#history-panel').ontoggle = () => { if ($('#history-panel').open)
            run(async () => { await Promise.all([loadHistory(), loadMutes()]); })(); };
        $('#user-penalty').onsubmit = run(async () => { const body = formQuery('#user-penalty'); body.minutes = Number(body.minutes); if (!confirm(`对 ${body.userId} 执行「${penalties[body.action]}」？`))
            return; await api('/penalties', 'POST', body); await Promise.all([loadMutes(), loadHistory(), overview()]); if (selectedId)
            await showDetail(selectedId); status('用户处理已生效。'); });
        $('#test-form').onsubmit = run(async () => { const data = await api('/test', 'POST', formQuery('#test-form')); const box = $('#test-result'); box.replaceChildren(el('h3', '试审结果')); detection(box, data); box.append(el('p', '实际处理：' + (data.allowed ? data.text : '拒绝发送，消息不会进入聊天频道。'))); });
        setInterval(() => { if (csrf && !document.hidden && $('#auto-refresh').checked && messageOffset === 0 && !loadingMessages)
            run(async () => { await Promise.all([loadMessages(), overview()]); })(); }, 10000);
    }
    (async () => { try {
        await metadata();
        if (isRules)
            await Promise.all([loadRules(), loadWhites()]);
        else
            await Promise.all([loadMessages(), overview()]);
    }
    catch (error) {
        $('#service-state').textContent = '审核服务连接失败';
        status(error.message, true);
    } })();
})();
