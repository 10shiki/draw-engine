// Minimal SVG editor mirroring DrawPy concepts
(() => {
  const svg = document.getElementById('canvas');
  const content = document.getElementById('content');
  const handlesLayer = document.getElementById('handles');
  const marqueeRect = document.getElementById('marquee');

  const tools = Array.from(document.querySelectorAll('.tool'));
  const symbolKindSelect = document.getElementById('symbol-kind');
  const fillInput = document.getElementById('fill');
  const strokeInput = document.getElementById('stroke');
  const widthInput = document.getElementById('width');
  const newBtn = document.getElementById('new-doc');
  const openBtn = document.getElementById('open-json');
  const saveBtn = document.getElementById('save-json');
  const exportBtn = document.getElementById('export-svg');
  const fileInput = document.getElementById('file-input');

  const gridSize = 20;
  let state = {
    tool: 'select',
    symbol: 'pipe',
    nextId: 1,
    shapes: [], // {id,type,x,y,w,h,fill,outline,width,text,symbol}
    connectors: [], // {id,src,dst,arrow,stroke,width}
    selection: new Set(),
    drag: null,
  };

  function newId() { return state.nextId++; }
  function snap(v) { return Math.round(v / gridSize) * gridSize; }
  function pt(evt) {
    const p = svg.createSVGPoint();
    p.x = evt.clientX; p.y = evt.clientY;
    const m = svg.getScreenCTM().inverse();
    const { x, y } = p.matrixTransform(m);
    return { x, y };
  }

  // Shape helpers
  function shapeCenter(s) { return { x: s.x + s.w/2, y: s.y + s.h/2 }; }
  function anchorPoint(s, toward){
    const c = shapeCenter(s); const dx = toward.x - c.x; const dy = toward.y - c.y;
    if (dx === 0 && dy === 0) return c;
    if (s.type === 'rect' || s.type === 'symbol'){
      const rx = s.w/2, ry = s.h/2;
      const tx = dx === 0 ? Infinity : rx / Math.abs(dx);
      const ty = dy === 0 ? Infinity : ry / Math.abs(dy);
      const t = Math.min(tx, ty);
      return { x: c.x + dx * t, y: c.y + dy * t };
    } else if (s.type === 'ellipse'){
      const a = s.w/2, b = s.h/2;
      const denom = (dx*dx)/(a*a) + (dy*dy)/(b*b);
      if (denom <= 0) return c;
      const t = 1/Math.sqrt(denom);
      return { x: c.x + dx*t, y: c.y + dy*t };
    }
    return c;
  }

  // DOM rendering
  function render(){
    content.innerHTML = '';
    handlesLayer.innerHTML = '';
    // Shapes
    for (const s of state.shapes){
      const g = el('g', { class: `node ${state.selection.has(`s:${s.id}`)?'selected':''}`, 'data-id': `s:${s.id}` });
      if (s.type === 'rect'){
        g.appendChild(el('rect', { class:'shape-body', x:s.x, y:s.y, width:s.w, height:s.h, fill:s.fill, stroke:s.outline, 'stroke-width':s.width }));
      } else if (s.type === 'ellipse'){
        g.appendChild(el('ellipse', { class:'shape-body', cx:s.x+s.w/2, cy:s.y+s.h/2, rx:s.w/2, ry:s.h/2, fill:s.fill, stroke:s.outline, 'stroke-width':s.width }));
      } else if (s.type === 'symbol'){
        for (const seg of symbolGeometry(s)) g.appendChild(seg);
      }
      if (s.text){
        const c = shapeCenter(s);
        g.appendChild(el('text', { x:c.x, y:c.y, 'text-anchor':'middle', 'dominant-baseline':'middle', class:'shape-text' }, s.text));
      }
      g.classList.add('shape');
      content.appendChild(g);
    }
    // Connectors
    for (const c of state.connectors){
      const s1 = state.shapes.find(s=>s.id===c.src);
      const s2 = state.shapes.find(s=>s.id===c.dst);
      if (!s1 || !s2) continue;
      const p1 = anchorPoint(s1, shapeCenter(s2));
      const p2 = anchorPoint(s2, shapeCenter(s1));
      const ln = el('line', { x1:p1.x, y1:p1.y, x2:p2.x, y2:p2.y, stroke:c.stroke, 'stroke-width':c.width, class:`connector ${state.selection.has(`c:${c.id}`)?'selected':''}`, 'data-id':`c:${c.id}`, 'marker-end':'url(#arrow)' });
      content.appendChild(ln);
    }
    ensureArrowMarker();

    // Handles
    const sids = selectedShapeIds();
    if (sids.length === 1){ drawHandles(state.shapes.find(s=>s.id===sids[0])); }
  }

  function ensureArrowMarker(){
    if (!svg.querySelector('#arrow')){
      const defs = svg.querySelector('defs') || svg.insertBefore(el('defs'), svg.firstChild);
      const marker = el('marker', { id:'arrow', markerWidth:10, markerHeight:10, refX:10, refY:3, orient:'auto', markerUnits:'strokeWidth' });
      marker.appendChild(el('path', { d:'M0,0 L10,3 L0,6 Z', fill: strokeInput.value }));
      defs.appendChild(marker);
    }
  }

  function drawHandles(s){
    const pts = [
      [s.x, s.y, 'nw'], [s.x + s.w/2, s.y, 'n'], [s.x + s.w, s.y, 'ne'],
      [s.x, s.y + s.h/2, 'w'], [s.x + s.w, s.y + s.h/2, 'e'],
      [s.x, s.y + s.h, 'sw'], [s.x + s.w/2, s.y + s.h, 's'], [s.x + s.w, s.y + s.h, 'se'],
    ];
    for (const [x,y,pos] of pts){
      const r = el('rect', { x:x-5, y:y-5, width:10, height:10, 'data-h':`${s.id}:${pos}` });
      handlesLayer.appendChild(r);
    }
  }

  function el(tag, attrs={}, text){
    const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k,v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text!=null) n.textContent = text;
    return n;
  }

  function symbolGeometry(s){
    const g = document.createDocumentFragment();
    const stroke = s.outline; const fill = s.fill; const sw = s.width;
    const cx = s.x + s.w/2, cy = s.y + s.h/2;
    const thick = Math.max(1, Math.floor(Math.min(s.w,s.h)*0.25));
    const thin = Math.max(1, Math.floor(Math.min(s.w,s.h)*0.08));
    const kind = s.symbol || 'pipe';
    const line = (x1,y1,x2,y2,w) => el('line', { class:'shape-body', x1,y1,x2,y2, stroke:stroke, 'stroke-width':w, 'stroke-linecap':'round', 'stroke-linejoin':'round' });
    if (kind==='pipe'){
      g.appendChild(line(s.x, cy, s.x+s.w, cy, thick));
    } else if (kind==='elbow'){
      g.appendChild(el('polyline', { class:'shape-body', points:`${s.x},${cy} ${s.x},${s.y} ${cx},${s.y}`, stroke:stroke, 'stroke-width':thick, fill:'none', 'stroke-linejoin':'round', 'stroke-linecap':'round' }));
    } else if (kind==='tee'){
      g.appendChild(line(cx, s.y+s.h, cx, s.y, thick));
      g.appendChild(line(s.x, cy, s.x+s.w, cy, thick));
    } else if (kind==='valve'){
      g.appendChild(line(s.x, s.y, s.x+s.w, s.y+s.h, thin));
      g.appendChild(line(s.x+s.w, s.y, s.x, s.y+s.h, thin));
    } else if (kind==='reducer'){
      const top = s.y + s.h*0.2, bottom = s.y + s.h*0.8, small = s.x + s.w*0.75;
      g.appendChild(el('polygon', { class:'shape-body', points:`${s.x},${top} ${s.x},${bottom} ${small},${cy}`, stroke:stroke, 'stroke-width':thin, fill:fill }));
    } else if (kind==='flange'){
      const lf = s.x + s.w*0.25, rf = s.x + s.w*0.75;
      g.appendChild(line(s.x, cy, s.x+s.w, cy, thin));
      g.appendChild(line(lf, s.y, lf, s.y+s.h, thin));
      g.appendChild(line(rf, s.y, rf, s.y+s.h, thin));
    } else if (kind==='instrument'){
      const r = Math.min(s.w,s.h)/2;
      g.appendChild(el('ellipse', { class:'shape-body', cx, cy, rx:r, ry:r, stroke:stroke, 'stroke-width':thin, fill:fill }));
    } else {
      g.appendChild(line(s.x, cy, s.x+s.w, cy, thick));
    }
    return g.childNodes;
  }

  // Selection utilities
  function selectedShapeIds(){
    return Array.from(state.selection).filter(t=>t.startsWith('s:')).map(t=>parseInt(t.slice(2),10));
  }

  // Event handling
  svg.addEventListener('mousedown', onDown);
  svg.addEventListener('mousemove', onMove);
  svg.addEventListener('mouseup', onUp);

  function onDown(e){
    const {x,y} = pt(e);
    const target = e.target;
    const add = e.shiftKey;
    // Handle grips
    if (target instanceof SVGRectElement && target.hasAttribute('data-h')){
      const [sid, pos] = target.getAttribute('data-h').split(':');
      const s = state.shapes.find(s=>s.id===parseInt(sid,10));
      if (!s) return;
      state.drag = { kind:'resize', sid:s.id, pos, start:{x,y}, orig:{x:s.x,y:s.y,w:s.w,h:s.h} };
      return;
    }
    if (state.tool === 'select'){
      const tag = pickTag(target);
      if (tag){
        if (!add) state.selection.clear();
        state.selection.add(tag);
        if (tag.startsWith('s:')){
          state.drag = { kind:'move', sids: selectedShapeIds(), start:{x,y} };
        }
        render();
        return;
      }
      // marquee
      state.drag = { kind:'marquee', start:{x,y} };
      updateMarquee(x,y,x,y);
      return;
    }
    if (state.tool === 'rect' || state.tool === 'ellipse' || state.tool==='symbol'){
      const s = { id:newId(), type: state.tool==='symbol'?'symbol':state.tool, symbol: symbolKindSelect.value,
        x:snap(x), y:snap(y), w:1, h:1, fill: fillInput.value, outline: strokeInput.value, width: parseInt(widthInput.value,10)||2, text:'' };
      state.shapes.push(s);
      state.selection = new Set([`s:${s.id}`]);
      state.drag = { kind:'create', sid:s.id, start:{x,y} };
      render();
      return;
    }
    if (state.tool === 'connector'){
      const tag = pickTag(target);
      if (tag && tag.startsWith('s:')){
        const sid = parseInt(tag.slice(2),10);
        state.drag = { kind:'connect', src:sid, preview: el('line', { x1:x, y1:y, x2:x, y2:y, stroke:'#2d7ff9', 'stroke-dasharray':'4 2'}) };
        content.appendChild(state.drag.preview);
      }
      return;
    }
    if (state.tool === 'text'){
      const tag = pickTag(target);
      if (tag && tag.startsWith('s:')){
        const sid = parseInt(tag.slice(2),10);
        const s = state.shapes.find(s=>s.id===sid);
        const t = prompt('図形のテキスト', s.text||'');
        if (t!=null){ s.text = t; render(); }
      }
      return;
    }
  }

  function onMove(e){
    if (!state.drag) return;
    const {x,y} = pt(e);
    const k = state.drag.kind;
    if (k==='create'){
      const s = state.shapes.find(s=>s.id===state.drag.sid);
      const x0 = state.drag.start.x, y0 = state.drag.start.y;
      const x1 = snap(x), y1 = snap(y);
      s.x = Math.min(x0, x1); s.y = Math.min(y0, y1);
      s.w = Math.max(1, Math.abs(x1 - x0)); s.h = Math.max(1, Math.abs(y1 - y0));
      render();
    } else if (k==='move'){
      const dx = x - state.drag.start.x; const dy = y - state.drag.start.y;
      for (const sid of state.drag.sids){
        const s = state.shapes.find(s=>s.id===sid); if (!s) continue;
        s.x = snap(s.x + dx); s.y = snap(s.y + dy);
      }
      state.drag.start = {x,y};
      render();
    } else if (k==='resize'){
      const s = state.shapes.find(s=>s.id===state.drag.sid); if (!s) return;
      const {x:ox,y:oy,w:ow,h:oh} = state.drag.orig;
      let nx=ox, ny=oy, nw=ow, nh=oh; const x1=snap(x), y1=snap(y);
      const pos = state.drag.pos;
      if (pos.includes('w')){ nx = Math.min(x1, ox+ow); nw = Math.abs(ox+ow - x1); }
      if (pos.includes('e')){ nx = Math.min(ox, x1); nw = Math.abs(x1 - ox); }
      if (pos.includes('n')){ ny = Math.min(y1, oy+oh); nh = Math.abs(oy+oh - y1); }
      if (pos.includes('s')){ ny = Math.min(oy, y1); nh = Math.abs(y1 - oy); }
      s.x = nx; s.y = ny; s.w = Math.max(1,nw); s.h = Math.max(1,nh);
      render();
    } else if (k==='connect'){
      const s = state.shapes.find(s=>s.id===state.drag.src);
      if (!s) return;
      const ap = anchorPoint(s, {x,y});
      setAttrs(state.drag.preview, { x1: ap.x, y1: ap.y, x2: x, y2: y });
    } else if (k==='marquee'){
      updateMarquee(state.drag.start.x, state.drag.start.y, x, y);
    }
  }

  function onUp(e){
    if (!state.drag) return;
    const {x,y} = pt(e);
    const k = state.drag.kind;
    if (k==='connect'){
      const tag = pickTag(e.target);
      if (tag && tag.startsWith('s:')){
        const dst = parseInt(tag.slice(2),10);
        if (dst !== state.drag.src){
          state.connectors.push({ id:newId(), src: state.drag.src, dst, arrow:'last', stroke: strokeInput.value, width: parseInt(widthInput.value,10)||2 });
        }
      }
      state.drag.preview.remove();
      render();
    } else if (k==='marquee'){
      applyMarquee(state.drag.start.x, state.drag.start.y, x, y, e.shiftKey);
      hideMarquee();
      render();
    }
    state.drag = null;
  }

  function pickTag(target){
    if (!(target instanceof Element)) return null;
    const g = target.closest('[data-id]');
    return g ? g.getAttribute('data-id') : null;
  }

  function updateMarquee(x0,y0,x1,y1){
    const left = Math.min(x0,x1), top = Math.min(y0,y1), w = Math.abs(x1-x0), h = Math.abs(y1-y0);
    setAttrs(marqueeRect, { x:left, y:top, width:w, height:h, visibility:'visible' });
  }
  function hideMarquee(){ marqueeRect.setAttribute('visibility', 'hidden'); }

  function applyMarquee(x0,y0,x1,y1, add){
    if (!add) state.selection.clear();
    const left = Math.min(x0,x1), right = Math.max(x0,x1), top = Math.min(y0,y1), bottom = Math.max(y0,y1);
    for (const s of state.shapes){
      if (left <= s.x && right >= s.x+s.w && top <= s.y && bottom >= s.y+s.h){
        state.selection.add(`s:${s.id}`);
      }
    }
    for (const c of state.connectors){
      const s1 = state.shapes.find(s=>s.id===c.src), s2 = state.shapes.find(s=>s.id===c.dst);
      if (!s1||!s2) continue;
      const p1 = anchorPoint(s1, shapeCenter(s2)), p2 = anchorPoint(s2, shapeCenter(s1));
      const lx = Math.min(p1.x,p2.x), rx = Math.max(p1.x,p2.x), ty = Math.min(p1.y,p2.y), by = Math.max(p1.y,p2.y);
      if (left<=lx && right>=rx && top<=ty && bottom>=by){ state.selection.add(`c:${c.id}`); }
    }
  }

  function setAttrs(node, attrs){ for (const [k,v] of Object.entries(attrs)) node.setAttribute(k, v); }

  // Toolbar handlers
  tools.forEach(btn => btn.addEventListener('click', () => {
    tools.forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    state.tool = btn.getAttribute('data-tool');
  }));
  symbolKindSelect.addEventListener('change', () => state.symbol = symbolKindSelect.value);
  fillInput.addEventListener('change', ()=>render());
  strokeInput.addEventListener('change', ()=>render());
  widthInput.addEventListener('change', ()=>render());

  newBtn.addEventListener('click', () => { state = { ...state, nextId:1, shapes:[], connectors:[], selection:new Set(), drag:null }; render(); });
  openBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const txt = await f.text();
    try{
      const data = JSON.parse(txt);
      state.shapes = data.shapes || [];
      state.connectors = data.connectors || [];
      state.nextId = data.next_id || data.nextId || Math.max(1, ...state.shapes.map(s=>s.id), ...state.connectors.map(c=>c.id)) + 1;
      state.selection = new Set();
      render();
    }catch(err){ alert('JSONの読み込みに失敗しました: ' + err.message); }
  });
  saveBtn.addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(serialize(), null, 2)], { type:'application/json' });
    triggerDownload('diagram.drawpy.json', blob);
  });
  exportBtn.addEventListener('click', () => {
    // build clean SVG
    const clone = svg.cloneNode(true);
    clone.removeAttribute('tabindex');
    const m = clone.querySelector('#marquee'); if (m) m.remove();
    const h = clone.querySelector('#handles'); if (h) h.remove();
    const blob = new Blob([`<?xml version="1.0" encoding="UTF-8"?>\n` + clone.outerHTML], { type:'image/svg+xml' });
    triggerDownload('diagram.svg', blob);
  });

  function serialize(){
    return {
      shapes: state.shapes.map(({item_ids,canvas_id,text_id, ...s})=>s),
      connectors: state.connectors.map(({canvas_id, ...c})=>c),
      next_id: state.nextId,
      settings: { default_fill: fillInput.value, default_outline: strokeInput.value, default_width: parseInt(widthInput.value,10)||2 }
    };
  }

  function triggerDownload(name, blob){
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(()=>URL.revokeObjectURL(a.href), 1000);
  }

  // Initial render
  render();
})();

