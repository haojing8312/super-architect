import {useEffect, useRef, useState} from 'react';
import {Box, Download, FileImage, Loader2, PencilRuler, Play, RefreshCw, Save, Upload, X} from 'lucide-react';
import {api, fileUrl, upload, type Row} from './api';
import './InteriorStudio.css';

type Props={onGenerate:(task:Row)=>Promise<Row>,onError:(message:string)=>void,runtimeBusy?:boolean};
type TraceRoom={name:string,bounds_px:number[]};

export default function InteriorStudio({onGenerate,onError,runtimeBusy=false}:Props){
 const [tasks,setTasks]=useState<Row[]>([]),[task,setTask]=useState<Row|null>(null),[styles,setStyles]=useState<Row[]>([]),[capabilities,setCapabilities]=useState<Row>({});
 const [source,setSource]=useState<Row|null>(null),[name,setName]=useState('室内三维方案'),[page,setPage]=useState(1),[height,setHeight]=useState(2.8),[thickness,setThickness]=useState(.15),[selectedStyles,setSelectedStyles]=useState(['scandi']);
 const [scalePolicy,setScalePolicy]=useState('estimate'),[points,setPoints]=useState<number[][]>([]),[length,setLength]=useState(3.6),[calibrating,setCalibrating]=useState(false);
 const [references,setReferences]=useState<Row[]>([]);
 const [sourcePreview,setSourcePreview]=useState(''),[frameUrl,setFrameUrl]=useState(''),[busy,setBusy]=useState(''),[tab,setTab]=useState<'plan'|'editor'|'data'>('plan');
 const [pictureSize,setPictureSize]=useState<Row|null>(null);
 const [traceMode,setTraceMode]=useState(false),[corner,setCorner]=useState<number[]|null>(null),[rooms,setRooms]=useState<TraceRoom[]>([]),[estimateWidth,setEstimateWidth]=useState(6),[sceneText,setSceneText]=useState('');
 const sourceInput=useRef<HTMLInputElement>(null),sceneInput=useRef<HTMLInputElement>(null),referenceInput=useRef<HTMLInputElement>(null),frame=useRef<HTMLIFrameElement>(null),image=useRef<HTMLImageElement>(null);
 const active=useRef(''),epoch=useRef(0),channel=useRef(crypto.randomUUID()),mounted=useRef(true),pendingSnapshot=useRef<((value:Row)=>void)|null>(null);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;epoch.current++}},[]);
 async function refreshList(){const result=await api('/interior/tasks');if(mounted.current)setTasks(result.items||[])}
 useEffect(()=>{Promise.all([api('/interior/styles'),api('/interior/capabilities'),api('/interior/tasks')]).then(([s,c,t])=>{if(mounted.current){setStyles(s.items||[]);setCapabilities(c);setTasks(t.items||[])}}).catch(error=>onError(String(error)))},[]);
 async function displayTask(value:Row){
  setLength(value.configuration?.calibration?.referenceLengthM??3.6);
  const ticket=++epoch.current;active.current=value.id;channel.current=crypto.randomUUID();setTask(value);setSource(value.source);setName(value.name);setSelectedStyles(value.styles);setRooms([]);setCorner(null);setTraceMode(false);setSceneText('');setPictureSize(null);setPoints(value.configuration?.calibration?.pointsPx||[]);setScalePolicy(value.configuration?.scalePolicy||'estimate');setCalibrating(false);setHeight(value.configuration?.heightM||2.8);setThickness(value.configuration?.wallThicknessM||.15);setPage(value.source.page);
  const picture=await fileUrl(value.source_url),preview=value.preview_url?await fileUrl(value.preview_url+'?channel='+channel.current):'';
  const refs=await Promise.all((value.reference_files||[]).map(async(x:Row)=>({...x,preview:await fileUrl(x.url)})));
  if(!mounted.current||ticket!==epoch.current)return;setSourcePreview(picture);setFrameUrl(preview);setReferences(refs);if(preview)setTab('editor');
 }
 async function loadTask(id:string){await displayTask(await api('/interior/tasks/'+id))}
 async function updateTask(value:Row){
  if(!mounted.current||active.current!==value.id)return;setTask(value);
  if(value.preview_url){const url=await fileUrl(value.preview_url+'?channel='+channel.current+'&revision='+value.revision);if(active.current===value.id&&mounted.current){setFrameUrl(url);setTab('editor')}}
  await refreshList();
 }
 useEffect(()=>{if(!runtimeBusy||!task)return;let valid=true;const timer=setInterval(()=>{api('/interior/tasks/'+task.id).then(value=>{if(valid&&active.current===value.id&&value.updated_at!==task.updated_at)updateTask(value)}).catch(()=>{})},2500);return()=>{valid=false;clearInterval(timer)}},[runtimeBusy,task?.id,task?.updated_at]);
 useEffect(()=>{
  function receive(event:MessageEvent){if(event.source!==frame.current?.contentWindow||event.origin!=='null'||event.data?.channel!==channel.current||event.data?.type!=='interior-scene')return;pendingSnapshot.current?.(event.data.scene);pendingSnapshot.current=null}
  window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);
 },[]);
 async function action(label:string,work:()=>Promise<void>){if(busy)return;setBusy(label);try{await work()}catch(error){onError(String(error))}finally{if(mounted.current)setBusy('')}}
 async function chooseSource(file:File){
  if(!/\.(png|jpe?g|webp|pdf)$/i.test(file.name)||file.size>15*1024*1024){onError('请选择不超过 15 MB 的 PNG/JPEG/WebP/PDF 主底图');return}
  await action('上传底图',async()=>{const value=await upload(file);active.current='';epoch.current++;setSource(value);setTask(null);setFrameUrl('');setPoints([]);setRooms([]);setPage(1);setTab('plan');setSourcePreview(/\.pdf$/i.test(file.name)?'':await fileUrl(value.url))});
 }
 async function createTask(){
  if(!source){onError('请先上传本次主底图');return}
  if(selectedStyles.includes('custom')&&!references.length){onError('自定义风格需要至少一张本次参考图片');return}
  await action('创建方案',async()=>{const value=await api('/interior/tasks',{file_id:source.id,name,page,styles:selectedStyles,height_m:height,wall_thickness_m:thickness,scale_policy:scalePolicy,reference_length_m:scalePolicy==='calibrated'?length:undefined,points_px:scalePolicy==='calibrated'?points:undefined,reference_ids:selectedStyles.includes('custom')?references.map(x=>x.id):[]});await displayTask(value);await refreshList()});
 }
 async function addReferences(files:FileList|null){
  if(!files)return;const selected=Array.from(files);
  if(references.length+selected.length>8||selected.some(x=>! /\.(png|jpe?g|webp)$/i.test(x.name)||x.size>15*1024*1024)){onError('风格参考最多 8 张，每张为不超过 15 MB 的 PNG/JPEG/WebP 图片');return}
  await action('上传风格参考',async()=>{const refs=await Promise.all(selected.map(async file=>{const saved=await upload(file);return {...saved,preview:await fileUrl(saved.url)}}));if(mounted.current)setReferences(previous=>[...previous,...refs])});
 }
 function toggleStyle(id:string){setSelectedStyles(previous=>id==='custom'?(previous.includes('custom')?[]:['custom']):previous.includes(id)?previous.filter(x=>x!==id):[...previous.filter(x=>x!=='custom'),id])}
 function clickPlan(event:React.MouseEvent){
  const element=image.current;if(!element)return;const rect=element.getBoundingClientRect(),x=(event.clientX-rect.left)*element.naturalWidth/rect.width,y=(event.clientY-rect.top)*element.naturalHeight/rect.height;
  if(calibrating){setPoints(previous=>previous.length===2?[[x,y]]:[...previous,[x,y]]);return}
  if(!traceMode||!task)return;
  if(!corner){setCorner([x,y]);return}
  const bounds=[Math.min(x,corner[0]),Math.min(y,corner[1]),Math.abs(x-corner[0]),Math.abs(y-corner[1])];if(bounds[2]>=2&&bounds[3]>=2)setRooms(previous=>[...previous,{name:`房间 ${previous.length+1}`,bounds_px:bounds}]);setCorner(null);
 }
 async function collect(){if(task)await action('读取生成场景',async()=>{await updateTask(await api(`/interior/tasks/${task.id}/collect`))})}
 async function generateDesign(){
  if(!task)return;const taskId=task.id;
  await action('提交本机设计任务',async()=>{const result=await onGenerate(task);if(mounted.current&&active.current===taskId&&result.id===taskId)await updateTask(result)});
 }
 async function saveFromEditor(){
  if(!task||!frame.current?.contentWindow)return;
  await action('保存当前编辑',async()=>{const value=await new Promise<Row>((resolve,reject)=>{const timer=setTimeout(()=>{pendingSnapshot.current=null;reject(new Error('编辑器尚未就绪，请稍后保存'))},8000);pendingSnapshot.current=scene=>{clearTimeout(timer);resolve(scene)};frame.current!.contentWindow!.postMessage({type:'interior-snapshot-request',channel:channel.current},'*')});await updateTask(await api(`/interior/tasks/${task.id}/edited-scene`,value,'PUT'))});
 }
 async function importScene(file:File){
  if(!task){onError('请先选择当前底图任务');return}if(file.size>40*1024*1024){onError('场景文件不能超过 40 MB');return}
  await action('导入编辑项目',async()=>{const value=JSON.parse(await file.text());await updateTask(await api(`/interior/tasks/${task.id}/edited-scene`,value,'PUT'))});
 }
 async function download(kind:string){if(!task)return;const anchor=document.createElement('a');anchor.href=await fileUrl(`/api/interior/tasks/${task.id}/files/${kind}/file`);anchor.download='';anchor.click()}
 const disabled=!!busy||runtimeBusy;
 const pixels=task?.source||pictureSize;
 return <div className="interior-studio studio">
  <div className="interior-heading"><div><h2><Box size={23}/>空间设计</h2><p>上传本次户型底图，生成或描绘场景；在离线三维编辑器继续设计。</p></div><span className="interior-status">{busy?<><Loader2 size={14} className="spin"/>{busy}…</>:runtimeBusy?'本机设计任务正在执行':task?.status==='exported'?'已导出交付网页':task?.latest_html?'可编辑草案':'准备底图'}</span></div>
  {capabilities.ready===false&&<p className="interior-notice">本机三维组件尚未就绪：{capabilities.missing?.join('、')||'请配置 Node.js'}</p>}
  <div className="interior-layout"><aside className="interior-sidebar">
   <section><h3>本次主底图</h3><button className="secondary-button" disabled={disabled} onClick={()=>sourceInput.current?.click()}><Upload size={15}/>上传 PNG / JPEG / PDF</button><input ref={sourceInput} type="file" accept=".png,.jpg,.jpeg,.webp,.pdf" hidden onChange={event=>{if(event.target.files?.[0])chooseSource(event.target.files[0]);event.target.value=''}}/><p className="small muted">{source?.name||task?.source?.name||'选择完整、清晰的平面图，最多 15 MB'}</p>
    <label className="field"><span>方案名称</span><input value={name} onChange={event=>setName(event.target.value)} maxLength={120}/></label>
    {(source&&/\.pdf$/i.test(source.name))&&<label className="field"><span>PDF 页码</span><input type="number" min={1} value={page} onChange={event=>{setPage(Number(event.target.value));setPoints([])}}/><small>每次只识别此页；创建任务后可预览所选页。</small></label>}
    <div className="interior-two"><label className="field"><span>净高（米）</span><input type="number" min={1.8} max={8} step={.1} value={height} onChange={event=>setHeight(Number(event.target.value))}/></label><label className="field"><span>墙厚（米）</span><input type="number" min={.05} max={1} step={.01} value={thickness} onChange={event=>setThickness(Number(event.target.value))}/></label></div>
    <label className="field"><span>尺度依据</span><select value={scalePolicy} onChange={event=>{setScalePolicy(event.target.value);setCalibrating(event.target.value==='calibrated')}}><option value="estimate">按标注或常规构件估算</option><option value="calibrated">在底图选两点标定</option></select></label>
    {scalePolicy==='calibrated'&&<><label className="field"><span>两点真实长度（米）</span><input type="number" min={.05} max={1000} value={length} step={.1} onChange={event=>setLength(Number(event.target.value))}/></label><p className="small muted">点击底图选择两个端点：{points.length} / 2{points.length===2&&` · ${Math.hypot(points[0][0]-points[1][0],points[0][1]-points[1][1]).toFixed(1)} 原图像素`}</p><button className="text-button" onClick={()=>{setPoints([]);setCalibrating(true);setTab('plan')}}>重选标定点</button></>}
   </section>
   <section><h3>设计风格</h3><div className="interior-styles">{styles.map(style=><label className={selectedStyles.includes(style.id)?'chosen':''} key={style.id}><input type="checkbox" checked={selectedStyles.includes(style.id)} onChange={()=>toggleStyle(style.id)}/><span>{style.name}</span><i>{style.palette.slice(0,3).map((color:string)=><b key={color} style={{background:color}}/>)}</i></label>)}</div>{selectedStyles.includes('custom')&&<div className="interior-references"><p className="small muted">参考图只提供配色、材料和家具造型，主底图仍是户型的唯一来源。</p><button className="secondary-button" disabled={disabled||references.length>=8} onClick={()=>referenceInput.current?.click()}><Upload size={14}/>上传风格参考</button><input ref={referenceInput} type="file" multiple accept=".png,.jpg,.jpeg,.webp" hidden onChange={event=>{addReferences(event.target.files);event.target.value=''}}/>{references.map(reference=><div className="interior-reference" key={reference.id}><img src={reference.preview} alt={reference.name}/><span>{reference.name}</span><button className="icon-button" aria-label={`移除参考 ${reference.name}`} onClick={()=>setReferences(previous=>previous.filter(x=>x.id!==reference.id))}><X size={13}/></button></div>)}</div>}<button className="primary-button" disabled={disabled||!source||!selectedStyles.length||(selectedStyles.includes('custom')&&!references.length)||(scalePolicy==='calibrated'&&points.length!==2)} onClick={createTask}>创建本次方案</button></section>
   <section><h3>已保存方案</h3><div className="interior-task-list">{tasks.map(value=><button className={task?.id===value.id?'active':''} key={value.id} disabled={!!busy} onClick={()=>action('打开方案',()=>loadTask(value.id))}><strong>{value.name}</strong><span>{value.source.name} · r{value.revision}</span></button>)}{!tasks.length&&<p className="small muted">方案保存在当前账号工作区。</p>}</div></section>
  </aside><main className="interior-main"><div className="interior-toolbar"><div>{(['plan','editor','data'] as const).map(value=><button className={tab===value?'active':''} key={value} onClick={()=>{setTab(value);if(value==='data'&&task)api(`/interior/tasks/${task.id}/scene`).then(scene=>setSceneText(JSON.stringify(scene,null,2))).catch(()=>setSceneText(''))}}>{value==='plan'?'底图与描绘':value==='editor'?'三维编辑':'场景数据'}</button>)}</div>{task&&<button className="secondary-button" disabled={disabled} onClick={generateDesign}><Play size={14}/>生成设计</button>}</div>
   {task&&<div className="interior-actionbar"><button className="text-button" disabled={disabled} onClick={collect}><RefreshCw size={14}/>读取生成结果</button><button className="text-button" disabled={disabled} onClick={()=>sceneInput.current?.click()}>导入编辑项目</button><input ref={sceneInput} type="file" accept=".json" hidden onChange={event=>{if(event.target.files?.[0])importScene(event.target.files[0]);event.target.value=''}}/>{task.latest_html&&<><button className="text-button" disabled={disabled} onClick={saveFromEditor}><Save size={14}/>保存当前编辑</button><button className="text-button" onClick={()=>download('html')}><Download size={14}/>下载离线网页</button><button className="text-button" onClick={()=>download('scene')}>场景 JSON</button></>}</div>}
   {tab==='plan'&&<><div className="interior-plan">{sourcePreview?<div className="interior-plan-image"><img ref={image} src={sourcePreview} alt="本次户型主底图" onClick={clickPlan} onLoad={()=>{setCorner(null);if(image.current)setPictureSize({width:image.current.naturalWidth,height:image.current.naturalHeight})}} style={{cursor:calibrating||traceMode?'crosshair':'default'}}/>{pixels&&<svg viewBox={`0 0 ${pixels.width} ${pixels.height}`} preserveAspectRatio="none">{rooms.map((room,index)=><g key={index}><rect x={room.bounds_px[0]} y={room.bounds_px[1]} width={room.bounds_px[2]} height={room.bounds_px[3]}/><text x={room.bounds_px[0]+5} y={room.bounds_px[1]+18}>{room.name}</text></g>)}{points.map((point,index)=><circle key={index} cx={point[0]} cy={point[1]} r={Math.max(3,pixels.width/150)}/>)}{corner&&<circle cx={corner[0]} cy={corner[1]} r={5}/>}</svg>}</div>:<div className="interior-empty"><FileImage size={40} strokeWidth={1}/><p>{source&&/\.pdf$/i.test(source.name)?'创建任务后预览选定的 PDF 页，再描绘房间。':'先上传本次户型底图'}</p></div>}</div>{task&&<div className="interior-trace"><h3><PencilRuler size={16}/>手工描绘</h3><p>在原图点击矩形房间的两个对角；这会按你描绘的区域建立草案。门窗洞口需继续补齐，家具可在三维编辑器添加。</p><div className="button-row"><button className="secondary-button" disabled={disabled} onClick={()=>{setTraceMode(!traceMode);setCalibrating(false);setCorner(null)}}>{traceMode?'结束描绘':'开始描绘房间'}</button>{scalePolicy==='estimate'&&<label>首个房间估算宽度（米）<input type="number" min={.5} max={200} value={estimateWidth} onChange={event=>setEstimateWidth(Number(event.target.value))}/></label>}</div>{rooms.map((room,index)=><div className="interior-room-row" key={index}><input aria-label={`房间 ${index+1} 名称`} value={room.name} onChange={event=>setRooms(previous=>previous.map((x,i)=>i===index?{...x,name:event.target.value}:x))}/><small>{room.bounds_px.map(v=>Math.round(v)).join(' , ')} px</small><button className="icon-button" aria-label="移除房间" onClick={()=>setRooms(previous=>previous.filter((_,i)=>i!==index))}><X size={14}/></button></div>)}<button className="primary-button" disabled={disabled||!rooms.length} onClick={()=>action('创建描绘草案',async()=>{await updateTask(await api(`/interior/tasks/${task.id}/trace`,{rooms,estimated_width_m:estimateWidth}))})}>按描绘建立三维草案</button></div>}</>}
   {tab==='editor'&&(frameUrl?<iframe ref={frame} key={frameUrl} title="室内三维草案编辑器" className="interior-frame" src={frameUrl} sandbox="allow-scripts allow-downloads allow-forms"/>:<div className="interior-empty"><Box size={40} strokeWidth={1}/><p>完成本机设计或手工描绘后，在这里编辑三维场景。</p></div>)}
   {tab==='data'&&<div className="interior-data"><p>高级编辑：场景始终绑定本次底图、哈希和页码。导入旧户型会被拒绝。</p><textarea aria-label="室内场景 JSON" spellCheck={false} value={sceneText} onChange={event=>setSceneText(event.target.value)}/><button className="primary-button" disabled={disabled||!task||!sceneText.trim()} onClick={()=>action('保存场景数据',async()=>{const scene=JSON.parse(sceneText);await api(`/interior/tasks/${task!.id}/scene`,scene,'PUT');await updateTask(await api(`/interior/tasks/${task!.id}/assemble`,{final:false}))})}><Save size={14}/>校验并保存草案</button></div>}
   <div className="interior-footnote">{task&&<span>本次来源 {task.source.name} · 第 {task.source.page} 页 · {task.source.sha256.slice(0,12)} · r{task.revision}。 </span>}草案中的估算尺寸、程序化家具和未核对开口需复核。离线网页可编辑、保存项目和导出 GLB；不等同施工深化或单图转网格。</div>
  </main></div>
 </div>
}
