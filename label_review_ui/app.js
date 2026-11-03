'use strict';
const $=id=>document.getElementById(id), token=new URLSearchParams(location.hash.slice(1)).get('token')||'';
const colors=['#28d4a0','#ffbb4b','#68b3ff','#ff739c','#c09aff','#d5e76e'];
let data=null,all=[],filtered=[],page=0,current=null,picture=null,originalPicture=null,boxes=[],blurs=[],selected=null,selectedBlur=null,tool='select',dirty=false,busy=false,scale=1,ox=0,oy=0,drag=null,space=false,visible=true,undo=[],redo=[],loadSerial=0,previewURL=null,previewSerial=0;
let predictions = [];
const canvas=$('canvas'),ctx=canvas.getContext('2d');
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'X-Review-Token':token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});if(!r.ok){const d=await r.json();throw Error(d.error||'Request failed');}return r.json();}
function imgURL(id,thumb=false){return `/api/${thumb?'thumb':'pixels'}?id=${id}&token=${encodeURIComponent(token)}&v=${current?.revision||Date.now()}`;}
function toast(message,error=false){$('toast').textContent=message;$('toast').style.background=error?'#9c3535':'#133e3b';$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,error?8000:3500);}
function category(id){return data?.categories.find(c=>c.id===id)||{id,name:'Unknown class'};}
function color(id){return colors[Math.max(0,data.categories.findIndex(c=>c.id===id))%colors.length];}
function editable(){return current&&!current.error&&!busy&&!drag;}
function snapshot(){return JSON.stringify({boxes,blurs});}
function remember(before = snapshot()) {
  undo.push(before);
  redo = [];
}
function changed(){dirty=blurs.length>0 || JSON.stringify(boxes)!==JSON.stringify(current.boxes);if(originalPicture)picture=originalPicture;previewSerial++;if(previewURL){URL.revokeObjectURL(previewURL);previewURL=null;}updateSave();inspector();draw();}
function updateSave(){const el=$('saveState');el.textContent=dirty?'Unsaved changes':current?.history?'Reviewed':'Ready to review';el.className='pill '+(dirty?'unsaved':current?.history?'saved':'');$('save').disabled=$('saveNext').disabled=!editable();$('discardChanges').disabled=!editable()||!dirty;$('undo').disabled=!editable()||!undo.length;$('redo').disabled=!editable()||!redo.length;}
function restoreStack(from,to){if(!editable()||!from.length)return;to.push(snapshot());const state=JSON.parse(from.pop());boxes=state.boxes;blurs=state.blurs;selected=null;selectedBlur=null;changed();}
function updatePrivacyEffectVisibility() {
  $('privacyEffectControls').hidden = tool !== 'blur' && !blurs[selectedBlur];
}
function setTool(t){hoverHandle=-1;tool=t;updatePrivacyEffectVisibility();document.querySelectorAll('[data-tool]').forEach(b=>b.classList.toggle('active',b.dataset.tool===t));canvas.style.cursor=t==='pan'?'grab':t==='select'?'default':'crosshair';}
function makeButton(text,fn,cls=''){const b=document.createElement('button');b.textContent=text;b.className=cls;b.onclick=fn;return b;}
function openDialog(){if(busy)return;$('openError').textContent='';$('folderPath').value=data?.folder||$('folderPath').value||preferences.view?.folder||'';$('folderDialog').showModal();}
function suggestions(paths){$('suggestions').replaceChildren();for(const path of paths)$('suggestions').append(makeButton(path,()=>{$('folderPath').value=path;}));}
async function browse(path=''){try{const d=await api('/api/browse?path='+encodeURIComponent(path));$('browser').hidden=false;$('browsePath').textContent=d.path||'Computer';$('browser').dataset.path=d.path;$('browser').dataset.parent=d.parent;$('folderList').replaceChildren(...d.folders.map(p=>makeButton('▸ '+(p.split(/[\\/]/).filter(Boolean).pop()||p),()=>browse(p))));$('useFolder').disabled=!d.path;}catch(e){$('openError').textContent=e.message;}}
function applyData(d){$('preannotationWeights').value=preferences.fields?.preannotationWeights??d.preannotation_weights??'';$('datasetStatistics').disabled=false;$('manageClasses').disabled=false;data=d;all=d.images;page=0;current=null;picture=null;boxes=[];blurs=[];selected=null;selectedBlur=null;drag=null;dirty=false;undo=[];redo=[];$('datasetName').textContent=d.folder;$('datasetName').title=d.folder;$('totalCount').textContent=all.length.toLocaleString();$('search').value='';$('statusFilter').value='all';$('classFilter').replaceChildren(new Option('All classes',''),...d.categories.map(c=>new Option(c.name,c.name)));for(const id of ['drawClass','editClass'])$(id).replaceChildren(...d.categories.map(c=>new Option(c.name,c.id)));restoreDatasetView(d);suggestions([d.folder]);filter();
const last = all.find(entry => entry.relative === preferences.view?.image && preferences.view?.folder === d.folder);
const resume = last ?? filtered[0] ?? all[0];
if(resume)load(resume.id,true);
else savePreferences();}
function filter(keep=false){if(!data)return;const q=$('search').value.toLowerCase().trim(),status=$('statusFilter').value,cls=$('classFilter').value;filtered=all.filter(e=>{const searchable=[e.name,e.relative,e.annotation,...e.annotation_ids,...e.classes].join(' ').toLowerCase();return(!q||searchable.includes(q))&&(!cls||e.classes.includes(cls))&&(status==='all'||status==='reviewed'&&e.reviewed||status==='unreviewed'&&!e.reviewed||status==='empty'&&!e.count||status==='missing'&&e.missing||status==='error'&&e.error);});if(!keep)page=0;page=Math.min(page,Math.max(0,Math.ceil(filtered.length/60)-1));renderList();progress();navigation();}
function progress(){const done=all.filter(e=>e.reviewed).length;$('footerReviewCount').textContent=`${done.toLocaleString()} reviewed`;$('reviewCount').textContent=`${done.toLocaleString()} / ${all.length.toLocaleString()} reviewed`;$('progressBar').style.width=100*done/Math.max(1,all.length)+'%';$('matchCount').textContent=filtered.length===all.length?'':`${filtered.length} found`;}
function renderList(){const list=$('imageList');list.replaceChildren();for(const e of filtered.slice(page*60,page*60+60)){const b=document.createElement('button');b.className='imageItem'+(current?.id===e.id?' selected':'');b.dataset.id=e.id;b.onclick=()=>load(e.id);const img=new Image();img.loading='lazy';img.src=imgURL(e.id,true);img.alt='';const meta=document.createElement('div');meta.className='meta';const title=document.createElement('b');title.textContent=e.name;title.title=e.name;const sub=document.createElement('small');sub.textContent=e.relative.split('/').slice(0,-1).join('/');sub.title=e.relative;const status=document.createElement('small');status.className=e.error||e.missing?'warn':'itemStatus';status.textContent=e.error?'⚠ Label needs attention':`${e.reviewed?'✓ Reviewed · ':''}${e.count} box${e.count===1?'':'es'}${e.missing?' · no label file':''}`;const counts=document.createElement('small');counts.className='suggestionCounts';renderSuggestionCounts(counts,e);meta.append(title,sub,status,counts);b.append(img,meta);list.append(b);}if(!filtered.length){const empty=document.createElement('div');empty.className='emptyLibrary';empty.textContent='No images match this search or filter.';list.append(empty);}$('pageInfo').textContent=filtered.length?`${page*60+1}–${Math.min(filtered.length,page*60+60)} of ${filtered.length}`:'0 results';$('pagePrev').disabled=!page;$('pageNext').disabled=(page+1)*60>=filtered.length;}
async function load(id,force=false){if(busy&&!force)return;if(dirty&&!force&&!confirm('Discard the unsaved edits on this image? Choose Cancel to save them first.'))return;const serial=++loadSerial;busy=true;updateSave();$('loading').hidden=false;try{const d=await api('/api/image?id='+id);const im=new Image();im.src=`/api/pixels?id=${id}&token=${encodeURIComponent(token)}&v=${d.revision}`;await im.decode();if(serial!==loadSerial)return;current=d;predictions=[];predictionRequest=null;privacySuggestions=[];privacyRequest=null;
$('predictionStatus').textContent='Dashed blue boxes are suggestions. Accept a box to edit or save it.';
picture=im;originalPicture=im;boxes=structuredClone(d.boxes);blurs=[];selected=null;selectedBlur=null;drag=null;dirty=false;undo=[];redo=[];if(previewURL)URL.revokeObjectURL(previewURL);previewURL=null;previewSerial++;$('welcome').hidden=true;$('imageName').textContent=d.name;$('imageName').title=d.name;$('imagePath').textContent=`${d.relative} · ${d.width} × ${d.height}`;$('imagePath').title=d.relative;$('formatInfo').textContent=`Source: ${d.format.toUpperCase()} · Save: ${d.saves.join(' + ')}`;$('backupInfo').textContent=d.history?`Last saved ${d.history.saved}`:'';const warnings=[d.error,d.formats_disagree?'YOLO and COCO differ for this image. Saving replaces both with the boxes displayed here.':'',...data.warnings.filter(w=>!w.startsWith('YOLO and COCO'))];$('notice').hidden=!warnings.some(Boolean);$('notice').textContent=warnings.filter(Boolean).join(' ');const index=filtered.findIndex(e=>e.id===id);if(index>=0)page=Math.floor(index/60);renderList();inspector();fit();navigation();canvas.focus({preventScroll:true});savePreferences();}catch(e){toast(e.message,true);}finally{if(serial===loadSerial){busy=false;$('loading').hidden=true;inspector();updateSave();maybePreannotate();maybeSuggestPrivacy();}}}
function navigation(){const i=filtered.findIndex(e=>e.id===current?.id);$('position').textContent=current?`${i>=0?i+1:'–'} / ${filtered.length} in view`:'No image selected';$('previous').disabled=!current||i<=0;$('next').disabled=!current||i<0||i>=filtered.length-1;$('previousUnmade').disabled=!current||!filtered.slice(0,Math.max(0,i)).some(e=>!e.reviewed);$('nextUnmade').disabled=!current||!filtered.slice(i+1).some(e=>!e.reviewed);}
function advance(direction,unmade=false){if(!current)return;const i=filtered.findIndex(e=>e.id===current.id);if(i<0){if(filtered.length)load(filtered[0].id);return;}for(let j=i+direction;j>=0&&j<filtered.length;j+=direction)if(!unmade||!filtered[j].reviewed){load(filtered[j].id);return;}toast(unmade?'No more unreviewed images in this direction.':'End of this view.');}
async function save(next = false) {
  if (!editable() || (!next && !dirty)) return;
  busy = true;
  updateSave();
  const nextId = filtered[filtered.findIndex(e => e.id === current.id) + 1]?.id;
  const reviewOnly = next && !dirty;
  try {
    const result = await api('/api/save', {
      id: current.id, revision: current.revision, boxes, blur: blurs,
      mark_reviewed: next, review_only: reviewOnly,
    });
    dirty = false;
    all[all.findIndex(e => e.id === current.id)] = result.entry;
    current = result.detail;
    filter(true);
    if (next && nextId) await load(nextId, true);
    else if (!reviewOnly) await load(current.id, true);
    toast(next ? (nextId ? 'Marked reviewed.' : 'Marked reviewed. End of this view.') : 'Edits saved.');
  } catch (error) {
    toast(error.message, true);
  } finally {
    busy = false;
    updateSave();
  }
}
function inspector(){updatePrivacyEffectVisibility();if(!data)return;renderPredictions();renderPrivacySuggestions();$('boxCount').textContent=boxes.length;$('annotationList').replaceChildren();for(const [i,b]of boxes.entries()){const row=document.createElement('div');row.className='annotationRow'+(selected===i?' active':'');const choose=makeButton('',()=>{selected=i;selectedBlur=null;setTool('select');inspector();draw();});const swatch=document.createElement('span');swatch.className='swatch';swatch.style.background=color(b.category);choose.append(swatch,document.createTextNode(`${i+1}. ${category(b.category).name}`));choose.title=`Annotation ${b.id} · ${b.bbox.map(Math.round).join(', ')}`;const del=makeButton('×',()=>{if(editable()){remember();boxes.splice(i,1);selected=null;selectedBlur=null;changed();}},'delete');del.title='Delete box';del.disabled=!editable();row.append(choose,del);$('annotationList').append(row);}const b=boxes[selected];$('boxEditor').hidden=!b;if(b){$('editClass').value=b.category;['coordX','coordY','coordW','coordH'].forEach((id,i)=>$(id).value=Math.round(b.bbox[i]*100)/100);}$('blurCount').textContent=blurs.length;$('blurList').replaceChildren(...blurs.map((r,i)=>{const row=document.createElement('div');
row.className='blurRow'+(selectedBlur===i?' active':'');
const select=makeButton(`Region ${i+1} · ${Math.round(r.bbox[2])} × ${Math.round(r.bbox[3])} · ${r.method === 'pixelate' ? 'Pixelate' : 'Blur'} ${r.radius}px`,()=>{
  selected=null;selectedBlur=i;setTool('select');inspector();draw();
});
select.setAttribute('aria-pressed', String(selectedBlur===i));
if(selectedBlur===i){
  $('privacyEffect').value=r.method || 'gaussian';
  $('blurRadius').value=r.radius;
  updatePrivacyStrength();
}
const remove=makeButton('×',()=>{if(!editable())return;remember();blurs.splice(i,1);selectedBlur=null;changed();});
remove.setAttribute('aria-label', `Delete blur region ${i+1}`);
remove.disabled=!editable();
row.append(select,remove);
return row;}));$('previewBlur').disabled=!blurs.length||busy;$('blurHint').textContent=blurs.length?'Pending blur regions. Preview to inspect; Save applies them to the image.':'Saved blur changes the image pixels. An original copy is backed up first.';}
function canvasSize(){const r=$('canvasWrap').getBoundingClientRect(),dpr=devicePixelRatio||1;canvas.width=Math.round(r.width*dpr);canvas.height=Math.round(r.height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);draw();}
function fit(){if(!current)return;const r=canvas.getBoundingClientRect();scale=Math.min((r.width-40)/current.width,(r.height-40)/current.height);ox=(r.width-current.width*scale)/2;oy=(r.height-current.height*scale)/2;draw();}
function zoom(factor,cx=canvas.clientWidth/2,cy=canvas.clientHeight/2){if(!current)return;const next=Math.max(.02,Math.min(12,scale*factor));ox=cx-(cx-ox)*next/scale;oy=cy-(cy-oy)*next/scale;scale=next;draw();}
function handles(b){const[x,y,w,h]=b.bbox;return [[x,y],[x+w/2,y],[x+w,y],[x+w,y+h/2],[x+w,y+h],[x+w/2,y+h],[x,y+h],[x,y+h/2]];}
function draw(){const r=canvas.getBoundingClientRect();ctx.clearRect(0,0,r.width,r.height);layoutPredictionLabels(r);if(!current||!picture)return;ctx.save();ctx.translate(ox,oy);ctx.scale(scale,scale);ctx.drawImage(picture,0,0,current.width,current.height);drawPrivacySuggestions();if(visible){drawPredictions();boxes.forEach((b,i)=>{const[x,y,w,h]=b.bbox;ctx.strokeStyle=color(b.category);ctx.fillStyle=color(b.category)+'16';ctx.lineWidth=(selected===i?2.5:1.6)/scale;ctx.fillRect(x,y,w,h);ctx.strokeRect(x,y,w,h);const label=`${i+1} ${category(b.category).name}`;ctx.font=`${12/scale}px Segoe UI`;const tw=ctx.measureText(label).width;const ly=Math.max(0,y-20/scale);ctx.fillStyle=color(b.category);ctx.fillRect(x,ly,tw+10/scale,20/scale);ctx.fillStyle='#102b2c';ctx.fillText(label,x+5/scale,ly+14/scale);if(selected===i) drawHandles(b,color(b.category));});}blurs.forEach((b,i)=>{ctx.strokeStyle='#d59dff';ctx.lineWidth=2/scale;ctx.setLineDash([7/scale,5/scale]);ctx.strokeRect(...b.bbox);ctx.setLineDash([]);ctx.fillStyle='#c483ed33';ctx.fillRect(...b.bbox);ctx.font=`${12/scale}px Segoe UI`;ctx.fillStyle='#e1b7ff';ctx.fillText(`BLUR ${i+1}`,b.bbox[0]+4/scale,b.bbox[1]+16/scale);if(selectedBlur===i) drawHandles(b,'#d59dff');});if(drag?.type==='draw'){ctx.strokeStyle=tool==='blur'?'#ddadff':'#32ecba';ctx.lineWidth=2/scale;ctx.setLineDash([5/scale,3/scale]);const p=drag.now;ctx.strokeRect(drag.start.x,drag.start.y,p.x-drag.start.x,p.y-drag.start.y);}ctx.restore();$('zoomValue').textContent=Math.round(scale*100)+'%';}
function point(e,clamp=true){const r=canvas.getBoundingClientRect();let x=(e.clientX-r.left-ox)/scale,y=(e.clientY-r.top-oy)/scale;if(clamp&&current){x=Math.max(0,Math.min(current.width,x));y=Math.max(0,Math.min(current.height,y));}return{x,y};}
let hoverHandle = -1;
const resizeCursors = ['nwse-resize', 'ns-resize', 'nesw-resize', 'ew-resize',
  'nwse-resize', 'ns-resize', 'nesw-resize', 'ew-resize'];

