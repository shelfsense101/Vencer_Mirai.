document.addEventListener('DOMContentLoaded',()=>{
 const search=document.getElementById('searchInput');
 if(search) search.addEventListener('input',()=>{const q=search.value.toLowerCase();document.querySelectorAll('#productRows tr[data-search]').forEach(row=>row.hidden=!row.dataset.search.includes(q));});
 const flash=document.querySelectorAll('.flash'); flash.forEach(el=>setTimeout(()=>{el.style.opacity='0';setTimeout(()=>el.remove(),400)},5000));
 const scanButton=document.getElementById('scanSubmit');
 if(scanButton) scanButton.addEventListener('click',async()=>{const input=document.getElementById('scanBarcode');const msg=document.getElementById('scanMessage');const token=document.querySelector('meta[name="csrf-token"]')?.content||'';try{const res=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify({barcode:input.value.trim()})});const data=await res.json();msg.textContent=data.message||data.error||'Request completed';msg.className=data.ok?'success-text':'danger-text';if(data.ok)input.value='';}catch(e){msg.textContent='Could not contact the server. Try again.';}});
 // Camera scanner is optional; typed barcode entry remains available on every device.
 const start=document.getElementById('startScanner'), stop=document.getElementById('stopScanner');let scanner=null;
 if(start) start.addEventListener('click',async()=>{const msg=document.getElementById('scanMessage');if(!window.Html5Qrcode){msg.textContent='Camera library unavailable. You can enter the barcode manually.';return;}try{scanner=new Html5Qrcode('scanner-reader');await scanner.start({facingMode:'environment'},{fps:10,qrbox:{width:240,height:140}},async text=>{document.getElementById('scanBarcode').value=text;stop.click();scanButton?.click();},()=>{});msg.textContent='Camera scanner active.';}catch(e){msg.textContent='Camera could not start. Check browser permission or enter barcode manually.';}});
 if(stop) stop.addEventListener('click',async()=>{if(scanner){try{await scanner.stop();scanner.clear();}catch(e){}scanner=null;}const msg=document.getElementById('scanMessage');if(msg)msg.textContent='Camera scanner stopped.';});
});
