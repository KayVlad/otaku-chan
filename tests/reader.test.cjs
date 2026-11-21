const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../src/templates/shell.html'), 'utf8');
const source = html.slice(html.indexOf('  function reader('), html.indexOf('  (() => {'));
function reader(count=600, stubScroll=true) {
  const context = vm.createContext({setTimeout, requestAnimationFrame: f=>f(), localStorage:{getItem:()=>null},
    sessionStorage:{getItem:()=>null,removeItem(){}},window:{}, Image:class {},fetch:()=>Promise.resolve({})});
  vm.runInContext(source,context);
  const r=context.reader(Array.from({length:count},(_,i)=>String(i)),null,null,1,'Test','Test','Vol',0,1);
  r.$nextTick=f=>{if(f)f();return Promise.resolve();}; if(stubScroll)r.scrollStrip=()=>{};r.saveProgress=()=>{};r.prefetchNext=()=>{};
  return r;
}
function event(id,x,y){return {pointerId:id,pointerType:'touch',button:0,clientX:x,clientY:y,currentTarget:{setPointerCapture(){},getBoundingClientRect:()=>({left:0,top:0,width:400,height:800})}};}
test('pinch enters zoom, keeps midpoint anchored, then continues one-finger pan',()=>{
 const r=reader();r.startDrag(event(1,100,400));r.startDrag(event(2,300,400));
 r.moveDrag(event(1,0,400));r.moveDrag(event(2,400,400));
 assert.equal(r.viewMode,'zoom');assert.equal(r.zoom,2);assert.equal(r.panX,0);assert.equal(r.panY,0);
 r.endDrag(event(2,400,400));const before=r.panX;r.moveDrag(event(1,30,410));
 assert.equal(r.panX,before+30);assert.equal(r.panY,10);
 r.endDrag(event(1,30,410));assert.equal(r.dragging,false);assert.equal(r._gesture,null);
});
test('pinch clamps zoom and canceled pointers do not keep dragging',()=>{
 const r=reader();r.startDrag(event(1,190,400));r.startDrag(event(2,210,400));r.moveDrag(event(2,900,400));
 assert.equal(r.zoom,8);r.endDrag(event(1,190,400));r.endDrag(event(2,900,400));
 const x=r.panX;r.moveDrag(event(2,999,400));assert.equal(r.panX,x);
});
test('single finger pans zoom mode; drag does not turn pages',()=>{
 const r=reader();r.setMode('zoom');r.startDrag(event(1,200,400));r.moveDrag(event(1,230,450));
 assert.equal(r.panX,30);assert.equal(r.panY,50);let turns=0;r.next=()=>turns++;
 r.endDrag(event(1,230,450));r.onStageClick();assert.equal(turns,0);
 r.setMode('fit');r.startDrag(event(1,200,400));r.endDrag(event(1,200,400));r.onStageClick();assert.equal(turns,1);
});
test('rapid slider input reaches final requested page during animation',async()=>{
 const r=reader();await r.goTo(150);await r.goTo(300);await r.goTo(599);
 await new Promise(resolve=>setTimeout(resolve,450));assert.equal(r.currentIdx,599);assert.equal(r.transitioning,false);
});

test('ordinary touch taps retain navigation-zone targeting; slow swipes suppress clicks',()=>{
 const r=reader();let captures=0;const e=event(1,200,400);e.currentTarget.setPointerCapture=()=>captures++;
 r.startDrag(e);assert.equal(captures,0);
 for(let x=201;x<=210;x++)r.moveDrag(event(1,x,400));
 r.endDrag(event(1,210,400));let turns=0;r.next=()=>turns++;r.onStageClick();assert.equal(turns,0);
});

test('thumbnail navigation scrolls only the strip on both mobile and desktop',()=>{
 const r=reader(600,false);
 const calls=[];
 const thumb={getBoundingClientRect:()=>({left:500,right:550,top:10,bottom:90})};
 const strip={clientWidth:320,clientHeight:88,clientLeft:0,clientTop:1,
   getBoundingClientRect:()=>({left:0,top:0}),querySelectorAll:()=>[thumb],
   scrollBy:options=>calls.push(options)};
 r.$refs={strip};r.scrollStrip(0);
 assert.equal(calls.length,1);assert.equal(calls[0].left,230);assert.equal(calls[0].top,1);
 calls.length=0;
 strip.clientWidth=84;strip.clientHeight=500;
 thumb.getBoundingClientRect=()=>({left:4,right:80,top:600,bottom:710});
 r.scrollStrip(0);
 assert.equal(calls.length,1);assert.equal(calls[0].left,0);assert.equal(calls[0].top,209);
});

test('visible, hidden and missing thumbnails do not move the layout',()=>{
 const r=reader(600,false);let scrolls=0;
 const strip={clientWidth:320,clientHeight:88,clientLeft:0,clientTop:0,
   getBoundingClientRect:()=>({left:0,top:0}),
   querySelectorAll:()=>[{getBoundingClientRect:()=>({left:4,right:54,top:4,bottom:84})}],
   scrollBy:()=>scrolls++};
 r.$refs={strip};r.scrollStrip(0);r.scrollStrip(599);
 strip.clientWidth=0;r.scrollStrip(0);
 assert.equal(scrolls,0);
});
