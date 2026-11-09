import {useEffect,useRef,useState} from 'react';
import {Home,MessageSquare,Box,FileText,BookOpen,Grid2X2,Image,Clock,Cpu,Search,Settings,PanelLeftClose,PanelLeftOpen,Plus,ArrowUpRight,X} from 'lucide-react';
import {Row} from './api';
import {BrandMark,BRAND} from './Brand';
export const navigation=[
 {id:'home',label:'项目概览',detail:'从这里开始，继续最近的设计',icon:Home,group:'工作台'},
 {id:'chat',label:'设计助理',detail:'在对话中推进设计任务',icon:MessageSquare,group:'工作台'},
 {id:'interior',label:'空间设计',detail:'户型分析与室内三维方案',icon:Box,group:'工作台'},
 {id:'deliverables',label:'成果交付',detail:'方案文档、演示文稿与表格',icon:FileText,group:'工作台'},
 {id:'knowledge',label:'项目知识',detail:'规范、文档与专业资料',icon:BookOpen,group:'资源'},
 {id:'canvas',label:'创意画板',detail:'参考图、分析与自由表达',icon:Grid2X2,group:'资源'},
 {id:'resources',label:'素材管理',detail:'图片、文档与模型资源',icon:Image,group:'资源'},
 {id:'tools',label:'工具中心',detail:'专业软件与创作工具',icon:Grid2X2,group:'资源'},
 {id:'reminders',label:'计划提醒',detail:'安排本机任务与提醒',icon:Clock,group:'资源'},
 {id:'usage',label:'运行记录',detail:'查看任务执行与用量',icon:Cpu,group:'资源'},
];
export function Modal({label,onClose,children}:{label:string,onClose:()=>void,children:React.ReactNode}) {
 const ref=useRef<HTMLDivElement>(null),callback=useRef(onClose);callback.current=onClose;
 useEffect(()=>{const previous=document.activeElement as HTMLElement;const timer=setTimeout(()=>ref.current?.querySelector<HTMLElement>('input,button,select,textarea,[tabindex="0"]')?.focus(),20);
 const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();callback.current()}if(e.key==='Tab'){const nodes=Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')||[]).filter(n=>n.offsetParent!==null);if(!nodes.length)return;const first=nodes[0],last=nodes.at(-1)!;if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}};document.addEventListener('keydown',key);return()=>{clearTimeout(timer);document.removeEventListener('keydown',key);previous?.focus()}},[]);
 return <div className="modal-mask yg-modal-mask" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><div ref={ref} role="dialog" aria-modal="true" aria-label={label} className="yg-dialog">{children}</div></div>;
}
export function CommandPalette({onClose,onNavigate,onSession,sessions}:{onClose:()=>void,onNavigate:(id:string)=>void,onSession:(s:Row)=>void,sessions:Row[]}) {
 const [query,setQuery]=useState(''),[index,setIndex]=useState(0);
 const items=[...navigation.map(n=>({...n,run:()=>onNavigate(n.id)})),...sessions.map(s=>({id:s.id,label:s.title,detail:'最近对话',icon:MessageSquare,run:()=>onSession(s)}))].filter(i=>(i.label+i.detail).toLowerCase().includes(query.toLowerCase())).slice(0,16);
 function choose(item:typeof items[number]){item.run();onClose()}
 return <Modal label="快速导航" onClose={onClose}><div className="yg-command"><label><Search size={20}/><input autoFocus aria-label="搜索页面或对话" placeholder="想去哪里？搜索功能或最近对话…" value={query} onChange={e=>{setQuery(e.target.value);setIndex(0)}} onKeyDown={e=>{if(e.key==='ArrowDown'){e.preventDefault();setIndex(i=>(i+1)%Math.max(1,items.length))}if(e.key==='ArrowUp'){e.preventDefault();setIndex(i=>(i+items.length-1)%Math.max(1,items.length))}if(e.key==='Enter'&&items[index])choose(items[index])}}/><kbd>Esc</kbd></label><div className="yg-command-results">{items.map((item,i)=><button className={i===index?'selected':''} key={item.id} onClick={()=>choose(item)} onMouseEnter={()=>setIndex(i)}><item.icon size={18}/><span><strong>{item.label}</strong><small>{item.detail}</small></span><ArrowUpRight size={16}/></button>)}{!items.length&&<p className="yg-empty-inline">没有找到结果，换个关键词试试。</p>}</div><footer>↑ ↓ 选择 <span>Enter 打开</span></footer></div></Modal>;
}
export default function Navigation({page,collapsed,onCollapse,onNavigate,onNew,onSearch,onSettings}:{page:string,collapsed:boolean,onCollapse:()=>void,onNavigate:(id:string)=>void,onNew:()=>void,onSearch:()=>void,onSettings:()=>void}) {
 return <aside className={'yg-sidebar '+(collapsed?'is-collapsed':'')}><button className="yg-sidebar-brand" onClick={()=>onNavigate('home')} title="返回项目概览"><BrandMark size={34}/>{!collapsed&&<span><strong>{BRAND.name}</strong><small>SUPER ARCHITECT</small></span>}</button><button className="yg-new-design" title="新建设计对话" onClick={onNew}><Plus size={18}/>{!collapsed&&<span>新建设计</span>}</button><button className="yg-quick-search" title="快速导航 Ctrl K" onClick={onSearch}><Search size={16}/>{!collapsed&&<><span>快速导航</span><kbd>Ctrl K</kbd></>}</button><nav aria-label="主导航">{['工作台','资源'].map(group=><section key={group}>{!collapsed&&<p className="yg-nav-label">{group}</p>}{navigation.filter(n=>n.group===group).map(item=><button key={item.id} title={item.label} className={'yg-nav-link '+(page===item.id?'active':'')} aria-current={page===item.id?'page':undefined} onClick={()=>onNavigate(item.id)}><item.icon size={18}/>{!collapsed&&<span>{item.label}</span>}</button>)}</section>)}</nav><footer><button title="偏好与连接" onClick={onSettings}><Settings size={17}/>{!collapsed&&<span>偏好与连接</span>}</button><button title={collapsed?'展开侧栏':'收起侧栏'} aria-label={collapsed?'展开侧栏':'收起侧栏'} onClick={onCollapse}>{collapsed?<PanelLeftOpen size={17}/>:<PanelLeftClose size={17}/>} {!collapsed&&<span>收起侧栏</span>}</button>{!collapsed&&<small>设计在这里，持续生长。</small>}</footer></aside>;
}
