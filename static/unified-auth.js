(()=>{
 const form=document.querySelector('[data-unified-form]');if(!form)return;
 const status=form.querySelector('[role=status]');
 const send=async(button,old=false)=>{
  button.disabled=true;status.textContent='正在发送…';
  const phone=form.elements.phone.value;
  try{
   const r=await fetch('/api/auth/sms/send',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':form.elements.csrf_token.value},body:JSON.stringify({phone,purpose:old?'old-phone':form.dataset.purpose})});
   const result=await r.json();if(!r.ok)throw Error(result.message||'发送失败，请稍后重试');
   form.elements[old?'old_challenge_id':'challenge_id'].value=result.challenge_id;
   status.textContent='验证码已发送，5分钟内有效。';
   let left=result.retry_after;button.textContent=`${left}秒后可重发`;
   const timer=setInterval(()=>{left--;button.textContent=left?`${left}秒后可重发`:'重新获取验证码';if(!left){clearInterval(timer);button.disabled=false;}},1000);
  }catch(e){status.textContent=e.message;button.disabled=false;}
 };
 form.querySelector('[data-send-code]').addEventListener('click',e=>send(e.currentTarget));
 form.querySelector('[data-send-old]')?.addEventListener('click',e=>send(e.currentTarget,true));
 form.elements.phone.addEventListener('input',()=>{form.elements.challenge_id.value='';});
})();
