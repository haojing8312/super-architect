import {ArrowUpRight,ArrowRight,MessageSquare,Box,FileText,BookOpen,Layers3,Search,Clock,Plus,Image,Compass,Grid2X2,Cpu,Pause,Play,Waves,Settings2,Activity,CheckCircle2,CircleDot,ArrowUpRight as Enter} from 'lucide-react';
import {useEffect,useState} from 'react';
import {Row} from './api';
import {profiles} from './profiles';
import ArchitectureScene from './ArchitectureScene';
import {departments,departmentById,taskStatusLabel,type DepartmentId,type HomeArtifact,type HomeTask} from './institute/data';
import './InstituteHome.css';

type CreativeTool = 'image'|'edit'|'render'|'video'|'model';
export function ToolCenter({onNavigate,onProfile,onCreative,onSettings}:{onNavigate:(page:string)=>void,onProfile:(id:string)=>void,onCreative:(id:CreativeTool)=>void,onSettings:()=>void}) {
 const [search,setSearch]=useState(''),[color,setColor]=useState('#245c47'),[copied,setCopied]=useState(false);
 const groups=[{title:'专业软件协作',items:profiles.filter(p=>!['assistant','rag','webreport'].includes(p.id)).map(p=>({id:p.id,name:p.name,description:p.description,icon:p.icon,action:()=>onProfile(p.id)}))},
 {title:'设计与创作',items:[['灵感出图','把设计描述转为视觉方向','image'],['图像精修','围绕参考图继续迭代','edit'],['模型表现','借助本机 Blender 渲染模型','render'],['动态演绎','通过本机 WorkBuddy 生成视频','video'],['图像建模','从参考图推敲三维造型','model']].map(([name,description,id])=>({id,name,description,icon:Image,action:()=>onCreative(id as CreativeTool)}))},
 {title:'文件与表达',items:[['canvas','创意画板','把想法、参考和分析放在同一张画布'],['drawing','图纸阅览','查看、检查与分析 CAD 图纸'],['model','模型阅览','查看三维模型与几何信息'],['resources','素材管理','保存常用图像、文档与设计参考'],['report','在线提案','把项目组织成可浏览的展示网页']].map(([id,name,description])=>({id,name,description,icon:Layers3,action:()=>onNavigate(id)}))}];
 return <div className="yg-tool-center"><header className="yg-page-heading"><div><p className="yg-eyebrow">YOUR DESIGN TOOLKIT</p><h1>工具中心</h1><p>连接熟悉的软件，把每一步设计串起来。</p></div><button className="secondary-button" onClick={onSettings}>管理软件连接<ArrowUpRight size={16}/></button></header><label className="yg-search"><Search size={17}/><input aria-label="搜索设计工具" placeholder="搜索软件、功能或工作方式…" value={search} onChange={e=>setSearch(e.target.value)}/></label>{groups.map(group=>{const items=group.items.filter(i=>(i.name+i.description).toLowerCase().includes(search.toLowerCase()));return items.length>0&&<section key={group.title}><h2>{group.title}<span>{items.length}</span></h2><div className="yg-tool-grid">{items.map(item=><button key={item.id} onClick={item.action}><span className="yg-tool-icon"><item.icon size={21}/></span><strong>{item.name}</strong><p>{item.description}</p><ArrowUpRight size={16} className="yg-tool-arrow"/></button>)}</div></section>})}<section className="yg-color-section"><h2>设计辅助</h2><div className="yg-color-picker"><div><strong>配色采集</strong><p>从屏幕取色，或选择色值带回你的设计。</p></div><input type="color" aria-label="选择设计颜色" value={color} onChange={e=>{setColor(e.target.value);setCopied(false)}}/><code>{color.toUpperCase()}</code>{window.pywebview&&<button className="secondary-button" onClick={async()=>{const value=await window.pywebview!.api.pick_color();if(value){setColor(value);setCopied(false)}}}>屏幕取色</button>}<button className="secondary-button" onClick={async()=>{await navigator.clipboard.writeText(color);setCopied(true)}}>{copied?'已复制':'复制色值'}</button></div></section>{!groups.some(g=>g.items.some(i=>(i.name+i.description).toLowerCase().includes(search.toLowerCase())))&&<p className="yg-empty-inline">没有匹配的工具，试试“建模”“汇报”或软件名称。</p>}</div>;
}

const quickLinks: {id: string; name: string; icon: typeof MessageSquare}[] = [
  {id: 'chat', name: '设计对话', icon: MessageSquare},
  {id: 'interior', name: '空间设计', icon: Box},
  {id: 'deliverables', name: '成果交付', icon: FileText},
  {id: 'knowledge', name: '项目知识', icon: BookOpen},
  {id: 'canvas', name: '创意画板', icon: Grid2X2},
  {id: 'tools', name: '工具中心', icon: Layers3},
];

