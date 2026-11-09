import {useEffect, useRef, useState} from 'react';
import {Box, Brush, CheckCircle2, Cpu, Image as ImageIcon, ImagePlus, Layers3, Loader2, Palette, Play, Trash2, Video, Wrench} from 'lucide-react';
import {api, fileUrl, upload, type Row} from './api';

export type CreativeKind = 'image'|'edit'|'video'|'model'|'render'|'design'|'tools';
export type CreativeRequest = {kind:CreativeKind,prompt:string,provider:string,model:string,attachments:Row[],options?:Row};
type Props = {kind:CreativeKind,settings:Row,onRun:(request:CreativeRequest)=>Promise<void>,onError:(message:string)=>void};
const labels:Record<CreativeKind,{title:string,description:string,action:string,icon:typeof ImageIcon,example:string}> = {
 image:{title:'AI 一键创作',description:'用本机 Agent 生成真正的图像，结果回到当前工作区。',action:'生成图片',icon:ImageIcon,example:'为一栋三层社区文化中心生成建筑概念图，木材与白色混凝土，午后自然光，保留清晰的入口与公共庭院。'},
 edit:{title:'AI 图像编辑',description:'上传原图，明确要改变的区域与必须保留的内容。',action:'开始编辑',icon:Brush,example:'保留原建筑体量与视角，将外墙改为浅色混凝土和木格栅，增加自然日光，保持入口与窗洞位置。'},
 video:{title:'AI 视频创作',description:'通过本机 WorkBuddy 的原生视频工具创建短片。账号与模型可用性以实际返回为准。',action:'生成视频',icon:Video,example:'建筑外观缓慢推进镜头，从入口走向庭院，真实自然光，流畅稳定，没有文字或标志。'},
 model:{title:'AI 图转模型',description:'结合参考与尺寸，用本机 Agent 和 Blender 重建可编辑几何。',action:'建立模型',icon:Box,example:'依据参考建立简洁的三层建筑体量模型，高度 12 米，宽 20 米，深 16 米，明确标记无法从图片确定的假设，保存 Blender 与 GLB 文件。'},
 render:{title:'AI 模型渲染',description:'通过已连接的 Blender 打开真实模型，布置材质、光线与相机并渲染。',action:'渲染场景',icon:Layers3,example:'为上传的建筑模型设置混凝土与玻璃材质，自然日光、三分之二外观视角，保存真实场景与渲染图。'},
 design:{title:'AI 设计智能体',description:'从设计意图与参考出发，用本机原生图像能力探索建筑方案。',action:'生成设计方案',icon:Palette,example:'为一个小型社区图书馆探索建筑方案，低层、亲切的街道界面，连接公共庭院，展示可读的体量与材料关系。'},
 tools:{title:'AI 工具列表',description:'查看本机已配置的 Agent、技能和专业软件连接。',action:'查询本机能力',icon:Wrench,example:'核对当前本机可用的工具与专业软件连接，说明哪些已就绪、哪些仍缺依赖，不调用任何生图或视频服务。'},
};
const imageExtensions = /\.(png|jpe?g|webp|gif|bmp)$/i;
const sceneExtensions = /\.(blend|glb|gltf|obj|fbx|stl|ply|png|jpe?g|webp)$/i;

