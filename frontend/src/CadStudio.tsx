import { useCallback, useEffect, useRef, useState } from 'react';
import { FileText, FolderOpen, Maximize2, LoaderCircle } from 'lucide-react';
import DOMPurify from 'dompurify';
import { api, getToken, pickPath, Row, upload } from './api';

export function CadStudio({onError,toast}:{onError:(message:string)=>void,toast?:(message:string)=>void}) {
  const frame=useRef<HTMLIFrameElement>(null), input=useRef<HTMLInputElement>(null), pending=useRef<Row|null>(null);
  const ready=useRef(false);
  const [drawing,setDrawing]=useState<Row|null>(null),[details,setDetails]=useState<Row|null>(null);
  const [busy,setBusy]=useState(false),[progress,setProgress]=useState(0),[svg,setSvg]=useState(''),[mode,setMode]=useState<'cad'|'svg'>('cad');
  const [viewer,setViewer]=useState<boolean|null>(null);
  const loading=useRef(false), viewerRef=useRef<boolean|null>(null);
  useEffect(()=>{viewerRef.current=viewer},[viewer]);
  useEffect(()=>{let valid=true;api('/local/info').then(value=>{if(!valid)return;const available=value.optional?.cad_viewer?.available===true;setViewer(available);if(!available)setMode('svg')}).catch(()=>{if(valid){setViewer(false);setMode('svg')}});return()=>{valid=false}},[]);
  const changeBusy=useCallback((value:boolean)=>{loading.current=value;setBusy(value)},[]);
  const openFrame=useCallback(async(file:Row)=>{
    if(!viewerRef.current)throw new Error('未安装可选的 DWG/DXF 完整查看器；DXF 可使用快速预览');
    const token=await getToken();
    const url=file.cad_url||`/api/cad/files/${encodeURIComponent(file.id)}/${encodeURIComponent(file.name)}`;
    frame.current?.contentWindow?.postMessage({type:'cad:open',url,name:file.name,token},location.origin);
    pending.current=null;
  },[]);
  useEffect(()=>{
    const receive=(event:MessageEvent)=>{
      if(event.origin!==location.origin||event.source!==frame.current?.contentWindow)return;
      const data=event.data;if(!data||typeof data.type!=='string')return;
      if(data.type==='cad:ready'){ready.current=true;if(pending.current)openFrame(pending.current).catch(error=>{changeBusy(false);onError(String(error))})}
      if(data.type==='cad:progress'){setProgress(Math.max(0,Math.min(100,Number(data.percentage)||0)))}
      if(data.type==='cad:loaded'){setDetails(data);changeBusy(false);setProgress(100);toast?.(`已打开 ${data.name||'图纸'}`)}
      if(data.type==='cad:stats'){setDetails(data)}
      if(data.type==='cad:error'){changeBusy(false);onError(data.message||'图纸解析失败')}
    };
    window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);
  },[onError,toast,openFrame,changeBusy]);
  async function accept(file:Row){
    if(file.name.toLowerCase().endsWith('.dwg')&&!viewerRef.current){changeBusy(false);onError('公开源码未包含可选的 DWG 查看器。请改用 DXF 快速预览，或在本机安装兼容查看器组件。');return}
    setDrawing(file);setDetails(null);setSvg('');setMode(viewerRef.current?'cad':'svg');changeBusy(true);setProgress(0);
    if(!viewerRef.current){
      try{const result=await api('/drawing/preview',{path:file.path});setSvg(DOMPurify.sanitize(result.svg,{USE_PROFILES:{svg:true,svgFilters:true}}))}
      catch(error){onError((error as Error).message)}
      finally{changeBusy(false)}
      return
    }
    if(ready.current)await openFrame(file);else pending.current=file;
  }
  async function load(file?:File){if(!file||loading.current)return;if(!/\.(dwg|dxf)$/i.test(file.name)){onError('请选择 DWG 或 DXF 图纸');return}changeBusy(true);try{await accept(await upload(file))}catch(error){changeBusy(false);onError((error as Error).message)}}
  async function nativeFile(){if(loading.current)return;try{const path=await pickPath();if(path&&!loading.current){changeBusy(true);await accept(await api('/cad/import',{path}))}}catch(error){changeBusy(false);onError((error as Error).message)}}
  async function quickSvg(){if(!drawing||loading.current)return;changeBusy(true);try{const result=await api('/drawing/preview',{path:drawing.path});setSvg(DOMPurify.sanitize(result.svg,{USE_PROFILES:{svg:true,svgFilters:true}}));setMode('svg')}catch(error){onError((error as Error).message)}finally{changeBusy(false)}}
  return <div className="drawing-studio" style={{height:'100%',display:'flex',flexDirection:'column',minHeight:0}}>
    <div className="studio-heading"><div><h1>图纸预览</h1><p>DXF 可本机快速预览；DWG 完整图层、测量与浏览需要可选的本地查看器组件。</p></div><div className="button-row">
      {drawing&&mode==='svg'&&viewer&&<button className="secondary-button" disabled={busy} onClick={()=>setMode('cad')}>完整图纸查看器</button>}
      {drawing?.name?.toLowerCase().endsWith('.dxf')&&<button className="secondary-button" disabled={busy} onClick={quickSvg}>{mode==='svg'?'重新生成预览':'DXF 快速预览'}</button>}
      <button className="secondary-button" disabled={!drawing||mode==='svg'||!viewer} onClick={()=>frame.current?.contentWindow?.postMessage({type:'cad:fit'},location.origin)}><Maximize2 size={15}/>适合窗口</button>
      <button className="secondary-button" disabled={busy} onClick={nativeFile}><FolderOpen size={15}/>本机路径</button>
      <button className="primary-button" disabled={busy} onClick={()=>input.current?.click()}><FileText size={16}/>{busy?'正在加载…':'打开图纸'}</button>
    </div></div>
    <div style={{flex:1,minHeight:0,position:'relative',background:'#171d25'}} onDragOver={event=>event.preventDefault()} onDrop={event=>{event.preventDefault();event.stopPropagation();load(event.dataTransfer.files[0])}}>
      {viewer!==false&&<iframe ref={frame} src="/cad-viewer/index.html" title="DWG 与 DXF 图纸查看器" sandbox="allow-scripts allow-same-origin allow-downloads" style={{position:'absolute',inset:0,width:'100%',height:'100%',border:0,visibility:mode==='cad'?'visible':'hidden'}} onError={()=>setViewer(false)}/>}
      {viewer===false&&!drawing&&<div className="empty-state" style={{position:'absolute',inset:0}}><div className="empty-icon"><FileText size={30}/></div><h2>打开一张 DXF 图纸</h2><p>完整 DWG 查看器是可选组件，未包含在公开源码中；DXF 快速预览仍可直接使用。</p><div className="button-row"><button className="secondary-button" onClick={()=>input.current?.click()}>选择图纸</button></div></div>}
      {mode==='svg'&&<div style={{width:'100%',height:'100%',overflow:'auto',background:'#fff'}} dangerouslySetInnerHTML={{__html:svg}}/>}
      {busy&&<div role="status" style={{position:'absolute',inset:0,display:'grid',placeItems:'center',background:'#17212ad9',color:'#e8eef5',pointerEvents:'none'}}><div style={{display:'flex',gap:10,alignItems:'center'}}><LoaderCircle size={20}/>正在解析图纸 {Math.round(progress)}%</div></div>}
    </div>
    <div className="canvas-status"><strong>{drawing?.name||'未打开图纸'}</strong><span>{details?`${details.entities} 个实体 · ${details.contours} 个可见轮廓 · ${details.layers?.length||0} 个图层`:''}</span><div className="titlebar-space"/><span>滚轮缩放 · 中键平移 · 拖入 DXF 打开</span></div>
    <input ref={input} type="file" accept=".dxf,.dwg" hidden onChange={event=>{load(event.target.files?.[0]);event.target.value=''}}/>
  </div>;
}
