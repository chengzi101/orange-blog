(() => {
 const $=s=>document.querySelector(s);
 const fmt=n=>{n=Math.max(0,Number(n)||0);const unit=n>=100000000?'亿':'万',base=n>=100000000?100000000:10000;const value=Math.floor(n/base*100)/100;return (n>0&&value===0?'<0.01':value.toLocaleString('zh-CN',{maximumFractionDigits:2}))+unit};
 let activity;
 async function init(){
  const box=$('#codex-activity');if(!box)return;
  try{
   activity = window.orangeTokenActivityData || await fetch('/static/redefine/codex-activity.json?t='+Date.now(),{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json()});
   if(!box.isConnected)return;
   window.orangeTokenActivityData=null;
   const data=activity,days=data.days,keys=Object.keys(days).sort(),incomplete=new Set(data.incomplete_days||[]);
   box.querySelector('[data-token-total]').textContent='总计消耗 '+fmt(data.total_tokens)+' Tokens';
   box.querySelector('[data-token-total]').title='本机可恢复历史累计 '+Number(data.total_tokens).toLocaleString('zh-CN')+' Tokens'+(data.undated_tokens?'；其中 '+fmt(data.undated_tokens)+' 缺少逐日明细':'');
   const end=new Date(data.updated_at.slice(0,10)+'T00:00:00Z');
   const start=new Date(end);start.setUTCDate(start.getUTCDate()-364);start.setUTCDate(start.getUTCDate()-start.getUTCDay());
   const daykey=d=>d.toISOString().slice(0,10);
   function draw(mode){
    const grid=box.querySelector('[data-token-grid]');
    const values=[];let cumulative=keys.filter(k=>k<daykey(start)).reduce((n,k)=>n+days[k],0);
    for(let date=new Date(start);date<=end;date.setUTCDate(date.getUTCDate()+1)){
     const key=daykey(date),daily=days[key]||0;cumulative+=daily;
     let value=daily;
     if(mode==='week'){const monday=new Date(date);monday.setUTCDate(monday.getUTCDate()-((monday.getUTCDay()+6)%7));const sunday=new Date(monday);sunday.setUTCDate(sunday.getUTCDate()+6);value=keys.filter(k=>k>=daykey(monday)&&k<=daykey(sunday)).reduce((n,k)=>n+days[k],0)}
     if(mode==='total')value=cumulative;
     values.push({key,value,daily});
    }
    const max=Math.max(1,...values.map(v=>v.value));
    const detail=box.querySelector('[data-token-detail]');
    function dailyLabel(item){return !item.daily?'当日未消耗Token':incomplete.has(item.key)?'当日已记录 '+fmt(item.daily)+' Tokens':'当日消耗 '+fmt(item.daily)+' Tokens'}
    function show(item){detail.textContent=item.key+' · '+dailyLabel(item)+(mode==='day'?'':' · '+(mode==='week'?'当周已记录':data.undated_tokens?'有日期记录累计':'累计')+' '+fmt(item.value)+' Tokens')}
    for(const [index,item] of values.entries()){
     let cell=grid.children[index];const fresh=!cell;if(fresh){cell=document.createElement('button');cell.type='button'}
     const level=item.value?Math.max(1,Math.min(4,Math.ceil(Math.sqrt(item.value/max)*4))):0;
     cell.className='orange-token-cell token-level-'+level;
     cell.title=item.key+' · '+dailyLabel(item);cell.dataset.date=item.key;cell.dataset.tokens=String(item.daily);cell.setAttribute('aria-label',cell.title);
     cell.onmouseenter=()=>show(item);cell.onfocus=()=>show(item);
     cell.onclick=()=>{grid.querySelectorAll('[aria-pressed="true"]').forEach(n=>n.setAttribute('aria-pressed','false'));cell.setAttribute('aria-pressed','true');show(item)};
     if(fresh)grid.append(cell);
    }
    detail.textContent='';

    const months=box.querySelector('[data-token-months]');months.replaceChildren();
    for(let i=0;i<12;i++){const d=new Date(end);d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-11+i);const label=document.createElement('span');label.textContent=(d.getUTCMonth()+1)+'月';months.append(label)}
    box.querySelectorAll('[data-token-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.tokenMode===mode)));
   }
   box.querySelectorAll('[data-token-mode]').forEach(b=>b.addEventListener('click',()=>draw(b.dataset.tokenMode)));draw('day');
   function clearDetail(){box.querySelector('[data-token-detail]').textContent='';box.querySelectorAll('.orange-token-cell[aria-pressed="true"]').forEach(n=>n.setAttribute('aria-pressed','false'))}
   box.onmouseleave=clearDetail;
   box.onfocusout=event=>{if(!box.contains(event.relatedTarget))clearDetail()};
   const scroller=box.querySelector('.orange-token-scroll');if(scroller)scroller.scrollLeft=scroller.scrollWidth;
  }catch{box.querySelector('[data-token-detail]').textContent='Token 活动暂时无法加载，请稍后再试。'}
 }
 document.addEventListener('orange:page',()=>{window.orangeTokenReady=init()});window.orangeTokenReady=init();
})();