function selectedRegion() {
  return blurs[selectedBlur] ?? (visible ? boxes[selected] : null);
}

function hitHandle(region, p) {
  let closest = -1;
  let distance = 9;
  handles(region).forEach(([x, y], index) => {
    const candidate = Math.hypot((x-p.x)*scale, (y-p.y)*scale);
    if (candidate < distance) {
      closest = index;
      distance = candidate;
    }
  });
  return closest;
}

function drawHandles(region, stroke) {
  ctx.lineWidth = 1.5 / scale;
  handles(region).forEach(([x, y], index) => {
    const active = index === (drag?.type === 'resize' ? drag.handle : hoverHandle);
    const half = (active ? 6 : 4) / scale;
    ctx.fillStyle = active ? stroke : 'white';
    ctx.strokeStyle = active ? 'white' : stroke;
    ctx.fillRect(x-half, y-half, 2*half, 2*half);
    ctx.strokeRect(x-half, y-half, 2*half, 2*half);
  });
}

function updateRegionHover(event) {
  if (tool !== 'select' || !editable()) return;
  const p = point(event, false);
  const region = selectedRegion();
  const handle = region ? hitHandle(region, p) : -1;
  const overRegion = pick(blurs, p) >= 0 || (visible && pick(boxes, p) >= 0);
  canvas.style.cursor = handle >= 0 ? resizeCursors[handle] : overRegion ? 'move' : 'default';
  if (hoverHandle !== handle) {
    hoverHandle = handle;
    draw();
  }
}

