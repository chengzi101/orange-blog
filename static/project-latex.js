(() => {
'use strict';
const host=document.querySelector('[data-project-latex-host]');if(!host)return;
const form=host.querySelector('form.editor-form'),format=form.elements.namedItem('content_format'),content=form.elements.namedItem('content'),panel=host.querySelector('[data-project-latex-panel]'),legacy=host.querySelector('[data-project-legacy]'),frame=host.querySelector('[data-project-latex-frame]'),status=host.querySelector('[data-project-latex-state]'),start=host.querySelector('[data-project-latex-start]');
let siteId=Number(host.dataset.siteProjectId)||Number(location.pathname.match(/\/edit\/(\d+)/)?.[1])||null,workspaceId=null,initialization=null,ready=false;
const waiters=new Map();const csrf=()=>form.elements.namedItem('csrf_token').value;
function state(message,error=false){status.textContent=message;status.dataset.error=String(error);status.hidden=false;}
function show(){const latex=format.value==='latex';host.classList.toggle('project-latex-editing',latex);panel.hidden=!latex;legacy.hidden=latex;form.querySelector('[data-project-latex-intent]').value=latex?'1':'0';const preview=form.querySelector('button[formtarget="_blank"]');if(preview)preview.hidden=latex;if(latex)ensure().catch(e=>state(e.message,true));}
async function ensure(){
 if(ready)return;
 if(initialization)return initialization;
 initialization=(async()=>{
  const initial=content.value;
  if(!siteId){
   if(!form.reportValidity())throw Error('请先填写项目名称和简介，再启用 LaTeX 编辑区。');
   state('正在保存项目信息…');
   const data=new FormData(form);data.set('content','');data.set('latex_workbench','1');
   const response=await fetch(form.action,{method:'POST',body:data,credentials:'same-origin',headers:{'X-Orange-Save':'1','Accept':'application/json'}});
   if(!(response.headers.get('content-type')||'').includes('application/json'))throw Error('请确认登录状态后重试。');
   const result=await response.json();if(!response.ok||!result.ok)throw Error(result.message||'项目信息保存失败。');
   siteId=Number(result.edit_url.match(/\/edit\/(\d+)/)?.[1]);if(!siteId)throw Error('无法确定项目编号。');
   host.dataset.siteProjectId=String(siteId);form.action=result.edit_url;history.replaceState(history.state,'',result.edit_url);
   if(typeof result.slug==='string')form.elements.namedItem('slug').value=result.slug;
  }
  state('正在打开当前项目的 LaTeX 文档…');
  const response=await fetch('/admin/latex/from-project/'+siteId,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify({initial_content:initial})});
  if(!(response.headers.get('content-type')||'').includes('application/json'))throw Error('请确认登录状态后重试。');
  const result=await response.json();if(!response.ok||!result.ok)throw Error(result.message||'文档打开失败。');
  workspaceId=result.workspace_id;
  frame.hidden=false;start.hidden=true;
  const loaded=new Promise((resolve,reject)=>{const timer=setTimeout(()=>{waiters.delete('ready');reject(Error('编辑区加载超时，可点击启用按钮重试。'));},25000);waiters.set('ready',{resolve:()=>{clearTimeout(timer);resolve();},reject});});
  frame.src=result.editor_url;await loaded;status.hidden=true;
 })();
 try{return await initialization;}catch(e){initialization=null;start.hidden=false;throw e;}
}
window.addEventListener('message',event=>{
 if(event.origin!==location.origin||event.source!==frame.contentWindow)return;
 const data=event.data;if(!data||data.workspaceId!==workspaceId)return;
 if(data.type==='orange-latex-ready'){ready=true;waiters.get('ready')?.resolve();waiters.delete('ready');}
 if(data.type==='orange-latex-flushed'){const entry=waiters.get(data.requestId);if(!entry)return;waiters.delete(data.requestId);data.ok?entry.resolve():entry.reject(Error(data.message||'LaTeX 文档尚未保存。'));}
});
window.orangeProjectLatexFlush=async()=>{
 if(format.value!=='latex')return;
 await ensure();
 const requestId=crypto.randomUUID();return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{waiters.delete(requestId);reject(Error('文档保存未收到确认，请保持页面打开并重试。'));},25000);waiters.set(requestId,{resolve:()=>{clearTimeout(timer);resolve();},reject:e=>{clearTimeout(timer);reject(e);}});frame.contentWindow.postMessage({type:'orange-latex-flush',requestId},location.origin);});
};
start.addEventListener('click',()=>ensure().catch(e=>state(e.message,true)));
host.querySelector('[data-project-save-meta]').addEventListener('click',()=>form.requestSubmit(form.querySelector('.save-button')));
let modeSave=Promise.resolve();
function rememberMode(){
 const mode=format.value,label=host.querySelector('[data-editor-format-status]');
 label.textContent='正在记忆编辑模式…';
 modeSave=modeSave.catch(()=>{}).then(async()=>{
  if(!siteId){if(mode!=='latex')return;await ensure();}
  const response=await fetch('/admin/latex/editor-format/'+siteId,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify({format:mode})});
  const result=await response.json();if(!response.ok||!result.ok)throw Error(result.message||'编辑模式保存失败，请重试切换。');
  if(format.value===mode)label.textContent='编辑模式已记忆；刷新后保持当前模式';
 }).catch(error=>{label.textContent=error.message||'编辑模式保存失败，请重试切换。';});
}
format.addEventListener('change',()=>{show();rememberMode();});show();
})();
