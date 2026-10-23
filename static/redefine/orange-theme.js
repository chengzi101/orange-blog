(() => {
  // Confirm the event rendered in this document, including PJAX page changes.
  // Using its ID prevents simultaneous tabs from confirming each other's page.
  const confirmedVisits = new Set();
  function confirmVisit() {
    const id = document.querySelector('meta[name="orange-visit-event"]')?.content;
    if (!/^\d+$/.test(id || '') || confirmedVisits.has(id)) return;
    confirmedVisits.add(id);
    const body = JSON.stringify({
      event_id: Number(id),
      screen: `${window.screen.width}x${window.screen.height}`,
      language: navigator.language || '',
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
    });
    const send = () => fetch('/analytics/confirm', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json' }, body,
    }).then(response => { if (!response.ok) throw new Error('Visit confirmation failed'); });
    send().catch(() => { window.setTimeout(() => { send().catch(() => { confirmedVisits.delete(id); }); }, 1000); });
  }
  document.addEventListener('orange:page', confirmVisit);
  confirmVisit();
  const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
  const root=document.documentElement;
  function theme(dark){root.classList.toggle('dark',dark);root.classList.toggle('light',!dark);document.body.classList.toggle('dark-mode',dark);document.body.classList.toggle('light-mode',!dark);root.style.colorScheme=dark?'dark':'light';}
  try{theme(localStorage.getItem('orange-theme')==='dark')}catch{}
  $('.tool-dark-light-toggle')?.addEventListener('click',()=>{const dark=!root.classList.contains('dark');theme(dark);try{localStorage.setItem('orange-theme',dark?'dark':'light')}catch{}});
  window.scrollToMain=()=>$('#main-content')?.scrollIntoView({behavior:'smooth'});
  $('.tool-scroll-to-top')?.addEventListener('click',()=>window.scrollTo({top:0,behavior:'smooth'}));
  $('.tool-scroll-to-bottom')?.addEventListener('click',()=>window.scrollTo({top:document.documentElement.scrollHeight,behavior:'smooth'}));
  $('.toggle-tools-list')?.addEventListener('click',()=>$('.hidden-tools-list')?.classList.toggle('orange-show'));
  let font=16;$('.tool-font-adjust-plus')?.addEventListener('click',()=>root.style.fontSize=`${font=Math.min(22,font+1)}px`);$('.tool-font-adjust-minus')?.addEventListener('click',()=>root.style.fontSize=`${font=Math.max(12,font-1)}px`);
  const drawer=$('.navbar-drawer');
  if(drawer){const close=document.createElement('button');close.type='button';close.className='orange-drawer-close';close.textContent='×';close.setAttribute('aria-label','关闭菜单');drawer.prepend(close);close.addEventListener('click',()=>drawer.classList.remove('orange-open'))}
  $('.navbar-bar')?.addEventListener('click',()=>drawer?.classList.toggle('orange-open'));
  $$('[navbar-data-toggle]').forEach(e=>e.addEventListener('click',()=>document.querySelector(`[data-target="${e.getAttribute('navbar-data-toggle')}"]`)?.classList.toggle('hidden')));
  $$('.navbar-list .has-dropdown').forEach(trigger=>{
    const menu=trigger.parentElement;
    const close=(keyboard=false)=>{
      menu.classList.remove('orange-dropdown-open');
      // Pointer clicks leave focus behind; keep genuine keyboard focus usable.
      const focused=document.activeElement;
      if(menu.contains(focused)&&(keyboard||!focused.matches(':focus-visible')))focused.blur();
    };
    trigger.addEventListener('click',event=>{event.preventDefault();menu.classList.toggle('orange-dropdown-open')});
    menu.addEventListener('pointerleave',event=>{if(event.pointerType==='mouse'||event.pointerType==='pen')close()});
    document.addEventListener('click',event=>{if(!menu.contains(event.target))close()});
    menu.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close(true)}});
  });
  const path=location.pathname.replace(/\/$/,'')||'/';$$('.navbar-list a').forEach(e=>e.classList.toggle('active',(e.getAttribute('href')?.replace(/\/$/,'')||'/')===path));
  const overlay=$('#orange-search'),input=overlay?.querySelector('input'),results=$('#search-result');let lastFocus,indexPromise;
  function closeSearch(){overlay?.classList.remove('active');lastFocus?.focus()}
  $$('.search-popup-trigger').forEach(e=>{e.setAttribute('role','button');e.setAttribute('tabindex','0');e.setAttribute('aria-label','搜索文章');const open=()=>{lastFocus=e;overlay.classList.add('active');input.focus()};e.addEventListener('click',open);e.addEventListener('keydown',ev=>{if(ev.key==='Enter')open()})});
  $('.popup-btn-close')?.addEventListener('click',closeSearch);overlay?.addEventListener('click',e=>{if(e.target===overlay)closeSearch()});
  let generation=0;input?.addEventListener('input',async()=>{const own=++generation,q=input.value.trim().toLowerCase();results.replaceChildren();if(!q){const p=document.createElement('p');p.className='search-hint';p.textContent='输入关键词搜索文章';results.append(p);return}try{indexPromise ||= (async()=>{const all=[];let cursor='0';do{const r=await fetch('/search-index.json?after='+encodeURIComponent(cursor));if(!r.ok)throw Error();all.push(...await r.json());const next=r.headers.get('X-Next-Cursor');if(next&&(!/^[0-9]{1,18}$/.test(next)||BigInt(next)<=BigInt(cursor)))throw Error();cursor=next}while(cursor);return all})();const data=await indexPromise;if(own!==generation)return;const matches=data.filter(p=>(p.title+' '+p.excerpt+' '+p.content).toLowerCase().includes(q));const list=document.createElement('ul');list.className='search-result-list';for(const p of matches){const li=document.createElement('li'),a=document.createElement('a'),ex=document.createElement('p');a.className='search-result-title';a.href=p.url;a.textContent=p.title;ex.className='search-result';ex.textContent=p.excerpt;li.append(a,ex);list.append(li)}if(!matches.length){const p=document.createElement('p');p.className='search-hint';p.textContent='没有找到相关文章';results.append(p)}else results.append(list)}catch{indexPromise=null;const p=document.createElement('p');p.textContent='搜索暂时不可用，请稍后重试';results.append(p)}});
  const lightbox=$('#orange-lightbox');document.addEventListener('click',event=>{const img=event.target.closest('.article-content img');if(img){lightbox.querySelector('img').src=img.src;lightbox.querySelector('img').alt=img.alt;lightbox.classList.add('orange-open')}});$('.image-viewer-close')?.addEventListener('click',()=>lightbox.classList.remove('orange-open'));lightbox?.addEventListener('click',e=>{if(e.target===lightbox)lightbox.classList.remove('orange-open')});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeSearch();drawer?.classList.remove('orange-open');lightbox?.classList.remove('orange-open')}if(e.key==='Tab'&&overlay?.classList.contains('active')){const items=[input,$('.popup-btn-close')];if(e.shiftKey&&document.activeElement===items[0]){e.preventDefault();items[1].focus()}else if(!e.shiftKey&&document.activeElement===items[1]){e.preventDefault();items[0].focus()}}});
  const percent=$('.tool-scroll-to-top .percent');window.addEventListener('scroll',()=>{if(percent){const max=document.documentElement.scrollHeight-innerHeight;percent.textContent=max>0?Math.round(scrollY/max*100)+'%':''}},{passive:true});

  // Build the outline from heading levels: LaTeX sections start at h2.
  function initToc(){
    $$('.post-toc .toc').forEach(toc=>{
      if(toc.dataset.collapsible)return;
      const links=[...toc.querySelectorAll('a[href^="#"]')];
      if(!links.length)return;
      const entries=links.map(a=>{
        let heading;try{heading=document.getElementById(decodeURIComponent(a.hash.slice(1)))}catch{}
        return {a,level:Number(a.dataset.tocLevel)||Number(heading?.tagName.slice(1))||2};
      });
      const base=Math.min(...entries.map(e=>e.level));
      const list=document.createElement('ul'),stack=[];
      list.className='orange-toc-tree';
      for(const {a,level} of entries){
        while(stack.length&&stack.at(-1).level>=level)stack.pop();
        const parent=stack.at(-1),li=document.createElement('li');
        li.dataset.outlineDepth=String(level-base+1);
        if(parent){
          if(!parent.children){parent.children=document.createElement('ul');parent.li.append(parent.children)}
          parent.children.append(li);
        }else list.append(li);
        li.append(a);stack.push({level,li});
      }
      toc.replaceChildren(list);toc.dataset.collapsible='true';
      [...toc.querySelectorAll('li[data-outline-depth="2"]')].forEach((li,index)=>{
        const children=li.querySelector(':scope > ul'),a=li.querySelector(':scope > a');
        if(!children)return;
        children.id='orange-toc-children-'+index;children.hidden=true;
        a.classList.add('orange-toc-toggle');
        a.setAttribute('aria-expanded','false');a.setAttribute('aria-controls',children.id);
        a.addEventListener('click',event=>{
          if(event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
          children.hidden=!children.hidden;a.setAttribute('aria-expanded',String(!children.hidden));
        });
        a.addEventListener('keydown',event=>{if(event.key===' '){event.preventDefault();a.click()}});
      });
      // Native fragment navigation triggers the site's PJAX popstate handler.
      // Keep an outline click in this document so its expanded state survives.
      toc.addEventListener('click',event=>{
        const a=event.target.closest('a[href^="#"]');
        if(!a||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
        let heading;try{heading=document.getElementById(decodeURIComponent(a.hash.slice(1)))}catch{}
        if(!heading)return;
        event.preventDefault();
        if(location.hash!==a.hash)history.pushState({...history.state,orangeScroll:[scrollX,scrollY],orangeScrollURL:a.href},'',a.hash);
        heading.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
      });
    });
    revealTocHash();
  }
  function revealTocHash(){
    if(!location.hash)return;
    const a=$$('.post-toc a').find(a=>a.hash===location.hash);
    const children=a?.closest('ul[hidden]')||(a?.hasAttribute('aria-controls')?document.getElementById(a.getAttribute('aria-controls')):null);
    if(children){children.hidden=false;children.previousElementSibling?.setAttribute('aria-expanded','true')}
  }
  window.addEventListener('hashchange',revealTocHash);

  let subtitleTimer,pageObserver;
  function initPage(){clearInterval(subtitleTimer);pageObserver?.disconnect();initToc();
  const subtitle=$('#subtitle');if(subtitle){const text=subtitle.dataset.text||'';const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;if(reduced)subtitle.textContent=text;else{let i=0;const cursor=document.createElement('span');cursor.className='typing-cursor';cursor.textContent='|';subtitle.after(cursor);subtitleTimer=setInterval(()=>{subtitle.textContent=text.slice(0,++i);if(i>=text.length)clearInterval(subtitleTimer)},90)}}
  const headings=$$('.article-content :is(h1,h2,h3,h4,h5,h6)[id]');if(headings.length){pageObserver=new IntersectionObserver(entries=>{for(const e of entries)if(e.isIntersecting){$$('.post-toc a').forEach(a=>a.classList.toggle('active',a.hash==='#'+e.target.id))}},{rootMargin:'-10% 0px -70% 0px'});headings.forEach(h=>pageObserver.observe(h))}
  }
  document.addEventListener('orange:page',initPage);initPage();
})();