function pick(regions, p) {
  const tolerance = 5 / scale;
  return regions.findLastIndex(({bbox: [x,y,w,h]}) =>
    p.x >= x-tolerance && p.x <= x+w+tolerance &&
    p.y >= y-tolerance && p.y <= y+h+tolerance);
}

canvas.addEventListener('pointerdown', event => {
  if (!current || busy || drag) return;
  canvas.focus();
  const p = point(event, false);
  if (space || tool === 'pan' || event.button === 1) {
    drag = {type: 'pan', x: event.clientX, y: event.clientY, ox, oy};
  } else if (event.button === 0 && editable()) {
    if (tool === 'box' || tool === 'blur') {
      if (p.x < 0 || p.y < 0 || p.x > current.width || p.y > current.height) return;
      drag = {type: 'draw', tool, start: p, now: p};
    } else {
      let region = selectedRegion();
      const handle = region ? hitHandle(region, p) : -1;
      hoverHandle = handle;
      if (handle < 0) {
        const blurIndex = pick(blurs, p);
        const boxIndex = visible && blurIndex < 0 ? pick(boxes, p) : -1;
        selectedBlur = blurIndex >= 0 ? blurIndex : null;
        selected = boxIndex >= 0 ? boxIndex : null;
        region = blurs[selectedBlur] ?? boxes[selected];
      }
      if (region) {
        drag = {type: handle >= 0 ? 'resize' : 'move', region, handle,
          start: p, old: region.bbox.slice(), before: snapshot()};
      }
      inspector();
      draw();
    }
  }
  if (drag) {
    if (drag.type === 'move') canvas.style.cursor = 'move';
    if (drag.type === 'resize') canvas.style.cursor = resizeCursors[drag.handle];
    drag.pointerId = event.pointerId;
    canvas.setPointerCapture(event.pointerId);
    updateSave();
    event.preventDefault();
  }
});

function moveDrag(event) {
  if (!drag || event.pointerId !== drag.pointerId) return;
  if (drag.type === 'pan') {
    ox = drag.ox + event.clientX - drag.x;
    oy = drag.oy + event.clientY - drag.y;
    return;
  }
  const p = point(event);
  if (drag.type === 'draw') {
    drag.now = p;
    return;
  }
    const unbounded = point(event, false);
  const dx = unbounded.x - drag.start.x;
  const dy = unbounded.y - drag.start.y;
  if (!drag.moved && Math.hypot(dx, dy) * scale < 1) return;
  drag.moved = true;
  let [x,y,w,h] = drag.old;
  if (drag.type === 'move') {
    x = Math.max(0, Math.min(current.width - w, x + dx));
    y = Math.max(0, Math.min(current.height - h, y + dy));
  } else {
    let right = x+w, bottom = y+h;
    const minWidth = Math.min(1, w), minHeight = Math.min(1, h);
    const handle = drag.handle;
    if ([0,6,7].includes(handle)) x = Math.max(0, Math.min(x+dx, right-minWidth));
    if ([2,3,4].includes(handle)) right = Math.min(current.width, Math.max(right+dx, x+minWidth));
    if ([0,1,2].includes(handle)) y = Math.max(0, Math.min(y+dy, bottom-minHeight));
    if ([4,5,6].includes(handle)) bottom = Math.min(current.height, Math.max(bottom+dy, y+minHeight));
    w = right-x;
    h = bottom-y;
  }
  drag.region.bbox = [x,y,w,h];
}

canvas.addEventListener('pointermove', event => {
  moveDrag(event);
  if (drag) draw();
  else updateRegionHover(event);
});

function endDrag(event) {
  if (!drag || event.pointerId !== drag.pointerId) return;
  const cancelled = event.type !== 'pointerup';
  if (!cancelled) moveDrag(event);
  const finished = drag;
  drag = null;
  hoverHandle = -1;
  updateRegionHover(event);
  if (cancelled && finished.region) {
    finished.region.bbox = finished.old;
  } else if (!cancelled && finished.type === 'draw') {
    const {start, now} = finished;
    const bbox = [Math.min(start.x,now.x), Math.min(start.y,now.y),
      Math.abs(start.x-now.x), Math.abs(start.y-now.y)];
    if (bbox[2]>=1 && bbox[3]>=1) {
      remember();
      if (finished.tool === 'blur') {
        blurs.push({bbox, method: $('privacyEffect').value, radius: Number($('blurRadius').value)});
        selectedBlur = blurs.length-1;
        selected = null;
      } else {
        boxes.push({id:'new-'+crypto.randomUUID(), category:Number($('drawClass').value), bbox});
        selected = boxes.length-1;
        selectedBlur = null;
      }
      setTool('select');
      changed();
      return;
    }
  } else if (!cancelled && finished.region && finished.moved && snapshot() !== finished.before) {
    remember(finished.before);
    changed();
    return;
  }
  updateSave();
  inspector();
  draw();
}

for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) {
  canvas.addEventListener(type, endDrag);
}
canvas.addEventListener('pointerleave', () => {
  hoverHandle = -1;
  if (tool === 'select' && !drag) canvas.style.cursor = 'default';
  if (!drag) draw();
});
canvas.addEventListener('contextmenu', event => event.preventDefault());
canvas.addEventListener('wheel', event => {
  event.preventDefault();
  if (drag) return;
  const rect = canvas.getBoundingClientRect();
  zoom(Math.exp(-event.deltaY*.0015), event.clientX-rect.left, event.clientY-rect.top);
}, {passive:false});

function deleteSelected() {
  if (!editable() || (selected === null && selectedBlur === null)) return;
  remember();
  if (selectedBlur !== null) blurs.splice(selectedBlur,1);
  else boxes.splice(selected,1);
  selected = selectedBlur = null;
  changed();
}

