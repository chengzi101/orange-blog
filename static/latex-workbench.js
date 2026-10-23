(() => {
'use strict';
const root=document.querySelector('[data-latex-workbench]');if(!root)return;
const $=s=>root.querySelector(s), $$=s=>Array.from(root.querySelectorAll(s));
const base='/admin/latex/'+root.dataset.projectId, csrf=root.dataset.csrf, prefix='orange-latex-draft:v2:'+root.dataset.projectId+':';
let tabId;try{const tabKey='orange-latex-tab:'+root.dataset.projectId;tabId=sessionStorage.getItem(tabKey)||crypto.randomUUID();sessionStorage.setItem(tabKey,tabId);}catch{tabId=Date.now()+'-'+Math.random().toString(36).slice(2);}
const key=prefix+tabId;let recoveryKey=null,recoveryRaw=null;
const fileList=$('[data-lw-files]'),title=$('[data-lw-title]'),main=$('[data-lw-main]'),engine=$('[data-lw-engine]'),saveState=$('[data-lw-save]'),compileButton=$('[data-lw-compile]');
const embedded=root.hasAttribute('data-embedded');let siteId=Number(root.dataset.siteProjectId)||null;
let model=null,files=new Map(),active='',changed=new Set(),deleted=new Set(),metaDirty=false,loading=false,saving=null,saveTimer,autoTimer,compiling=false,currentBuild=null,blocked=false,backupFailed=false,noticeTimer;
const cm=CodeMirror.fromTextArea($('[data-lw-editor]'),{mode:'stex',lineNumbers:true,lineWrapping:true,matchBrackets:true,autoCloseBrackets:true,indentUnit:2,tabSize:2,extraKeys:{'Ctrl-S':()=>saveAll(),'Cmd-S':()=>saveAll(),'Ctrl-Enter':()=>compile(),'Cmd-Enter':()=>compile(),'Tab':c=>c.replaceSelection('  ')}});
const dirty=()=>changed.size>0||deleted.size>0||metaDirty;
function notifyParent(type,extra={}){if(embedded&&window.parent!==window)window.parent.postMessage({type,workspaceId:Number(root.dataset.projectId),...extra},location.origin);}
window.addEventListener('message',async event=>{if(!embedded||event.origin!==location.origin||event.source!==window.parent||event.data?.type!=='orange-latex-flush')return;try{const ok=await saveAll();notifyParent('orange-latex-flushed',{requestId:event.data.requestId,ok:!!ok,message:ok?'':'源码尚未确认保存。'});}catch(e){notifyParent('orange-latex-flushed',{requestId:event.data.requestId,ok:false,message:e.message});}});
function notice(text,error=false){const el=$('[data-lw-notice]');el.textContent=text;el.dataset.error=String(error);el.hidden=false;clearTimeout(noticeTimer);if(!error)noticeTimer=setTimeout(()=>{el.hidden=true;},6500);}
function draft(){return {version:1,serverVersion:model?.version,updatedAt:Date.now(),title:title.value,main_file:main.value,engine:engine.value,files:Object.fromEntries(Array.from(files).filter(([,f])=>f.text!==null).map(([n,f])=>[n,f.text])),deleted:[...deleted],active};}
function persist(){if(!model)return;try{localStorage.setItem(key,JSON.stringify(draft()));backupFailed=false;}catch{backupFailed=true;notice('浏览器草稿备份失败，请点击「下载本机备份」并保持页面打开。',true);$('[data-lw-recovery]').hidden=false;}}
function downloadText(name,text){const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function mark(){if(loading||!model)return;saveState.textContent='有未保存更改';persist();clearTimeout(saveTimer);if(!blocked)saveTimer=setTimeout(()=>saveAll().catch(()=>{}),900);clearTimeout(autoTimer);if($('[data-lw-auto]').checked&&!blocked)autoTimer=setTimeout(()=>compile(true),1000);updateStale();}
async function api(path,body,method='POST'){
 const form=body instanceof FormData;
 const response=await fetch(base+path,{method,credentials:'same-origin',headers:form?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:method==='GET'?undefined:form?body:JSON.stringify(body||{})});
 const type=response.headers.get('content-type')||'';
 if(!type.includes('application/json'))throw Error(response.status===400?'请求已失效，请先下载本机备份，然后刷新页面。':'会话可能已失效，请先下载本机备份，再重新登录。');
 const result=await response.json();if(!response.ok||!result.ok){const e=Error(result.message||'操作失败，请重试。');e.status=response.status;throw e;}return result;
}
async function saveAll(){
 if(!model||blocked){if(blocked)notice('保存发生版本冲突。请先下载本机备份，再刷新对照。',true);return false;}
 clearTimeout(saveTimer);if(saving){await saving;if(dirty())return saveAll();return true;}
 if(!dirty())return true;
 const snapshots={};for(const name of changed){const f=files.get(name);if(f?.text!==null&&f)snapshots[name]=f.text;}
 const snapshot={version:model.version,title:title.value,main_file:main.value,engine:engine.value,files:snapshots,deleted:[...deleted]};
 saveState.textContent='正在保存…';persist();
 saving=(async()=>{
  try{
   const result=await api('/save',snapshot);model.version=result.version;model.title=snapshot.title;model.main_file=snapshot.main_file;model.engine=snapshot.engine;
   for(const [name,text] of Object.entries(snapshots))if(files.get(name)?.text===text)changed.delete(name);
   for(const name of snapshot.deleted)if(!files.has(name))deleted.delete(name);
   metaDirty=title.value!==snapshot.title||main.value!==snapshot.main_file||engine.value!==snapshot.engine;
   if(dirty()){persist();saveState.textContent='已保存，仍有新更改';}
   else{try{localStorage.removeItem(key);if(recoveryKey&&localStorage.getItem(recoveryKey)===recoveryRaw)localStorage.removeItem(recoveryKey);recoveryKey=null;recoveryRaw=null;}catch{}saveState.textContent='已保存 · '+new Date().toLocaleTimeString();}
   updateStale();return true;
  }catch(e){if(e.status===409)blocked=true;persist();saveState.textContent=e.status===409?'版本冲突，已保留本机备份':'保存失败，源码保留在本机';notice(e.message,true);throw e;}
  finally{saving=null;}
 })();
 const ok=await saving;if(dirty()&&!blocked)return saveAll();return ok;
}
function renderFiles(){
 fileList.replaceChildren();
 for(const [name,f] of [...files].sort(([a],[b])=>a.localeCompare(b))){const button=document.createElement('button');button.type='button';button.setAttribute('aria-current',String(name===active));button.title=name;const icon=document.createElement('span');icon.textContent=f.text===null?'▧':'⌘';const label=document.createElement('span');label.textContent=name;button.append(icon,label);button.addEventListener('click',()=>openFile(name));fileList.append(button);}
 const chosen=main.value||model?.main_file;main.replaceChildren();for(const [name] of files)if(name.toLowerCase().endsWith('.tex')){const o=document.createElement('option');o.value=name;o.textContent=name;main.append(o);}main.value=chosen;
}
function openFile(name,line){
 const f=files.get(name);if(!f)return;active=name;$('[data-lw-active]').textContent=name;loading=true;
 const isText=f.text!==null;cm.getWrapperElement().hidden=!isText;$('[data-lw-binary]').hidden=isText;
 if(isText){cm.setValue(f.text);cm.clearHistory();if(line)cm.setCursor(Math.max(0,line-1),0);cm.refresh();if(line){cm.scrollIntoView({line:Math.max(0,line-1),ch:0},80);cm.focus();}}
 else{const url=base+'/file?path='+encodeURIComponent(name);$('[data-lw-binary-download]').href=url;const image=$('[data-lw-image]');image.hidden=true;image.removeAttribute('src');if(/\.(png|jpe?g)$/i.test(name)){image.src=url+'&preview=1';image.hidden=false;}}
 loading=false;renderFiles();if(line)switchTab('code');
}
function updateStale(){const state=$('[data-lw-build-state]');if(currentBuild?.status==='success'){const stale=currentBuild.version!==model.version||dirty();state.textContent=stale?'源码已变化，PDF 待更新':'编译成功';$('[data-lw-publish]').disabled=stale;}}
cm.on('change',()=>{if(loading||!active||!files.has(active)||files.get(active).text===null)return;files.get(active).text=cm.getValue();changed.add(active);mark();updateStale();});
cm.on('cursorActivity',()=>{const p=cm.getCursor();$('[data-lw-cursor]').textContent='行 '+(p.line+1)+'，列 '+(p.ch+1);});
for(const input of [title,main,engine])input.addEventListener(input===title?'input':'change',()=>{if(loading)return;metaDirty=true;mark();updateStale();});
$('[data-lw-save-button]').addEventListener('click',()=>saveAll().catch(()=>{}));
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();saveAll().catch(()=>{});}if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();compile();}});
window.addEventListener('pagehide',()=>{if(dirty())persist();});window.addEventListener('beforeunload',e=>{if(dirty())persist();if(dirty()&&backupFailed){e.preventDefault();e.returnValue='';}});
async function dialog(heading,build){const d=$('[data-lw-dialog]'),body=$('[data-lw-dialog-body]');$('[data-lw-dialog-title]').textContent=heading;body.replaceChildren();d.querySelector('button[value=confirm]').disabled=false;const read=build(body);d.returnValue='cancel';d.showModal();return new Promise(resolve=>d.addEventListener('close',()=>resolve(d.returnValue==='confirm'?read():null),{once:true}));}
function textNode(tag,text){const e=document.createElement(tag);e.textContent=text;return e;}
$('[data-lw-new]').addEventListener('click',async()=>{const name=await dialog('新建源码文件',body=>{body.append(textNode('p','支持子目录，例如 chapters/intro.tex 或 references.bib。'));const input=document.createElement('input');input.placeholder='chapters/intro.tex';input.required=true;body.append(input);setTimeout(()=>input.focus(),50);return()=>input.value.trim();});if(!name)return;if(files.has(name)){notice('同名文件已经存在。',true);return;}if(!/\.(tex|bib|sty|cls|bst|bbx|cbx|def|cfg|clo|fd|map|enc|ist|tikz|csv|txt|md)$/i.test(name)){notice('请选择受支持的文本文件扩展名。',true);return;}files.set(name,{path:name,text:'',size:0});changed.add(name);deleted.delete(name);renderFiles();openFile(name);mark();});
$('[data-lw-remove-file]').addEventListener('click',async()=>{if(!active)return;if(active===main.value){notice('请先选择其他主文件，再删除当前主文件。',true);return;}const name=active;const ok=await dialog('删除文件',body=>{body.append(textNode('p','确定删除 '+name+'？删除后将无法在文件列表中查看。'));return()=>true;});if(!ok)return;files.delete(name);changed.delete(name);deleted.add(name);renderFiles();openFile(main.value||[...files.keys()][0]);mark();});
$('[data-lw-upload]').addEventListener('click',()=>$('[data-lw-upload-input]').click());
$('[data-lw-upload-input]').addEventListener('change',async e=>{const selected=[...e.target.files];if(!selected.length)return;try{
 if(!await saveAll())return;let replace=false;
 async function send(){const data=new FormData();data.set('csrf_token',csrf);data.set('version',String(model.version));data.set('replace',replace?'1':'0');for(const f of selected)data.append('files',f,f.webkitRelativePath||f.name);return api('/upload',data);}
 try{await send();}catch(err){if(err.status!==409||!err.message.startsWith('文件已存在'))throw err;const yes=await dialog('覆盖同名文件',body=>{body.append(textNode('p',err.message));return()=>true;});if(!yes)return;replace=true;await send();}
 await loadState(false);notice('文件已上传。');
 }catch(err){notice(err.message,true);}finally{e.target.value='';}});
