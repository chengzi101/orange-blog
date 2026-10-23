/* Gradually soften the scenery as the article section enters the viewport. */
(() => {
 let scheduled=false;
 function update(){
  scheduled=false;
  const background=document.querySelector('.home-banner-background'),hero=document.querySelector('.orange-home .home-banner-container');
  if(!background)return;
  if(!hero){background.style.removeProperty('--reading-blur');background.style.removeProperty('--reading-softness');return;}
  const height=hero.getBoundingClientRect().height;
  const distance=-hero.getBoundingClientRect().top;
  const start=height*.28;
  const end=Math.max(start+1,height-110);
  const progress=Math.min(1,Math.max(0,(distance-start)/(end-start)));
  background.style.setProperty('--reading-blur',(progress*16).toFixed(2)+'px');
  background.style.setProperty('--reading-softness',(progress*.12).toFixed(3));
  scheduled=false;
 }
 function request(){if(!scheduled){scheduled=true;requestAnimationFrame(update)}}
 window.addEventListener('scroll',request,{passive:true});
 window.addEventListener('resize',request,{passive:true});
 window.addEventListener('pageshow',request);
 document.addEventListener('orange:page',request);
 update();
})();