$('discardChanges').onclick = () => {
  if (!editable() || !dirty || !confirm('Discard all unsaved changes to this image?')) return;
  boxes = structuredClone(current.boxes);
  blurs = [];
  selected = selectedBlur = null;
  undo = [];
  redo = [];
  changed();
  canvas.focus({preventScroll:true});
};
$('editClass').onchange=()=>{if(!editable()||!boxes[selected])return;const categoryId=Number($('editClass').value);
if(boxes[selected].category===categoryId)return;
remember();boxes[selected].category=categoryId;changed();};
function applyCoordinates(){if(!editable()||!boxes[selected])return;const box=['coordX','coordY','coordW','coordH'].map(id=>Number($(id).value));if(box.some(x=>!Number.isFinite(x))||box[0]<0||box[1]<0||box[2]<1||box[3]<1||box[0]+box[2]>current.width||box[1]+box[3]>current.height){toast('Keep the box inside the image.',true);inspector();return;}if(box.every((value,i)=>value===boxes[selected].bbox[i]))return;remember();boxes[selected].bbox=box;changed();}
$('applyCoordinates').onclick=applyCoordinates;for(const id of ['coordX','coordY','coordW','coordH'])$(id).onchange=applyCoordinates;
$('previewBlur').onclick=async()=>{if(!editable()||!blurs.length)return;const version=++previewSerial;try{$('previewBlur').disabled=true;const r=await fetch('/api/blur-preview',{method:'POST',headers:{'X-Review-Token':token,'Content-Type':'application/json'},body:JSON.stringify({id:current.id,blur:blurs})});if(!r.ok)throw Error((await r.json()).error);const url=URL.createObjectURL(await r.blob());const image=new Image();image.src=url;await image.decode();if(version!==previewSerial){URL.revokeObjectURL(url);return;}if(previewURL)URL.revokeObjectURL(previewURL);previewURL=url;picture=image;draw();toast('Privacy preview ready. Save to apply it.');}catch(e){toast(e.message,true);}finally{$('previewBlur').disabled=!blurs.length;}};
function updatePrivacyStrength() {
  $('strengthLabel').textContent = $('privacyEffect').value === 'pixelate' ? 'Pixel block size' : 'Gaussian strength';
  $('radiusValue').textContent = $('blurRadius').value + ' px';
}
$('privacyEffect').onchange = () => {
  updatePrivacyStrength();
  const region = blurs[selectedBlur];
  const method = $('privacyEffect').value;
  if (!editable() || !region || (region.method || 'gaussian') === method) return;
  remember();
  region.method = method;
  changed();
};
$('blurRadius').oninput = updatePrivacyStrength;
$('blurRadius').onchange=()=>{
  const region=blurs[selectedBlur];
  const radius=Number($('blurRadius').value);
  if(!editable()||!region||region.radius===radius)return;
  remember();
  region.radius=radius;
  changed();
};
document.querySelectorAll('[data-tool]').forEach(b=>b.onclick=()=>setTool(b.dataset.tool));$('openFolder').onclick=$('welcomeOpen').onclick=openDialog;$('browseFolder').onclick=()=>browse($('folderPath').value);$('browseUp').onclick=()=>browse($('browser').dataset.parent);$('useFolder').onclick=()=>{$('folderPath').value=$('browser').dataset.path;$('browser').hidden=true;};
$('loadDataset').onclick=async()=>{if(dirty&&!confirm('Discard unsaved edits and open another dataset?'))return;$('loadDataset').disabled=true;$('loadDataset').textContent='Scanning dataset…';$('openError').textContent='';try{const d=await api('/api/open',{folder:$('folderPath').value,mode:$('formatMode').value});$('folderDialog').close();applyData(d);}catch(e){$('openError').textContent=e.message;}finally{$('loadDataset').disabled=false;$('loadDataset').textContent='Open dataset';}};
$('helpButton').onclick=()=>$('helpDialog').showModal();$('doneHelp').onclick=()=>$('helpDialog').close();$('search').oninput=()=>filter();$('statusFilter').onchange=$('classFilter').onchange=()=>filter();$('pagePrev').onclick=()=>{page--;renderList();};$('pageNext').onclick=()=>{page++;renderList();};$('previous').onclick=()=>advance(-1);$('next').onclick=()=>advance(1);$('previousUnmade').onclick=()=>advance(-1,true);$('nextUnmade').onclick=()=>advance(1,true);$('save').onclick=()=>save();$('saveNext').onclick=()=>save(true);$('deleteBox').onclick=deleteSelected;$('undo').onclick=()=>restoreStack(undo,redo);$('redo').onclick=()=>restoreStack(redo,undo);$('fit').onclick=fit;$('actual').onclick=()=>zoom(1/scale);$('zoomIn').onclick=()=>zoom(1.3);$('zoomOut').onclick=()=>zoom(1/1.3);$('toggleBoxes').onclick=()=>{visible=!visible;$('toggleBoxes').textContent=visible?'◉ Boxes':'○ Boxes';draw();};
window.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)||document.querySelector('dialog[open]'))return;if(e.code==='Space'){space=true;e.preventDefault();}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();save();}else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();restoreStack(e.shiftKey?redo:undo,e.shiftKey?undo:redo);}else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='y'){e.preventDefault();restoreStack(redo,undo);}else if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();deleteSelected();}else if(e.key==='ArrowLeft'){e.preventDefault();advance(-1);}else if(e.key==='ArrowRight'){e.preventDefault();advance(1);}else if(e.key==='Enter'){if(document.activeElement.matches('button:not(#saveNext)'))return;e.preventDefault();if(!e.repeat)save(true);}else if(e.key.toLowerCase()==='v')setTool('select');else if(e.key.toLowerCase()==='b'){if(e.shiftKey&&!e.ctrlKey&&!e.metaKey&&!e.altKey){e.preventDefault();if(!e.repeat)acceptAllPredictions();}else if(!e.shiftKey)setTool('box');}else if(e.key.toLowerCase()==='g'){if(e.shiftKey){e.preventDefault();if(!e.repeat)acceptPrivacy(pendingPrivacySuggestions());}else setTool('blur');}else if(e.key.toLowerCase()==='h')$('toggleBoxes').click();else if(e.key==='Escape'){selected=selectedBlur=null;setTool('select');inspector();draw();}});window.addEventListener('keyup',e=>{if(e.code==='Space')space=false;});window.addEventListener('blur',()=>space=false);window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});new ResizeObserver(canvasSize).observe($('canvasWrap'));


let classSuggestion = null;

function classOptions() {
  classSuggestion = null;
  $('classAction').disabled = false;
  $('classAction').replaceChildren(new Option('Add a new class', ''),
    ...data.categories.map(c => new Option(c.name, c.id)));
  $('classAction').onchange();
}

$('manageClasses').onclick = () => {
  if (busy || !data) return;
  if (dirty) { toast('Save your image edits before managing classes.', true); return; }
  classOptions();
  $('classError').textContent = '';
  $('classDialog').showModal();
};
$('classAction').onchange = () => {
  const id = $('classAction').value;
  $('className').value = id === '' ? '' : category(Number(id)).name;
  $('startRemoveClass').hidden = id === '';
  $('removeClassPanel').hidden = true;
  $('removeClassConfirmation').disabled = true;
  $('removeClassConfirmation').value = '';
  $('className').disabled = false;
  $('saveClass').textContent = 'Save class';
  $('classError').textContent = '';
};
function updateRemovalPhrase() {
  const id = Number($('classAction').value);
  const handling = $('removeClassHandling').value;
  const target = category(Number($('removeClassTarget').value));
  let instruction = 'KEEP ALL ANNOTATIONS';
  if (handling === 'reassign') instruction = `REASSIGN ALL ANNOTATIONS TO ${target.id}: ${target.name}`;
  if (handling === 'delete') instruction = 'DELETE ALL ANNOTATIONS IN THIS CLASS';
  $('removeClassTargetLabel').hidden = handling !== 'reassign';
  $('removeClassPhrase').textContent = `REMOVE CLASS ${id}: ${category(id).name} | ${instruction}`;
  $('removeClassConfirmation').value = '';
}
$('removeClassHandling').onchange = $('removeClassTarget').onchange = updateRemovalPhrase;
$('startRemoveClass').onclick = () => {
  const id = Number($('classAction').value);
  $('removeClassTarget').replaceChildren(...data.categories.filter(c => c.id !== id)
    .map(c => new Option(c.name, c.id)));
  $('removeClassHandling').value = 'keep';
  $('removeClassPanel').hidden = false;
  $('className').disabled = true;
  $('saveClass').textContent = 'Confirm removal';
  $('removeClassConfirmation').disabled = false;
  updateRemovalPhrase();
  $('removeClassConfirmation').focus();
};
$('removeClassConfirmation').onpaste = event => event.preventDefault();
$('removeClassConfirmation').ondrop = event => event.preventDefault();
$('closeClasses').onclick = () => $('classDialog').close();
$('classForm').onsubmit = async event => {
  event.preventDefault();
  if (busy) return;
  const id = $('classAction').value;
  const imageId = current?.id;
  const suggestion = classSuggestion;
  const previousFilter = $('classFilter').value;
  const filterId = data.categories.find(c => c.name === previousFilter)?.id;
  const drawId = $('drawClass').value;
  busy = true;
  $('saveClass').disabled = true;
  $('classError').textContent = '';
  updateSave();
  try {
    const removing = !$('removeClassPanel').hidden;
    data = await api('/api/classes', removing
      ? {action: 'remove', id: Number(id), handling: $('removeClassHandling').value,
          target_id: Number($('removeClassTarget').value), confirmation: $('removeClassConfirmation').value}
      : {id: id === '' ? null : Number(id), name: $('className').value});
    all = data.images;
    $('classFilter').replaceChildren(new Option('All classes', ''),
      ...data.categories.map(c => new Option(c.name, c.name)));
    $('classFilter').value = data.categories.find(c => c.id === filterId)?.name ?? '';
    for (const field of ['drawClass', 'editClass']) {
      $(field).replaceChildren(...data.categories.map(c => new Option(c.name, c.id)));
    }
    $('drawClass').value = id === '' ? String(data.categories.at(-1).id) : data.categories.some(c => String(c.id) === drawId) ? drawId : String(data.categories[0].id);
    filter(true);
    if (suggestion) {
      // Adding a class can change the COCO revision. Refresh the saved baseline
      // while keeping pending boxes, blur regions, and the undo history.
      current = await api('/api/image?id=' + imageId);
      const newClass = data.categories.at(-1);
      for (const prediction of predictions) {
        if (prediction.name === suggestion.name && prediction.category === null) {
          prediction.category = newClass.id;
        }
      }
    } else if (imageId !== undefined) {
      await load(imageId, true);
    }
    $('classDialog').close();
    toast(removing ? 'Class removed from the editor.' : id === '' ? 'Class added.' : 'Class renamed.');
  } catch (error) {
    $('classError').textContent = error.message;
  } finally {
    busy = false;
    $('saveClass').disabled = false;
    inspector();
    draw();
    updateSave();
  }
};
const statisticCounts = ['images', 'positive', 'negative', 'missing_annotations',
  'invalid_annotations', 'unreadable_images', 'instances'];
