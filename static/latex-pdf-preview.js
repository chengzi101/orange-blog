import {getDocument, GlobalWorkerOptions} from './vendor/pdfjs/build/pdf.js';
import {EventBus, PDFViewer, PDFLinkService} from './vendor/pdfjs/web/pdf_viewer.js';
GlobalWorkerOptions.workerSrc='/static/vendor/pdfjs/build/pdf.worker.js';

export function createPreview(container) {
 const pages=document.createElement('div');pages.className='pdfViewer';container.append(pages);
 const bus=new EventBus();
 const links=new PDFLinkService({eventBus:bus,externalLinkTarget:2,externalLinkRel:'noopener noreferrer'});
 const viewer=new PDFViewer({container,viewer:pages,eventBus:bus,linkService:links,textLayerMode:1,annotationMode:2,annotationEditorMode:-1});
 links.setViewer(viewer);
 let task=null,doc=null,url='',generation=0,location=null,restoring=false,restore=null,interacted=false;
 bus.on('updateviewarea',event=>{if(!restoring)location={...event.location,scaleValue:viewer.currentScaleValue};});
 for(const type of ['wheel','touchstart','pointerdown','keydown'])container.addEventListener(type,()=>{interacted=true;},{passive:true});
 function applyPosition(){
  if(!restore){viewer.currentScaleValue='page-width';return;}
  viewer.currentScaleValue=restore.scaleValue||'page-width';
  viewer.scrollPageIntoView({pageNumber:Math.min(Math.max(1,restore.pageNumber),doc.numPages),destArray:[null,{name:'XYZ'},restore.left,restore.top,null],allowNegativeOffset:true});
 }
 bus.on('pagesinit',()=>{applyPosition();restoring=false;viewer.update();});
 bus.on('pagesloaded',()=>{if(!interacted&&restore){restoring=true;applyPosition();restoring=false;viewer.update();}restore=null;});
 new ResizeObserver(()=>{if(doc&&viewer.currentScaleValue==='page-width')viewer.currentScaleValue='page-width';}).observe(container);
 return {
  async load(nextUrl){
   if(nextUrl===url&&doc)return;
   const serial=++generation;
   if(task){const previousTask=task;task=null;await previousTask.destroy();if(serial!==generation)return;}
   const pending=getDocument({url:nextUrl,cMapUrl:'/static/vendor/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/static/vendor/pdfjs/standard_fonts/',wasmUrl:'/static/vendor/pdfjs/wasm/',isEvalSupported:false});task=pending;
   let next;
   try{next=await pending.promise;}catch(error){if(serial!==generation)return;task=null;throw error;}
   if(serial!==generation){await pending.destroy();return;}
   task=null;const previous=doc;
   // Capture at replacement time, so scrolling while the new PDF downloads is respected.
   viewer.update();restore=location?{...location}:null;interacted=false;restoring=true;doc=next;url=nextUrl;
   viewer.setDocument(next);links.setDocument(next);
   container.dataset.pdfUrl=nextUrl;container.dataset.pdfPages=String(next.numPages);
   if(previous)previous.loadingTask.destroy().catch(()=>{});
  }
 };
}