let pdfPreviewPromise=null,previewBuildId=null;
function refreshPreview(build){
 if(previewBuildId===build.id)return;
 previewBuildId=build.id;
 const container=$('[data-lw-pdf]');container.hidden=false;
 if(!pdfPreviewPromise)pdfPreviewPromise=import('/static/latex-pdf-preview.js?v=20261021-destroy-fix-v1').then(module=>module.createPreview(container)).catch(error=>{pdfPreviewPromise=null;throw error;});
 pdfPreviewPromise.then(preview=>{if(previewBuildId===build.id)return preview.load(base+'/build/'+build.id+'/pdf');}).then(()=>{if(previewBuildId===build.id){container.hidden=false;$('[data-lw-placeholder]').hidden=true;}}).catch(error=>{if(previewBuildId===build.id){previewBuildId=null;notice('PDF 预览加载失败：'+error.message,true);}});
}
function showBuild(build){
 currentBuild=build;$('[data-lw-log]').textContent=build.log||'';$('[data-lw-log-summary]').textContent=build.status==='error'?'· 有错误':'';const errors=$('[data-lw-errors]');errors.replaceChildren();errors.hidden=!(build.errors||[]).length;
 for(const item of build.errors||[]){const b=document.createElement('button');b.type='button';b.textContent=item.file+':'+item.line+' '+item.message;b.addEventListener('click',()=>{if(files.has(item.file))openFile(item.file,item.line);else openFile(main.value,item.line);});errors.append(b);}
 if(build.status==='success'){
  refreshPreview(build);updateStale();
 }else if(build.status==='error'){$('[data-lw-build-state]').textContent='编译失败';$('[data-lw-publish]').disabled=true;$('[data-lw-log]').closest('details').open=true;}
}
async function poll(bid){
 try{const result=await api('/build/'+bid,null,'GET');if(result.build.status==='running'){setTimeout(()=>poll(bid),400);return;}compiling=false;compileButton.disabled=false;compileButton.textContent='▶ 编译';showBuild(result.build);if($('[data-lw-auto]').checked&&(result.build.version!==model.version||dirty())){clearTimeout(autoTimer);autoTimer=setTimeout(()=>compile(true),1000);}if(result.build.status==='success')notice('PDF 编译成功。');else notice('编译失败，点击错误可定位源码。',true);}
 catch(e){compiling=false;compileButton.disabled=false;notice(e.message,true);}
}
async function compile(automatic=false){
 if(compiling||!model||blocked)return;
 clearTimeout(autoTimer);compiling=true;try{if(!await saveAll()){compiling=false;return;}compileButton.disabled=true;compileButton.textContent='正在编译…';$('[data-lw-build-state]').textContent='正在编译…';const result=await api('/compile',{version:model.version});poll(result.build_id);}
 catch(e){compiling=false;compileButton.disabled=false;compileButton.textContent='▶ 编译';$('[data-lw-build-state]').textContent='未完成';notice(e.message,true);}
}
compileButton.addEventListener('click',()=>compile());
$('[data-lw-auto]').addEventListener('change',()=>{try{localStorage.setItem('orange-latex-auto',String($('[data-lw-auto]').checked));}catch{}clearTimeout(autoTimer);if($('[data-lw-auto]').checked)autoTimer=setTimeout(()=>compile(true),1000);});
$('[data-lw-publish]').addEventListener('click',async()=>{try{if(!await saveAll())return;if(!currentBuild||currentBuild.version!==model.version){notice('请先重新编译最新源码。',true);return;}const kind=siteId?'project':'media',id=siteId;const result=await api('/publish',{version:model.version,build_id:currentBuild.id,target:kind,target_id:id?Number(id):null});await dialog(siteId?'发布完成':'PDF 已发布',body=>{body.append(textNode('p',result.message));const input=document.createElement('input');input.value=location.origin+result.url;input.readOnly=true;body.append(input);setTimeout(()=>{input.focus();input.select();},50);return()=>true;});}catch(e){notice(e.message,true);}});
$('[data-lw-delete]').addEventListener('click',async()=>{const yes=await dialog('删除整个 LaTeX 项目',body=>{body.append(textNode('p','这会移除工作台中的源码和编译记录。已发布的 PDF 会保留，源码会留存一份服务器恢复包。'));return()=>true;});if(!yes)return;try{if(!await saveAll())return;const result=await api('/delete',{version:model.version});localStorage.removeItem(key);location.href=result.url;}catch(e){notice(e.message,true);}});
$('[data-lw-backup]').addEventListener('click',()=>downloadText('latex-local-backup.json',JSON.stringify(draft(),null,2)));
$('[data-lw-discard]').addEventListener('click',()=>{try{localStorage.removeItem(recoveryKey||key);}catch{}recoveryKey=null;recoveryRaw=null;$('[data-lw-recovery]').hidden=true;});
let recoveryDraft=null;
$('[data-lw-recover]').addEventListener('click',()=>{if(!recoveryDraft)return;const a=document.createElement('a');a.href=base+'/source.zip';a.download='';a.click();loading=true;for(const n of recoveryDraft.deleted||[]){files.delete(n);deleted.add(n);}for(const [name,text] of Object.entries(recoveryDraft.files||{})){if(typeof text!=='string')continue;files.set(name,{path:name,text,size:text.length});changed.add(name);deleted.delete(name);}title.value=recoveryDraft.title||model.title;engine.value=recoveryDraft.engine||model.engine;renderFiles();main.value=files.has(recoveryDraft.main_file)?recoveryDraft.main_file:model.main_file;metaDirty=true;loading=false;openFile(files.has(recoveryDraft.active)?recoveryDraft.active:main.value);$('[data-lw-recovery]').hidden=true;mark();notice('本机备份已恢复，正在保存；服务器原版本已开始下载。');});
function switchTab(panel){$('[data-lw-workspace]').dataset.mobilePanel=panel;for(const b of $$('[data-lw-tab]'))b.setAttribute('aria-current',String(b.dataset.lwTab===panel));if(panel==='code')setTimeout(()=>cm.refresh(),30);}
for(const b of $$('[data-lw-tab]'))b.addEventListener('click',()=>switchTab(b.dataset.lwTab));
$('[data-lw-fullscreen]').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await root.requestFullscreen();setTimeout(()=>cm.refresh(),100);}catch{notice('当前浏览器不支持全屏。');}});
const divider=$('.lw-divider');function resize(clientX){const grid=$('[data-lw-workspace]'),rect=grid.getBoundingClientRect(),sidebar=$('.lw-files').getBoundingClientRect().width;const area=rect.width-sidebar-8,code=Math.max(180,Math.min(area-200,clientX-rect.left-sidebar));grid.style.setProperty('--lw-ratio',(code/(area-code))+'fr');cm.refresh();}
divider.addEventListener('pointerdown',e=>{divider.setPointerCapture(e.pointerId);const move=ev=>resize(ev.clientX);divider.addEventListener('pointermove',move);divider.addEventListener('pointerup',()=>divider.removeEventListener('pointermove',move),{once:true});});divider.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();resize(divider.getBoundingClientRect().left+(e.key==='ArrowLeft'?-35:35));});
let initialState=document.querySelector('[data-lw-initial]');
async function loadState(checkDraft=true){const result=initialState?JSON.parse(initialState.textContent):await api('/state',null,'GET');initialState=null;model=result.project;siteId=result.site_project_id||siteId;if(siteId)$('[data-lw-publish]').textContent='发布正文';files=new Map(result.files.map(f=>[f.path,f]));changed.clear();deleted.clear();metaDirty=false;blocked=false;loading=true;title.value=model.title;engine.value=model.engine;main.value='';renderFiles();main.value=model.main_file;openFile(files.has(active)?active:model.main_file);loading=false;saveState.textContent='已保存';
 if(checkDraft){try{const candidates=[];for(const candidateKey of Object.keys(localStorage)){if(!candidateKey.startsWith(prefix))continue;try{const raw=localStorage.getItem(candidateKey),d=JSON.parse(raw);if(d?.version!==1||!d.files||typeof d.files!=='object')continue;const same=d.title===model.title&&d.main_file===model.main_file&&d.engine===model.engine&&!d.deleted?.length&&Object.entries(d.files).every(([n,t])=>files.get(n)?.text===t);if(same){if(candidateKey===key)localStorage.removeItem(candidateKey);}else candidates.push({key:candidateKey,raw,d});}catch{}}candidates.sort((a,b)=>b.d.updatedAt-a.d.updatedAt);if(candidates.length){recoveryDraft=candidates[0].d;recoveryKey=candidates[0].key;recoveryRaw=candidates[0].raw;$('[data-lw-recovery]').hidden=false;}}catch{}}
 notifyParent('orange-latex-ready');
 const running=result.builds.find(b=>b.status==='running');const successful=result.builds.find(b=>b.status==='success');const latest=result.builds[0];
 if(running){compiling=true;compileButton.disabled=true;compileButton.textContent='正在编译…';poll(running.id);}
 // Loading an old preview must never hold up typing or overwrite a newer build.
 if(successful)api('/build/'+successful.id,null,'GET').then(r=>{if(!currentBuild)showBuild(r.build);if(compiling)$('[data-lw-build-state]').textContent='正在编译…';}).catch(e=>notice(e.message,true));
 if(!running&&!recoveryDraft&&$('[data-lw-auto]').checked&&(!latest||latest.version!==model.version))autoTimer=setTimeout(()=>compile(true),1000);
}
$('[data-lw-auto]').checked=true;
try{$('[data-lw-auto]').checked=localStorage.getItem('orange-latex-auto')!=='false';}catch{}
loadState().catch(e=>{saveState.textContent='加载失败';notice(e.message,true);});
})();