const statisticLabels = ['Images', 'Positive', 'Negative', 'Missing labels',
  'Invalid labels', 'Unreadable', 'Instances'];
const statisticTables = [];

function statisticsTable(title, columns, rows) {
  statisticTables.push({title, columns, rows});
  const heading = document.createElement('h3');
  heading.textContent = title;
  const table = document.createElement('table');
  const head = table.createTHead().insertRow();
  for (const label of columns) {
    const cell = document.createElement('th');
    cell.scope = 'col';
    cell.textContent = label;
    head.append(cell);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const value of row) tr.insertCell().textContent = typeof value === 'number'
      ? value.toLocaleString(undefined, {maximumFractionDigits: 6}) : value;
  }
  const scroll = document.createElement('div');
  scroll.className = 'statisticsTable';
  scroll.append(table);
  $('statisticsResults').append(heading, scroll);
}

function renderStatistics(report) {
  const result = $('statisticsResults');
  result.replaceChildren();
  statisticTables.length = 0;
  statisticsTable('Dataset and splits', ['Split', ...statisticLabels],
    [['All images', ...statisticCounts.map(key => report.totals[key] ?? 0)],
      ...Object.entries(report.splits).map(([name, counts]) =>
        [name, ...statisticCounts.map(key => counts[key] ?? 0)])]);
  statisticsTable('Class distribution', ['Class', 'Images containing class', 'Instances', ...Object.keys(report.splits)],
    report.classes.map(c => [c.name, c.images, c.instances, ...Object.values(c.instances_by_split)]));
  statisticsTable('Image resolution', ['Width × height (px)', 'Images'], Object.entries(report.resolutions));
  statisticsTable('Instances per labeled image', ['Instances', 'Images'], Object.entries(report.instances_per_image));
  statisticsTable('Bounding-box area as % of image', ['Class', ...Object.keys(report.boxes.area_bins)],
    [['All boxes', ...Object.values(report.boxes.area_bins)],
      ...report.classes.map(c => [c.name, ...Object.values(c.area_bins)])]);
  const measures = {width_px: 'Width (px)', height_px: 'Height (px)',
    width_height_ratio: 'Width / height', area_percent: 'Area (%)'};
  const quantileKeys = ['min', 'q1', 'median', 'q3', 'mean', 'max'];
  statisticsTable('Bounding-box geometry', ['Class', 'Measure', ...quantileKeys],
    [{name: 'All boxes', ...report.boxes}, ...report.classes].flatMap(c =>
      Object.entries(measures).map(([key, label]) =>
        [c.name, label, ...quantileKeys.map(q => c[key]?.[q] ?? '—')])));
  const centerHeading = document.createElement('h3');
  centerHeading.textContent = 'Box centers in the image';
  const centerClass = document.createElement('select');
  centerClass.setAttribute('aria-label', 'Box center distribution by class');
  const groups = [{name: 'All boxes', ...report.boxes}, ...report.classes];
  groups.forEach((group, index) => centerClass.add(new Option(group.name, index)));
  const grid = document.createElement('div');
  grid.className = 'statisticsGrid';
  centerClass.onchange = () => {
    const cells = groups[Number(centerClass.value)].center_grid.flat();
    const max = Math.max(1, ...cells);
    grid.replaceChildren(...cells.map((count, i) => {
      const cell = document.createElement('span');
      cell.title = `Row ${Math.floor(i / 10) + 1}, column ${i % 10 + 1}: ${count} centers`;
      cell.textContent = count;
      const strength = count / max;
      cell.style.background = `rgba(0, 110, 115, ${.05 + .95 * strength})`;
      if (strength > .65) cell.style.color = 'white';
      return cell;
    }));
  };
  centerClass.onchange();
  result.append(centerHeading, centerClass, grid);
  const metadataRows = Object.entries(report.metadata_distributions).flatMap(([field, values]) =>
    Object.entries(values).map(([value, count]) => [field, value, count]));
  statisticsTable('Capture metadata', ['Field', 'Value', 'Images'], metadataRows);
  const overlaps = Object.entries(report.groups_in_multiple_splits).flatMap(([field, groups]) =>
    Object.entries(groups).map(([group, splits]) => [field, group, splits.join(', ')]));
  statisticsTable('Recorded route or sequence IDs shared across splits', ['Field', 'ID', 'Splits'], overlaps);
  if (report.issues.length) {
    statisticsTable('Data quality issues', ['Image', 'Issue'],
      report.issues.map(issue => [issue.image, issue.problem]));
  }
  const notes = document.createElement('ul');
  for (const note of report.notes) {
    const item = document.createElement('li');
    item.textContent = note;
    notes.append(item);
  }
  result.append(notes);
}