function formatDate(value?: string) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('zh-CN', {month: '2-digit', day: '2-digit'});
}

function statusTone(status: string) {
  if (/running|streaming/.test(status)) return 'run';
  if (status==='awaiting_input') return 'wait';
  if (/fail/.test(status)) return 'fail';
  if (/completed|done/.test(status)) return 'done';
  return 'idle';
}

export default function WorkbenchHome({sessions,onSession,onNavigate,onNew,onSearch,runtimes,onSettings,tasks,artifacts,onArtifact}:{
  sessions:Row[],onSession:(s:Row)=>void,onNavigate:(page:string)=>void,onNew:(text?:string)=>void,onSearch:()=>void,runtimes:Row[],onSettings:()=>void,
  /** Real task rows from the local backlog. Never fabricate values when absent. */
  tasks?:HomeTask[],onArtifact?:(item:HomeArtifact)=>void,
  /** Real delivered artifacts; the list stays explicitly disconnected when absent. */
  artifacts?:HomeArtifact[],
}) {
 const [brief,setBrief]=useState('');
 const [paused,setPaused]=useState(false);
 const [reduced,setReduced]=useState(false);
 const [sceneMode,setSceneMode]=useState<'live'|'static'>('live');
 const connected=Array.isArray(tasks);
 const liveTasks=tasks??[];

 useEffect(()=>{
   const query=matchMedia('(prefers-reduced-motion: reduce)');
   const apply=()=>setReduced(query.matches);
   apply();
   query.addEventListener('change',apply);
   return ()=>query.removeEventListener('change',apply);
 },[]);

 const openDepartment=(id:DepartmentId)=>{const dept=departmentById(id);if(dept)onNavigate(dept.entry)};

 return <div className="ih-home">
  <section className="ih-stage" aria-label="建筑设计院场景与实时任务">
   <header className="ih-stage-bar">
    <div className="ih-stage-title"><span className="ih-eyebrow">SUPER ARCHITECT · INSTITUTE</span><strong>建筑设计院</strong><span className="ih-stage-sub">五所一中心 · 总师办评审区</span></div>
    <div className="ih-stage-tools">
      <span className={'ih-status '+(sceneMode==='static'?'is-frozen':reduced?'is-frozen':paused?'is-paused':'')}><i/>{sceneMode==='static'?'静态院区图':reduced?'已减弱动态':paused?'场景已暂停':'场景运行中'}</span>
      <button className="ih-tool" onClick={()=>setPaused(value=>!value)} aria-pressed={paused} disabled={sceneMode==='static'} title={paused?'继续播放场景动画':'暂停场景动画'}>{paused?<Play size={14}/>:<Pause size={14}/>}{paused?'继续':'暂停'}</button>
      <button className="ih-tool" onClick={()=>setReduced(value=>!value)} aria-pressed={reduced} disabled={sceneMode==='static'} title="按需关闭循环动作">{reduced?<Waves size={14}/>:<Activity size={14}/>}{reduced?'恢复动态':'减弱动态'}</button>
    </div>
   </header>
   <div className="ih-stage-body">
    <div className="ih-scene-wrap">
      <ArchitectureScene paused={paused} reducedMotion={reduced} onEnterDepartment={openDepartment} onStatus={setSceneMode}/>
      <div className="ih-scene-note"><span>7 个部门 · 原创三维场景</span><span>任务执行状态见右侧</span></div>
    </div>
    <aside className="ih-live-panel" aria-label="实时任务">
      <header><div><span className="ih-panel-kicker">TODAY</span><strong>实时任务</strong></div>{connected&&<em className="is-live">实时</em>}</header>
      <div className="ih-task-list">
        {(!connected||liveTasks.length===0)&&<div className="ih-panel-empty">
          <span className="ih-empty-mark"><CheckCircle2 size={17}/></span>
          <strong>当前空闲</strong>
          <p>开始一次设计后，本机执行进度与结果会显示在这里。</p>
          <button className="ih-panel-cta" onClick={()=>onNew()}>新建设计<ArrowRight size={13}/></button>
        </div>}
        {connected&&liveTasks.map(task=>{
          const dept=task.department?(departmentById(task.department)?.name||task.department):'';
          const progress=typeof task.progress==='number'&&Number.isFinite(task.progress)?Math.max(0,Math.min(1,task.progress)):null;
          const session=sessions.find(item=>item.id===task.sessionId);
          return <button className="ih-task" key={task.id} onClick={()=>{if(session)onSession(session)}} disabled={!session} title={session?'打开对应设计对话':'该任务没有可打开的设计对话'}>
            <span className={'ih-task-dot '+statusTone(task.status)}><CircleDot size={13}/></span>
            <span className="ih-task-copy"><strong>{task.title}</strong><small>{[dept,taskStatusLabel[task.status]||task.status].filter(Boolean).join(' · ')}{task.detail?' · '+task.detail:''}</small></span>
            {progress!==null&&<span className="ih-task-progress" aria-label={'进度 '+Math.round(progress*100)+'%'}><i style={{width:Math.round(progress*100)+'%'}}/></span>}
            {session&&<ArrowRight size={14}/>}
          </button>;
        })}
      </div>
      <footer><span>{connected&&liveTasks.length?`${liveTasks.filter(task=>['running','streaming','queued','pending','awaiting_input'].includes(task.status)).length} 项待处理 · ${liveTasks.length} 条记录`:'记录保存在本机'}</span><button className="ih-link" onClick={()=>onNavigate('usage')}>运行记录<ArrowUpRight size={13}/></button></footer>
    </aside>
   </div>
  </section>

  <section className="ih-brief" aria-label="设计需求">
    <div className="ih-brief-head"><Compass size={17}/><strong>今天推进哪个设计？</strong><kbd>Ctrl K</kbd></div>
    <form onSubmit={event=>{event.preventDefault();onNew(brief)}}>
      <input aria-label="设计需求" value={brief} onChange={event=>setBrief(event.target.value)} placeholder="例如：核对三层平面与结构轴网，整理一版评审提纲…"/>
      <button type="submit" aria-label="提交设计需求"><ArrowUpRight size={20}/></button>
    </form>
  </section>

  <nav className="ih-quick" aria-label="主要工作入口">{quickLinks.map(item=><button key={item.id} onClick={()=>onNavigate(item.id)}><item.icon size={15}/>{item.name}</button>)}<button onClick={()=>onNew()} className="ih-quick-new"><Plus size={15}/>新建设计</button></nav>

  <section className="ih-departments" aria-label="部门入口">
    {departments.map(dept=><button key={dept.id} className={'ih-dept ih-dept-'+dept.id} onClick={()=>onNavigate(dept.entry)}>
      <span className="ih-dept-code">{dept.roomCode}</span>
      <strong>{dept.name}</strong>
      <small>{dept.entryLabel} · {dept.capability}</small>
      {dept.gap?<em>{dept.gap}</em>:<em className="is-ok">已接入本机能力</em>}
      <Enter size={14} className="ih-dept-arrow"/>
    </button>)}
  </section>

  <div className="ih-lower">
    <section className="ih-recent" aria-label="最近项目">
      <header><div><Clock size={16}/><h2>最近项目</h2><span>{sessions.length?`${Math.min(sessions.length,5)} / ${sessions.length}`:''}</span></div><button className="ih-link" onClick={onSearch}>查看全部<ArrowRight size={14}/></button></header>
      {sessions.length?<div className="ih-recent-list">{sessions.slice(0,5).map(item=><button className="ih-recent-row" key={item.id} onClick={()=>onSession(item)}>
        <span className="ih-recent-icon"><MessageSquare size={16}/></span>
        <span className="ih-recent-copy"><strong>{item.title||'未命名对话'}</strong><small>{profiles.find(p=>p.id===item.profile)?.name||'设计助理'}{item.updated_at?' · '+formatDate(item.updated_at):''}</small></span>
        <ArrowUpRight size={15}/>
      </button>)}</div>:<div className="ih-recent-empty"><Layers3 size={26}/><div><strong>还没有项目</strong><p>创建第一次设计对话，项目会按本地工作区保存。</p></div><button className="ih-link" onClick={()=>onNew()}><Plus size={16}/>创建设计</button></div>}
    </section>
    <div className="ih-side-stack">
      <section className="ih-artifacts" aria-label="最近成果">
        <header><FileText size={16}/><h2>最近成果</h2></header>
        {(artifacts??[]).length?<div className="ih-artifact-list">{(artifacts??[]).slice(0,3).map(item=><button key={item.id} onClick={()=>onArtifact?onArtifact(item):onNavigate('deliverables')}><span>{item.kind||'成果'}</span><strong>{item.title}</strong><small>{formatDate(item.updatedAt)}</small></button>)}</div>:<p className="ih-mini-empty-line">还没有已交付成果。</p>}
      </section>
      <section className="ih-env" aria-label="本机创作环境">
        <header><Cpu size={16}/><h2>本机环境</h2></header>
        {['codex','codebuddy'].map(id=>{const found=runtimes.find(item=>item.provider===id);return <div className="ih-env-row" key={id}><span>{id==='codex'?'Codex':'WorkBuddy'}</span><small className={found?.installed?'is-ready':''}><i/>{found?.installed?'已检测到':'未检测到'}</small></div>})}
        <button className="ih-link" onClick={onSettings}><Settings2 size={13}/>配置创作环境</button>
      </section>
    </div>
  </div>
  <p className="ih-footnote">资料、项目与成果保存在本机工作区。场景动态可随时暂停，右侧显示真实任务与待确认内容。</p>
 </div>;
}
