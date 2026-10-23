(() => {
 const $=s=>document.querySelector(s);
 let request,serial=0,motion,view;
 const reduced=()=>window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
 history.scrollRestoration='manual';
 // Keep this history entry's position before recording the newly loaded page.
 const entry=history.state;
 const navigationType=performance.getEntriesByType('navigation')[0]?.type;
 const saved=(navigationType==='reload'||navigationType==='back_forward')&&
  (!entry?.orangeScrollURL||entry.orangeScrollURL===location.href)&&
  Array.isArray(entry?.orangeScroll)&&entry.orangeScroll.length===2&&
  entry.orangeScroll.every(n=>Number.isFinite(n)&&n>=0)?entry.orangeScroll:null;
 let restoring=!!saved,restoreFrame,settled=0,fontsReady=!document.fonts;
 const deadline=performance.now()+8000;
 let stampTimer=0,lastStamp=-Infinity;
 const stamp=(immediate=false)=>{
  if(restoring)return;
  const remaining=1000-(performance.now()-lastStamp);
  if(immediate!==true&&remaining>0){
   if(!stampTimer)stampTimer=setTimeout(()=>{stampTimer=0;stamp()},remaining);
   return;
  }
  clearTimeout(stampTimer);stampTimer=0;
  const state=history.state;
  if(state?.orangeScrollURL===location.href&&state.orangeScroll?.[0]===scrollX&&state.orangeScroll?.[1]===scrollY)return;
  lastStamp=performance.now();
  try{history.replaceState({...state,orangeScroll:[scrollX,scrollY],orangeScrollURL:location.href},'',location.href)}
  catch(error){if(error.name!=='SecurityError')throw error}
 };
 function finishRestore(){restoring=false;cancelAnimationFrame(restoreFrame);stamp()}
 function restore(){
  if(!restoring)return;
  window.scrollTo({left:saved[0],top:saved[1],behavior:'instant'});
  const ready=document.readyState==='complete'&&fontsReady&&!document.documentElement.classList.contains('orange-loading');
  settled=ready&&Math.abs(scrollY-saved[1])<=1?settled+1:0;
  if(settled>=3||performance.now()>deadline){finishRestore();return}
  restoreFrame=requestAnimationFrame(restore);
 }
 if(restoring){
  document.fonts?.ready.then(()=>{fontsReady=true});
  restoreFrame=requestAnimationFrame(restore);
 }else stamp();
 // A reader's input always takes priority over an unfinished layout restore.
 for(const type of ['wheel','touchstart','pointerdown'])window.addEventListener(type,()=>{if(restoring)finishRestore()},{passive:true});
 window.addEventListener('keydown',event=>{if(restoring&&['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key))finishRestore()});
 window.addEventListener('pagehide',()=>stamp(true));
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')stamp(true)});
 window.addEventListener('pageshow',event=>{if(event.persisted)finishRestore()});
 let scrollFrame;window.addEventListener('scroll',()=>{if(!scrollFrame)scrollFrame=requestAnimationFrame(()=>{scrollFrame=0;stamp()})},{passive:true});
 function publicURL(url){return url.origin===location.origin&&!/^\/(admin|static|auth|api)(\/|$)/.test(url.pathname)&&! /\.(xml|json|pdf|zip|png|jpe?g|svg|webp|ico)$/i.test(url.pathname)}
 function refreshNav(){const path=location.pathname.replace(/\/$/,'')||'/';document.querySelectorAll('.navbar-list a,.drawer-navbar-list a').forEach(a=>a.classList.toggle('active',new URL(a.href).pathname.replace(/\/$/,'')===path||(path==='/'&&a.getAttribute('href')==='/')));$('.navbar-content')?.classList.toggle('has-home-banner',document.body.classList.contains('orange-home'));$('.navbar-drawer')?.classList.remove('orange-open');document.querySelectorAll('.orange-dropdown-open').forEach(n=>n.classList.remove('orange-dropdown-open'));$('#orange-search')?.classList.remove('active');$('#orange-lightbox')?.classList.remove('orange-open')}
 async function navigate(url,{pop=false,state=null}={}){
  if(restoring)finishRestore();
  request?.abort();motion?.cancel();view?.skipTransition();request=new AbortController();const own=++serial;
  $('#main-content').setAttribute('aria-busy','true');
  try{
   const response=await fetch(url.href,{signal:request.signal,headers:{'X-Orange-Navigation':'1'}});
   if(!response.ok||!response.headers.get('content-type')?.includes('text/html'))throw Error();
   const target=new URL(response.url);if(!publicURL(target))throw Error();target.hash=url.hash;
   const next=new DOMParser().parseFromString(await response.text(),'text/html');
   const content=next.querySelector('#main-content');if(!content||!next.querySelector('.navbar-container'))throw Error();
   // Pages requiring their own scripts use a normal load instead.
   if(content.querySelector('script')||Array.from(next.scripts).some(s=>s.src&&!/\/(orange-theme|scroll-blur|orange-navigation|codex-activity|community)\.js(?:\?|$)/.test(s.src)))throw Error();
   if(own!==serial)return;
   const assetList=doc=>Array.from(doc.querySelectorAll('script[src],link[rel="stylesheet"]')).map(n=>n.getAttribute('src')||n.getAttribute('href')).filter(x=>/\.css(?:[?#]|$)/i.test(x)||/\/(orange-theme|scroll-blur|orange-navigation|codex-activity|community)\.js(?:[?#]|$)/.test(x)).sort().join('|');
   if(assetList(document)!==assetList(next)){location.assign(target.href);return;}
   if(next.querySelector('#codex-activity')){
    try{const dataResponse=await fetch('/static/redefine/codex-activity.json?t='+Date.now(),{cache:'no-store',signal:request.signal});if(dataResponse.ok)window.orangeTokenActivityData=await dataResponse.json()}catch(error){if(error.name==='AbortError')throw error}
   }
   if(own!==serial)return;
   const panel=$('#main-content');
   async function swap(){
    if(own!==serial)return;
    if(!pop){stamp(true);history.pushState({orangeScroll:[0,0]},'',target.href)}
    panel.replaceChildren(...content.childNodes);
    $('.home-banner-container')?.remove();const hero=next.querySelector('.home-banner-container');if(hero)$('.main-content-container').before(hero);
    document.body.classList.toggle('orange-home',next.body.classList.contains('orange-home'));
    document.title=next.title;const desc=next.querySelector('meta[name="description"]');if(desc)$('meta[name="description"]')?.setAttribute('content',desc.content);
    const footer=next.querySelector('.main-content-footer');if(footer)$('.main-content-footer').replaceChildren(...footer.childNodes);
    const visitMeta=next.querySelector('meta[name="orange-visit-event"]');
    document.querySelector('meta[name="orange-visit-event"]')?.setAttribute('content',visitMeta?.content||'');
    refreshNav();document.dispatchEvent(new Event('orange:page'));
    await window.orangeTokenReady;
    if(pop&&state?.orangeScroll){window.scrollTo({left:state.orangeScroll[0],top:state.orangeScroll[1],behavior:'instant'})}
    else if(target.hash){let el;try{el=document.getElementById(decodeURIComponent(target.hash.slice(1)))}catch{}if(el)el.scrollIntoView({behavior:'instant'});else window.scrollTo({top:0,behavior:'instant'})}
    else window.scrollTo({top:0,behavior:'instant'});
    const heading=$('#main-content h1');if(heading){heading.setAttribute('tabindex','-1');heading.focus({preventScroll:true})}
    window.dispatchEvent(new Event('scroll'));
   }
   if(!reduced()&&document.startViewTransition){
    view=document.startViewTransition(swap);
    await view.finished.catch(()=>{});
   }else{
    await swap();
    if(!reduced()&&panel.animate)motion=panel.animate([{opacity:.3},{opacity:1}],{duration:220,easing:'ease-out'});
   }
  }catch(error){if(error.name!=='AbortError'&&own===serial)location.assign(url.href)}
  finally{if(own===serial)$('#main-content')?.removeAttribute('aria-busy')}
 }
 document.addEventListener('click',event=>{
  if(event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
  const a=event.target.closest('a[href]');if(!a||a.hasAttribute('download')||(a.target&&a.target!=='_self')||a.dataset.noPjax!==undefined)return;
  const raw=a.getAttribute('href');if(!raw||raw.startsWith('#'))return;
  const url=new URL(a.href);if(!publicURL(url)||url.pathname===location.pathname&&url.search===location.search)return;
  event.preventDefault();navigate(url);
 });
 document.addEventListener('submit',event=>{const form=event.target;if(!form.matches('.orange-search-form')||form.method.toLowerCase()!=='get')return;const url=new URL(form.action||location.href);if(!publicURL(url))return;url.search=new URLSearchParams(new FormData(form)).toString();event.preventDefault();navigate(url)});
 window.addEventListener('popstate',event=>{clearTimeout(stampTimer);stampTimer=0;navigate(new URL(location.href),{pop:true,state:event.state})});
 refreshNav();
})();