function downloadStatistics(filename, content, mime) {
  const url = URL.createObjectURL(new Blob([content], {type: mime}));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$('datasetStatistics').onclick = () => {
  if (busy || !data) return;
  $('statisticsDialog').showModal();
  $('statisticsResults').replaceChildren();
  $('statisticsExports').hidden = true;
  $('recalculateStatistics').textContent = 'Calculate statistics';
  loadStatistics(false);
};

$('recalculateStatistics').onclick = () => loadStatistics(true);

async function loadStatistics(recalculate) {
  if (busy) return;
  const status = $('statisticsStatus');
  const button = $('recalculateStatistics');
  const errorMessage = $('statisticsError');
  status.setAttribute('aria-busy', 'true');
  if (!recalculate) status.textContent = 'Looking for saved statistics…';
  const previousButtonText = button.textContent;
  button.textContent = recalculate ? 'Calculating…' : previousButtonText;
  button.disabled = true;
  errorMessage.textContent = '';
  busy = true;
  updateSave();
  try {
    const {report, path} = await api('/api/statistics', recalculate ? {} : undefined);
    if (!report) {
      status.textContent = 'No saved statistics yet. Click Calculate statistics to create the report.';
      return;
    }
    renderStatistics(report);
    const timestamp = new Date(report.generated_at).toLocaleString(undefined, {
      dateStyle: 'medium', timeStyle: 'long',
    });
    status.textContent = `Saved report · Calculated ${timestamp} · Format: ${report.annotation_mode}. `
      + (report.annotation_mode === data.mode ? '' : 'This report uses a different annotation mode. ')
      + 'Recalculate to include changes made since that time.';
    status.title = path;
    $('statisticsExports').hidden = false;
    $('statisticsJson').onclick = () =>
      downloadStatistics('dataset-statistics.json', JSON.stringify(report, null, 2), 'application/json');
    $('statisticsCsv').onclick = () => {
      const quote = value => {
        const text = String(value);
        const safe = typeof value === 'string' && /^[=+@\-\t\r]/.test(text) ? "'" + text : text;
        return '"' + safe.replaceAll('"', '""') + '"';
      };
      const csv = statisticTables.flatMap(({title, columns, rows}) =>
        [[title], columns, ...rows, []]).map(row => row.map(quote).join(',')).join('\r\n');
      downloadStatistics('dataset-statistics.csv', '\uFEFF' + csv, 'text/csv;charset=utf-8');
    };
  } catch (error) {
    if ($('statisticsExports').hidden) status.textContent = 'Saved statistics could not be displayed.';
    errorMessage.textContent = error.message;
  } finally {
    button.textContent = $('statisticsExports').hidden ? 'Calculate statistics' : 'Recalculate statistics';
    button.disabled = false;
    status.removeAttribute('aria-busy');
    busy = false;
    updateSave();
  }
}

const predictionLabels = new Map();
const predictionFilters = $('predictionFilters');
let predictionRequest = null;
function refreshPredictionControls() {
  renderPredictions();
  draw();
  maybePreannotate();
}
predictionFilters.onchange = refreshPredictionControls;
$('autoPreannotation').onchange = refreshPredictionControls;
$('suppressOverlappingPredictions').onchange = refreshPredictionControls;
for (const field of ['preannotationWeights', 'preannotationConfidence']) {
  $(field).onchange = refreshPredictionControls;
}

function predictionClass(name) {
  const key = name.toLowerCase().replace(/^\d+[\s:._-]+/, '').replace(/[^a-z0-9]/g, '');
  if (['pothole', 'potholes'].includes(key)) return 'pothole';
  if (['manhole', 'manholes', 'manholecover'].includes(key)) return 'manhole';
  if (['speedbump', 'speedbumps', 'speedreducingbump'].includes(key)) return 'speedbump';
  return 'other';
}

function enabledPredictionClasses() {
  return new Set([...predictionFilters.querySelectorAll('input:checked')].map(input => input.value));
}

function coversAnnotation([x,y,w,h], [ax,ay,aw,ah]) {
  if (aw <= 0 || ah <= 0) return false;
  const width = Math.max(0, Math.min(x+w, ax+aw) - Math.max(x, ax));
  const height = Math.max(0, Math.min(y+h, ay+ah) - Math.max(y, ay));
  return width * height > .7 * aw * ah;
}

function pendingPredictions() {
  if (!$('autoPreannotation').checked) return [];
  const accepted = new Set(boxes.map(box => box.id));
  const enabled = enabledPredictionClasses();
  const suppress = $('suppressOverlappingPredictions').checked;
  return predictions.filter(prediction => !accepted.has(prediction.id)
    && enabled.has(predictionClass(prediction.name))
    && (!suppress || !boxes.some(box => coversAnnotation(prediction.bbox, box.bbox))));
}

function acceptPrediction(prediction) {
  if (!editable() || !pendingPredictions().includes(prediction)) return;
  if (!data.categories.some(item => item.id === prediction.category)) {
    classOptions();
    classSuggestion = prediction;
    $('classAction').disabled = true;
    $('className').value = prediction.name.replace(/^\d+[\s:._-]+/, '').trim();
    $('classDialog').showModal();
    $('className').focus();
    return;
  }
  addPredictions([prediction]);
}

function acceptAllPredictions() {
  if (!editable()) return;
  const pending = pendingPredictions();
  const allowed = new Set(data.categories.map(item => item.id));
  const matched = pending.filter(item => allowed.has(item.category));
  if (matched.length) addPredictions(matched);
  else if (pending.length) acceptPrediction(pending[0]);
}

function addPredictions(items) {
  remember();
  for (const prediction of items) {
    boxes.push({id: prediction.id, category: prediction.category, bbox: prediction.bbox.slice()});
  }
  selected = boxes.length - 1;
  selectedBlur = null;
  setTool('select');
  changed();
  canvas.focus({preventScroll:true});
}

function drawPredictions() {
  ctx.save();
  ctx.strokeStyle = '#55d6ff';
  ctx.lineWidth = 2 / scale;
  ctx.setLineDash([6/scale, 4/scale]);
  for (const prediction of pendingPredictions()) ctx.strokeRect(...prediction.bbox);
  ctx.restore();
}

function privacyModelName(source) {
  const field = {
    'Dashcam YOLOv8': 'dashcamConfidence',
    'YuNet': 'faceConfidence',
    'OpenCV SSD face': 'ssdFaceConfidence',
    'YOLOv9 plate': 'plateConfidence',
    'YOLOv9-s plate': 'plateSConfidence',
  }[source];
  const label = field && $(field)?.labels?.[0]?.textContent;
  return label ? label.trim().replace(/\s*\(%\)$/, '') : source;
}

function modelVoteCount(sources) {
  sources = sources.map(privacyModelName);
  const count = document.createElement('span');
  count.className = 'modelVoteCount';
  count.title = ''; // Suppress the parent label's native tooltip.
  count.tabIndex = 0;
  count.textContent = `${sources.length} model${sources.length === 1 ? '' : 's'}`;
  count.setAttribute('aria-label', 'Contributing models: ' + sources.join(', '));
  let timer, tooltip;
  const hide = () => {
    clearTimeout(timer);
    tooltip?.remove();
    tooltip = null;
  };
  const show = () => {
    hide();
    timer = setTimeout(() => {
      if (!count.isConnected) return;
      tooltip = document.createElement('div');
      tooltip.className = 'modelVoteTooltip';
      tooltip.setAttribute('role', 'tooltip');
      for (const name of sources) {
        const line = document.createElement('div');
        line.textContent = name;
        tooltip.append(line);
      }
      document.body.append(tooltip);
      const anchor = count.getBoundingClientRect();
      const left = Math.max(4, Math.min(anchor.left, innerWidth-tooltip.offsetWidth-4));
      const top = anchor.bottom+tooltip.offsetHeight+8 <= innerHeight
        ? anchor.bottom+4 : Math.max(4, anchor.top-tooltip.offsetHeight-4);
      tooltip.style.left = left + 'px';
      tooltip.style.top = top + 'px';
    }, 1000);
  };
  count.addEventListener('pointerenter', show);
  count.addEventListener('pointerleave', hide);
  count.addEventListener('focus', show);
  count.addEventListener('blur', hide);
  count.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
  count.addEventListener('pointerdown', event => event.stopPropagation());
  return count;
}

function layoutPredictionLabels(viewport) {
  const pending = current && picture
    ? [...(visible ? pendingPredictions() : []), ...pendingPrivacySuggestions()] : [];
  const shown = new Set(pending.map(prediction => prediction.id));
  for (const [id, label] of predictionLabels) {
    if (!shown.has(id)) {
      label.querySelector(".modelVoteCount")?.dispatchEvent(new Event("blur"));
      label.remove();
      predictionLabels.delete(id);
    }
  }
  const placed = [];
  const margin = 4;
  const clamp = (value, max) => Math.max(margin, Math.min(value, max));
  for (const prediction of pending) {
    let label = predictionLabels.get(prediction.id);
    if (!label) {
      label = document.createElement('div');
      label.className = prediction.kind ? 'predictionLabel privacyLabel' : 'predictionLabel';
      if (prediction.weak) label.classList.add('weakPrivacy');
      const check = makeButton('✓', () => prediction.kind ? acceptPrivacy([prediction]) : acceptPrediction(prediction));
      check.setAttribute('aria-label', `Accept ${prediction.name} suggestion`);
      const text = document.createElement('span');
      text.textContent = prediction.kind
        ? `Blur ${prediction.name} ·`
        : `AI ${prediction.name} ${Math.round(prediction.confidence*100)}%`;
      label.append(check, text);
      if (prediction.kind) label.append(modelVoteCount(prediction.sources));
      $('predictionLabels').append(label);
      predictionLabels.set(prediction.id, label);
    }
    const check = label.firstElementChild;
    const missingClass = !prediction.kind && !data.categories.some(item => item.id === prediction.category);
    check.title = missingClass ? 'Add a dataset class before accepting' : 'Accept suggestion';
    check.setAttribute('aria-label', `${missingClass ? 'Add class for' : 'Accept'} ${prediction.name} suggestion`);
    const [x, y, w, h] = prediction.bbox;
    const left = ox + x*scale, top = oy + y*scale;
    const outside = left+w*scale < 0 || left > viewport.width ||
      top+h*scale < 0 || top > viewport.height;
    label.classList.toggle('offscreen', outside);
    label.title = outside ? 'Suggestion box is outside the current view' : prediction.name;
    label.style.visibility = 'visible';
    const width = label.offsetWidth, height = label.offsetHeight;
    const maxX = viewport.width - width - margin, maxY = viewport.height - height - margin;
    const px = clamp(left, maxX);
    const preferred = clamp(top-height-3 < margin ? top+3 : top-height-3, maxY);
    const candidates = [preferred];
    for (let offset = height+3; offset <= viewport.height; offset += height+3) {
      if (preferred+offset <= maxY) candidates.push(preferred+offset);
      if (preferred-offset >= margin) candidates.push(preferred-offset);
    }
    const py = candidates.find(candidate => placed.every(other =>
      px+width+2 <= other.x || other.x+other.width+2 <= px ||
      candidate+height+2 <= other.y || other.y+other.height+2 <= candidate));
    if (py === undefined || maxX < margin || maxY < margin) {
      label.style.visibility = 'hidden';
      continue;
    }
    label.style.left = px + 'px';
    label.style.top = py + 'px';
    placed.push({x:px, y:py, width, height});
  }
}

$('acceptPredictionsAll').onclick = acceptAllPredictions;

function renderPredictions() {
  updateSuggestionRows();
  const pending = pendingPredictions();
  $('predictionCount').textContent = pending.length;
  $('acceptPredictionsAll').hidden = !pending.length;
  $('acceptPredictionsAll').disabled = !editable();
  const list = $('predictionList');
  list.replaceChildren();
  for (const prediction of pending) {
    const row = document.createElement('div');
    row.className = 'predictionRow';
    const name = document.createElement('strong');
    name.textContent = `${prediction.name} · ${Math.round(prediction.confidence*100)}%`;
    const missingClass = !data.categories.some(item => item.id === prediction.category);
    const accept = makeButton(missingClass ? 'Add class…' : 'Accept', () => acceptPrediction(prediction));
    accept.disabled = !editable();
    const dismiss = makeButton('Dismiss', () => {
      predictions = predictions.filter(item => item !== prediction);
      renderPredictions();
      draw();
    }, 'quiet');
    dismiss.disabled = !editable();
    row.append(name, accept, dismiss);
    list.append(row);
  }
}

async function maybePreannotate() {
  const status = $('predictionStatus');
  if (!$('autoPreannotation').checked || !enabledPredictionClasses().size) {
    status.textContent = 'Enable preannotation and select at least one class to see suggestions.';
    return;
  }
  if (!editable()) return;
  const image = current;
  const weights = $('preannotationWeights').value.trim();
  const confidence = Number($('preannotationConfidence').value) / 100;
  const key = JSON.stringify([loadSerial, weights, confidence]);
  if (predictionRequest?.key === key) {
    status.textContent = predictionRequest.message;
    return;
  }
  const request = {key, pending: true, options: {weights, confidence},
    endpoint: '/api/preannotate', message: 'Running the model on this image…'};
  predictionRequest = request;
  status.textContent = request.message;
  predictions = [];
  renderPredictions();
  draw();
  try {
    const result = await api('/api/preannotate', {
      id: image.id, revision: image.revision, folder: data.folder, ...request.options,
    });
    if (predictionRequest !== request) return;
    predictions = result.predictions.map(item => ({...item, id: 'new-' + crypto.randomUUID()}));
    request.pending = false;
    request.message = `${result.cached ? 'Saved · ' : ''}${predictions.length} model detections · ${result.device} · ${result.seconds} s`;
  } catch (error) {
    if (predictionRequest !== request) return;
    predictionRequest = null;
    if ($('autoPreannotation').checked && enabledPredictionClasses().size) status.textContent = error.message;
    return;
  }
  refreshPredictionControls();
}

let privacySuggestions = [];
let privacyRequest = null;

function privacyTypes() {
  return new Set([...$('privacyTypes').querySelectorAll('input:checked')].map(input => input.value));
}

function visiblePrivacyCandidate(region, types) {
  return types.has(region.kind) &&
    (!region.already_pixelated || !$('hidePixelatedPlates').checked) &&
    (!$('hideUnverifiedFaces').checked || (region.face_features !== false && region.human_context !== false)) &&
    (region.plate_text_readable !== false || !$('hideUnreadablePlates').checked);
}

function pendingPrivacySuggestions() {
  if (!$('autoPrivacy').checked) return [];
  const types = privacyTypes();
  return privacySuggestions.filter(region => visiblePrivacyCandidate(region, types) &&
    !blurs.some(blur => blur.suggestionId === region.id || coversAnnotation(blur.bbox, region.bbox)));
}

function acceptPrivacy(suggestions) {
  if (!editable()) return;
  const pending = pendingPrivacySuggestions();
  const accepted = suggestions.filter(item => pending.includes(item));
  if (!accepted.length) return;
  remember();
  for (const region of accepted) {
    const method = $('privacyEffect').value;
    const radius = method === 'pixelate' ? Number($('blurRadius').value) : region.radius;
    blurs.push({bbox: region.bbox.slice(), method, radius, suggestionId: region.id});
  }
  selected = null;
  selectedBlur = blurs.length-1;
  setTool('select');
  changed();
  canvas.focus({preventScroll:true});
}

function drawPrivacySuggestions() {
  ctx.save();
  ctx.strokeStyle = '#f5a4ef';
  ctx.lineWidth = 2 / scale;
  ctx.setLineDash([3/scale, 5/scale]);
  for (const region of pendingPrivacySuggestions()) {
    ctx.strokeStyle = region.weak ? '#fce0f8' : '#f5a4ef';
    ctx.strokeRect(...region.bbox);
  }
  ctx.restore();
}

function renderPrivacySuggestions() {
  updateSuggestionRows();
  const pending = pendingPrivacySuggestions();
  $('acceptPrivacyAll').disabled = !pending.length || !editable();
  const list = $('privacyList');
  list.replaceChildren();
  for (const region of pending) {
    const row = document.createElement('div');
    row.className = 'predictionRow privacyRow' + (region.weak ? ' weakPrivacy' : '');
    const description = document.createElement('div');
    description.textContent = `${region.name} · ${region.sources.map(privacyModelName).join(' + ')}${Math.round(region.confidence * 100)}% · ${region.vehicle_context === false ? 'no vehicle detected' : region.weak ? 'weaker' : 'detector score'}`;
    const accept = makeButton('Accept blur', () => acceptPrivacy([region]));
    const dismiss = makeButton('Dismiss', () => {
      privacySuggestions = privacySuggestions.filter(item => item !== region);
      renderPrivacySuggestions();
      draw();
    }, 'quiet');
    accept.disabled = dismiss.disabled = !editable();
    row.append(description, accept, dismiss);
    list.append(row);
  }
}

async function maybeSuggestPrivacy() {
  const status = $('privacyStatus');
  if (!$('autoPrivacy').checked || !privacyTypes().size) {
    status.textContent = 'Enable automatic blur suggestions and select faces or plates.';
    return;
  }
  if (!editable()) return;
  const thresholds = {
    'Dashcam YOLOv8': Number($('dashcamConfidence').value) / 100,
    'YuNet': Number($('faceConfidence').value) / 100,
    'YOLOv9 plate': Number($('plateConfidence').value) / 100,
    'OpenCV SSD face': Number($('ssdFaceConfidence').value) / 100,
    'YOLOv9-s plate': Number($('plateSConfidence').value) / 100,
  };
  const key = JSON.stringify([loadSerial, thresholds]);
  if (privacyRequest?.key === key) {
    status.textContent = privacyRequest.message;
    return;
  }
  const image = current;
  const request = {key, pending: true, options: {thresholds},
    endpoint: '/api/privacy-suggestions', message: 'Running the privacy detectors…'};
  privacyRequest = request;
  status.textContent = request.message;
  privacySuggestions = [];
  renderPrivacySuggestions();
  draw();
  try {
    const result = await api('/api/privacy-suggestions', {
      id: image.id, revision: image.revision, folder: data.folder, ...request.options,
    });
    if (privacyRequest !== request) return;
    privacySuggestions = result.regions.map(region => ({
      ...region, id: 'privacy-' + crypto.randomUUID(),
      name: region.kind === 'face' ? 'face' : 'license plate',
    }));
    request.pending = false;
    request.failed = result.detectors.length === 0;
    request.message = `${result.cached ? 'Saved · ' : ''}${result.regions.length} proposals · ${result.detectors.length}/5 detectors · ${result.seconds} s`;
    if (result.warnings.length) request.message += '. ' + result.warnings.join(' · ');
  } catch (error) {
    if (privacyRequest !== request) return;
    request.message = error.message;
    privacyRequest = null;
  }
  if ($('autoPrivacy').checked && privacyTypes().size) status.textContent = request.message;
  renderPrivacySuggestions();
  draw();
}

function refreshPrivacySuggestions() {
  renderPrivacySuggestions();
  draw();
  maybeSuggestPrivacy();
}
for (const id of ['autoPrivacy', 'privacySettings']) $(id).onchange = refreshPrivacySuggestions;
$('acceptPrivacyAll').onclick = () => acceptPrivacy(pendingPrivacySuggestions());

const prefetchedPredictions = new Map();
const prefetchIssues = new Map();

function suggestionCacheKey(folder, mode, request, image) {
  const namespace = JSON.stringify([folder, mode, request.endpoint, request.options]);
  return JSON.stringify([namespace, image.relative]);
}

function renderSuggestionCounts(target, image) {
  target.replaceChildren();
  const selectedImage = image.id === current?.id;
  const classes = enabledPredictionClasses();
  const types = privacyTypes();
  const cached = request => request && !request.pending &&
    prefetchedPredictions.get(suggestionCacheKey(data.folder, data.mode, request, image));
  const road = $('autoPreannotation').checked && classes.size && (selectedImage
    ? pendingPredictions().length
    : cached(predictionRequest)?.predictions?.filter(item => classes.has(predictionClass(item.name))).length);
  const privacy = $('autoPrivacy').checked && types.size && (selectedImage
    ? pendingPrivacySuggestions().length
    : cached(privacyRequest)?.regions?.filter(region => visiblePrivacyCandidate(region, types)).length);
  for (const [count, kind, label] of [
    [privacy, 'privacy', 'privacy box'], [road, 'annotation', 'new annotation'],
  ]) {
    if (!count) continue;
    const badge = document.createElement('span');
    badge.className = 'suggestionCount ' + kind;
    badge.textContent = `${count} ${label}${count === 1 ? '' : kind === 'privacy' ? 'es' : 's'} suggested`;
    if (!selectedImage && kind === 'annotation') {
      badge.title = 'Candidate count before checking overlap with existing annotations.';
    }
    target.append(badge);
  }
}

function updateSuggestionRows() {
  const rows = $('imageList').querySelectorAll('.imageItem');
  filtered.slice(page*60, page*60+60).forEach((image, index) => {
    const row = rows[index];
    if (row?.dataset.id === String(image.id)) {
      renderSuggestionCounts(row.querySelector('.suggestionCounts'), image);
    }
  });
}
const restoredPredictionNamespaces = new Set();
let restoringPredictions = false;

async function restorePredictionProgress(dataset, request, images, namespace) {
  restoringPredictions = true;
  try {
    for (let start = 0; start < images.length; start += 200) {
      if (dataset !== data || !$('backgroundPredictions').checked ||
          ![predictionRequest, privacyRequest].some(active => active &&
            suggestionCacheKey(dataset.folder, dataset.mode, active, {relative: ''}) === namespace)) return;
      const batch = images.slice(start, start + 200);
      const hits = await api(request.endpoint, {
        cache_ids: batch.map(image => image.id), folder: dataset.folder, ...request.options,
      });
      for (const image of batch) {
        if (Object.hasOwn(hits, image.id)) {
          prefetchedPredictions.set(suggestionCacheKey(dataset.folder, dataset.mode, request, image), hits[image.id]);
        }
      }
      if (dataset === data) updateSuggestionRows();
    }
    restoredPredictionNamespaces.add(namespace);
  } catch (error) {
    // Individual queue requests can still reuse the disk cache if bulk loading fails.
    console.warn('Could not restore prediction progress', error);
    restoredPredictionNamespaces.add(namespace);
  } finally {
    restoringPredictions = false;
  }
}

let prefetchStartedAt = 0;
let prefetchDelayUntil = 0;
let prefetchWait = '';

async function prefetchPredictions() {
  const status = $('prefetchStatus');
  const roadEnabled = $('autoPreannotation').checked && enabledPredictionClasses().size;
  const privacyEnabled = $('autoPrivacy').checked && privacyTypes().size;
  status.hidden = !$('backgroundPredictions').checked || !current || !data?.prediction_cache || !(roadEnabled || privacyEnabled);
  if (status.hidden) return;
  const requests = [roadEnabled ? predictionRequest : null,
    privacyEnabled ? privacyRequest : null].filter(Boolean);
  const available = requests.filter(request => !request.failed && !request.pending);
  const index = filtered.findIndex(image => image.id === current.id);
  const folder = data.folder;

  if (!busy && !requests.some(request => request.pending)) {
    for (const request of available) {
      const namespace = suggestionCacheKey(folder, data.mode, request, {relative: ''});
      if (restoredPredictionNamespaces.has(namespace)) continue;
      status.textContent = 'Restoring saved prediction progress…';
      if (!restoringPredictions) {
        void restorePredictionProgress(data, request, all.filter(image => !image.error), namespace);
      }
      return;
    }
  }

  let remaining = 0;
  let issueCount = 0;
  let latestIssue = '';
  let nextJob;
  for (let i = index < 0 ? filtered.length : index+1; i < filtered.length; i++) {
    const image = filtered[i];
    if (image.error) continue;
    let needsWork = false;
    let imageIssue = '';
    for (let j = 0; j < available.length; j++) {
      const key = suggestionCacheKey(folder, data.mode, available[j], image);
      if (prefetchIssues.has(key)) imageIssue = prefetchIssues.get(key);
      if (prefetchedPredictions.has(key) || prefetchIssues.has(key)) continue;
      needsWork = true;
      nextJob ??= {image, request: available[j], key};
    }
    if (needsWork) remaining++;
    if (imageIssue) { issueCount++; latestIssue = imageIssue; }
  }
  const show = (state, count = true) => {
    const text = state + (count ? ` · ${remaining.toLocaleString()} images left to check or cache` : '') +
      (issueCount ? ` · ${issueCount.toLocaleString()} with detector issues` : '');
    status.title = latestIssue;
    if (status.textContent !== text) status.textContent = text;
  };
  if (busy || requests.some(request => request.pending)) {
    show('Background predictions waiting for current image', false);
    return;
  }
  if (!available.length) {
    show('Background predictions waiting for a detector', false);
    return;
  }
  if (prefetchStartedAt) {
    if (Date.now() - prefetchStartedAt > 750) show('Background predictions running');
    return;
  }
  if (!nextJob) { show('Background predictions ready'); return; }
  if (drag || document.hidden || document.querySelector('dialog[open]')) {
    show('Background predictions paused');
    return;
  }

  if (Date.now() < prefetchDelayUntil) {
    show(prefetchWait);
    return;
  }

  const {image, request, key} = nextJob;
  prefetchStartedAt = Date.now();
  if (!prefetchWait) show('Background predictions running');
  try {
    const result = await api(request.endpoint, {id: image.id, folder, background: true, ...request.options});
    if (result.deferred) {
      prefetchDelayUntil = Date.now() + 1800;
      prefetchWait = result.reason || 'Waiting for background inference capacity';
      show(prefetchWait);
    } else {
      prefetchWait = '';
      if (result.warnings?.length) prefetchIssues.set(key, result.warnings.join(' · '));
      prefetchedPredictions.set(key, result);
      updateSuggestionRows();
    }
  } catch (error) {
    prefetchIssues.set(key, error.message);
  } finally {
    prefetchStartedAt = 0;
  }
}

$('backgroundPredictions').onchange = prefetchPredictions;
setInterval(prefetchPredictions, 650);

const preferenceFields = ['backgroundPredictions', 'autoPreannotation', 'autoPrivacy', 'suppressOverlappingPredictions',
  'preannotationWeights', 'preannotationConfidence', 'dashcamConfidence', 'faceConfidence',
  'plateConfidence', 'ssdFaceConfidence', 'plateSConfidence', 'hidePixelatedPlates', 'hideUnreadablePlates', 'hideUnverifiedFaces', 'privacyEffect', 'blurRadius', 'formatMode'];
const preferenceGroups = ['predictionFilters', 'privacyTypes'];
const viewFields = ['search', 'statusFilter', 'classFilter', 'drawClass'];
let preferences = {};
let preferencesReady = false;
let preferenceWrite = Promise.resolve();
let lastPreferences = '';

function restorePreferences(saved) {
  preferences = saved;
  for (const id of preferenceFields) {
    const input = $(id);
    const value = saved.fields?.[id];
    if (input.type === 'checkbox' && typeof value === 'boolean') input.checked = value;
    else if (typeof value === 'string') input.value = value;
  }
  for (const id of preferenceGroups) {
    const enabled = saved.groups?.[id];
    if (!Array.isArray(enabled)) continue;
    for (const input of $(id).querySelectorAll('input[type=checkbox]')) {
      input.checked = enabled.includes(input.value);
    }
  }
  updatePrivacyStrength();
  visible = saved.boxesVisible !== false;
  $('toggleBoxes').textContent = visible ? '◉ Boxes' : '○ Boxes';
  if (saved.view?.folder) $('folderPath').value = saved.view.folder;
}

function restoreDatasetView(dataset) {
  const view = preferences.view;
  if (view?.folder !== dataset.folder) return;
  for (const id of viewFields) {
    const input = $(id);
    const value = view[id];
    if (typeof value === 'string' && (input.tagName !== 'SELECT' ||
      [...input.options].some(option => option.value === value))) input.value = value;
  }
}

function savePreferences() {
  if (!preferencesReady) return;
  const fields = Object.fromEntries(preferenceFields.map(id => {
    const input = $(id);
    return [id, input.type === 'checkbox' ? input.checked : input.value];
  }));
  const groups = Object.fromEntries(preferenceGroups.map(id =>
    [id, [...$(id).querySelectorAll('input:checked')].map(input => input.value)]));
  const view = data?.folder ? {
    folder: data.folder, mode: data.mode,
    image: current?.relative ?? (preferences.view?.folder === data.folder ? preferences.view.image : undefined),
    ...Object.fromEntries(viewFields.map(id => [id, $(id).value])),
  } : preferences.view;
  preferences = {fields, groups, view, boxesVisible: visible};
  const snapshot = preferences;
  const serialized = JSON.stringify(snapshot);
  if (serialized === lastPreferences) return;
  lastPreferences = serialized;
  preferenceWrite = preferenceWrite.then(() => api('/api/preferences', snapshot)).catch(error => {
    if (lastPreferences === serialized) lastPreferences = '';
    toast('Could not save review settings: ' + error.message, true);
  });
}

for (const id of [...preferenceFields, ...preferenceGroups, ...viewFields]) {
  $(id).addEventListener('change', savePreferences);
}
$('toggleBoxes').addEventListener('click', savePreferences);

Promise.all([api('/api/state'), api('/api/preferences').catch(() => ({}))])
  .then(async ([state, saved]) => {
    restorePreferences(saved);
    preferencesReady = true;
    if (state.folder) {
      applyData(state);
      return;
    }
    const folder = saved.view?.folder;
    suggestions([...new Set([folder, ...(state.suggested || [])].filter(Boolean))]);
    if (!folder) return;
    $('loading').textContent = 'Reopening previous dataset…';
    $('loading').hidden = false;
    try {
      const dataset = await api('/api/open', {
        folder, mode: saved.view.mode ?? $('formatMode').value,
      });
      $('loading').textContent = 'Loading image…';
      applyData(dataset);
      if (dataset.images.length) return;
    } catch (error) {
      toast('Could not reopen the previous dataset: ' + error.message, true);
    }
    $('loading').textContent = 'Loading image…';
    $('loading').hidden = true;
  }).catch(error => toast(error.message, true));