export function CreativeStudio({kind,settings,onRun,onError}:Props){
 const details=labels[kind], Icon=details.icon;
 const [prompt,setPrompt]=useState(''),[provider,setProvider]=useState(kind==='video'?'codebuddy':settings.provider||'codex');
 const [model,setModel]=useState(settings.models?.[provider]||(settings.provider===provider?settings.model:'')||'');
 const [attachments,setAttachments]=useState<Row[]>([]),[uploading,setUploading]=useState(false),[submitting,setSubmitting]=useState(false);
 const [options,setOptions]=useState<Row>({size:'auto',quality:'auto',style:'',background:'auto',resolution:'1080P',aspect_ratio:'16:9',enable_audio:true,negative_prompt:'',media_model:''});
 const [catalog,setCatalog]=useState<Row>({runtimes:[],skills:[],servers:{}}),[capabilities,setCapabilities]=useState<Row>({});
 const inputRef=useRef<HTMLInputElement>(null),revision=useRef(0),mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{revision.current++;setPrompt('');setAttachments([]);setUploading(false);if(kind==='video'){setProvider('codebuddy');setModel(settings.models?.codebuddy||'')}},[kind]);
 useEffect(()=>{setModel(settings.models?.[provider]||(settings.provider===provider?settings.model:'')||'')},[provider,settings.models,settings.model,settings.provider]);
 useEffect(()=>{let valid=true;api('/creative/capabilities').then(value=>{if(valid)setCapabilities(value)}).catch(()=>{});return()=>{valid=false}},[]);
 useEffect(()=>{if(kind!=='tools')return;let valid=true;Promise.all([api('/runtimes'),api('/skills'),api('/mcp')]).then(([r,s,m])=>{if(valid)setCatalog({runtimes:r.items||[],skills:s.items||[],servers:m.servers||{}})}).catch(error=>onError(String(error)));return()=>{valid=false}},[kind]);
 const isImage=['image','edit','design'].includes(kind),isVideo=kind==='video',isScene=kind==='model'||kind==='render';
 const maximum=isVideo?2:3;
 const configure=(key:string,value:any)=>setOptions(previous=>({...previous,[key]:value}));
 async function addFiles(files:FileList|File[]|null){
  if(!files)return;
  const chosen=Array.from(files);
  if(attachments.length+chosen.length>maximum){onError(`此创作最多上传 ${maximum} 个参考文件`);return}
  if(chosen.some(file=>file.size>80*1024*1024)){onError('单个文件不能超过 80 MB');return}
  if(chosen.some(file=>!(isScene?sceneExtensions:imageExtensions).test(file.name))){onError(isScene?'请选择 Blender/GLB/OBJ 等模型或普通参考图片':'请选择 PNG、JPEG、WebP、GIF 或 BMP 图片');return}
  const current=revision.current;setUploading(true);
  try{
   const saved=await Promise.all(chosen.map(async file=>{const item=await upload(file);return{...item,preview:imageExtensions.test(file.name)?await fileUrl(item.url):''}}));
   if(mounted.current&&current===revision.current)setAttachments(previous=>[...previous,...saved]);
  }catch(error){onError(String(error))}finally{if(mounted.current&&current===revision.current)setUploading(false)}
 }
 async function generate(){
  if(!prompt.trim()){onError('请先描述本轮要生成的内容');return}
  if(kind==='edit'&&!attachments.length){onError('图像编辑需要至少一张原图');return}
  if(kind==='video'&&provider!=='codebuddy'){onError('原生 AI 视频请选择 WorkBuddy；Codex 生图能力不等同于视频生成');return}
  if(kind==='video'&&(prompt.trim()+(options.style?'\n视觉风格：'+options.style:'')).length>2500){onError('视频描述（含风格）最多 2500 字符，请精简后提交');return}
  setSubmitting(true);
  try{await onRun({kind,prompt:prompt.trim(),provider,model,attachments,options:{...options}})}
  catch(error){onError(String(error))}finally{if(mounted.current)setSubmitting(false)}
 }
 return <div className="studio" style={{overflow:'auto',padding:'26px 30px'}}>
  <div style={{maxWidth:920,margin:'0 auto'}}>
   <div className="studio-heading"><div><h2 style={{display:'flex',alignItems:'center',gap:10}}><Icon size={23} strokeWidth={1.5}/>{details.title}</h2><p>{details.description}</p></div></div>
   {kind==='tools'&&<div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16,marginBottom:24}}>
    <section className="data-table" style={{padding:18}}><h3 style={{fontSize:14,margin:'0 0 14px'}}>本机 Agent</h3>{catalog.runtimes.map((runtime:Row)=><div key={runtime.provider} style={{display:'flex',gap:9,alignItems:'center',margin:'12px 0'}}><Cpu size={17}/><div style={{flex:1}}><strong style={{fontSize:12}}>{runtime.provider==='codex'?'Codex':'WorkBuddy'}</strong><div className="small muted">{runtime.installed?runtime.version||'已检测到':'未检测到'}{runtime.error&&' · '+runtime.error}</div></div>{runtime.installed&&<CheckCircle2 size={16}/>}</div>)}</section>
    <section className="data-table" style={{padding:18}}><h3 style={{fontSize:14,margin:'0 0 14px'}}>专业软件连接</h3>{Object.entries(catalog.servers).map(([name,value])=><div key={name} style={{display:'flex',justifyContent:'space-between',fontSize:12,margin:'11px 0'}}><span>{name}</span><span className="muted">{(value as Row).enabled?'已配置，需真实连接':'未启用'}</span></div>)}</section>
    <section className="data-table" style={{padding:18,gridColumn:'1 / -1'}}><h3 style={{fontSize:14,margin:'0 0 14px'}}>本地技能</h3><div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}>{catalog.skills.filter((skill:Row)=>!skill.id.startsWith('_')).map((skill:Row)=><div key={skill.id}><strong style={{fontSize:12}}>{skill.name}</strong><div className="small muted" style={{marginTop:5,lineHeight:1.7}}>{skill.enabled?'已启用':'已关闭'} · {skill.description}</div></div>)}</div></section>
   </div>}
   <div className="data-table" style={{padding:24,overflow:'visible'}}>
    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20}}>
     <label className="field"><span>本机 Agent</span><select value={provider} onChange={event=>setProvider(event.target.value)}><option value="codex">Codex</option><option value="codebuddy">WorkBuddy{isVideo?' · 原生视频工具':''}</option></select></label>
     <label className="field"><span>Agent 模型</span><input value={model} onChange={event=>setModel(event.target.value)} placeholder="使用此 Agent 的当前模型"/></label>
    </div>
    <label className="field"><span>本轮创作描述</span><textarea value={prompt} onChange={event=>setPrompt(event.target.value)} placeholder={details.example} style={{minHeight:140,resize:'vertical',border:'1px solid var(--border)',borderRadius:6,padding:13,fontSize:13,lineHeight:1.8,background:'var(--bg)',color:'var(--text)'}}/></label>
    <button className="text-button" onClick={()=>setPrompt(details.example)} style={{marginBottom:20}}>使用示例描述</button>
    {kind!=='tools'&&<>
     <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:13}}><span style={{fontSize:12,color:'var(--muted)'}}>{isVideo?'首帧与尾帧（可选）':isScene?'模型与参考文件（可选）':kind==='edit'?'编辑原图与参考图':'参考图片（可选）'}</span><button className="secondary-button" onClick={()=>inputRef.current?.click()} disabled={uploading||attachments.length>=maximum}><ImagePlus size={15}/>{uploading?'上传中…':'上传真实文件'}</button></div>
     <div style={{display:'grid',gridTemplateColumns:`repeat(${maximum}, 1fr)`,gap:12,marginBottom:22}}>{Array.from({length:maximum},(_,index)=>{const item=attachments[index];return <div className="resource-card" key={item?.id||index}><div className="resource-preview" style={{height:140}}>{item?.preview?<img src={item.preview} alt={item.name}/>:<ImagePlus size={25} strokeWidth={1}/>}</div><div className="resource-info"><strong>{isVideo?(index===0?'首帧':'尾帧'):`图 ${index+1}`} · {item?.name||'未上传'}</strong>{item&&<div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}><span>{(item.size/1024).toFixed(0)} KB</span><button className="icon-button" aria-label={`移除${item.name}`} onClick={()=>setAttachments(previous=>previous.filter(entry=>entry.id!==item.id))}><Trash2 size={14}/></button></div>}</div></div>})}</div>
     <input ref={inputRef} type="file" multiple hidden accept={isScene?'.blend,.glb,.gltf,.obj,.fbx,.stl,.ply,.png,.jpg,.jpeg,.webp':'.png,.jpg,.jpeg,.webp,.gif,.bmp'} onChange={event=>{addFiles(event.target.files);event.target.value=''}}/>
     {(isImage||kind==='render')&&<div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20}}><label className="field"><span>画面尺寸</span><select value={options.size} onChange={event=>configure('size',event.target.value)}><option value="auto">由本机工具决定</option><option value="1024x1024">方形 · 1024 × 1024</option><option value="1536x1024">横向 · 1536 × 1024</option><option value="1024x1536">纵向 · 1024 × 1536</option></select></label><label className="field"><span>生成质量</span><select value={options.quality} onChange={event=>configure('quality',event.target.value)}><option value="auto">默认质量</option><option value="low">快速</option><option value="medium">标准</option><option value="high">高质量</option></select></label></div>}
     <label className="field"><span>视觉风格与材料</span><input value={options.style} onChange={event=>configure('style',event.target.value)} placeholder="例如：自然日光、混凝土与玻璃、轴测建筑体量"/></label>
     {isImage&&<label className="field"><span>背景</span><select value={options.background} onChange={event=>configure('background',event.target.value)}><option value="auto">由提示词决定</option><option value="opaque">不透明</option><option value="transparent">透明（以工具实际支持为准）</option></select></label>}
     {isVideo&&<><div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20}}><label className="field"><span>视频分辨率</span><select value={options.resolution} onChange={event=>configure('resolution',event.target.value)}><option>720P</option><option>1080P</option></select></label><label className="field"><span>画面比例</span><select disabled={attachments.length>0} value={options.aspect_ratio} onChange={event=>configure('aspect_ratio',event.target.value)}><option>16:9</option><option>9:16</option><option>1:1</option></select>{attachments.length>0&&<small className="muted">图生视频使用输入图片比例</small>}</label></div><label className="checkbox-line" style={{marginBottom:20}}><input type="checkbox" checked={options.enable_audio} onChange={event=>configure('enable_audio',event.target.checked)}/>生成音频（以模型支持为准）</label><label className="field"><span>排除内容</span><input value={options.negative_prompt} maxLength={2500} onChange={event=>configure('negative_prompt',event.target.value)} placeholder="例如：文字、闪烁、异常变形"/></label></>}
     {provider==='codebuddy'&&(isImage||isVideo)&&<label className="field"><span>媒体模型（可选，与上方 Agent 模型不同）</span><input value={options.media_model} onChange={event=>configure('media_model',event.target.value)} placeholder="留空使用 WorkBuddy 当前媒体模型"/></label>}
    </>}
    <div className="small muted" style={{lineHeight:1.9,margin:'2px 0 20px'}}>
     {isScene?'通过本机 Agent + Blender 操作真实模型；几何近似重建不等同于专用单图转 3D 模型。请先连接 Blender。':isVideo?'视频能力已在本机 WorkBuddy 工具中发现，具体账号、媒体模型和输出仍以本次返回为准。':capabilities.providers?.[provider]?.[kind]||'本机账号、模型与工具可用性以实际返回为准。'}
     {kind!=='tools'&&<><br/>本机工具本体可能消耗账号额度。WorkBuddy 媒体可能额外扣积分，实际回执会随任务显示；输出也可能包含平台水印。</>}
    </div>
    <div className="button-row" style={{justifyContent:'flex-end'}}><button className="primary-button" onClick={generate} disabled={uploading||submitting||!prompt.trim()}>{submitting?<Loader2 size={16} className="spin"/>:<Play size={15}/>} {submitting?'正在提交真实任务…':details.action}</button></div>
   </div>
  </div>
 </div>
}

export default CreativeStudio;
