import {useCallback,useEffect,useState} from 'react';
import {ArrowDownToLine,ArrowUpRight,CheckCircle2,RefreshCw,X,Download} from 'lucide-react';
import {api,Row} from './api';
import {BrandMark} from './Brand';

const labels:Record<string,string>={idle:'保持工作台常新',checking:'正在检查新版本',available:'有新版本可用',downloading:'正在下载更新',verifying:'下载完成，正在校验',ready:'更新已准备好',preparing:'正在准备升级窗口',installing:'安装窗口已打开',current:'已是最新版本',failed:'更新未完成'};
let startupChecked=false;
export function UpdateEntry({onOpen}:{onOpen:()=>void}) {
 const [state,setState]=useState<Row|null>(null);
 useEffect(()=>{let alive=true;const load=()=>api('/update/status').then(v=>{if(alive)setState(v)}).catch(()=>{});void load();const timer=setInterval(load,5000);const initial=setTimeout(()=>{if(startupChecked)return;startupChecked=true;void api('/update/check',{}).then(v=>{if(alive)setState(v)}).catch(()=>{})},4000);return()=>{alive=false;clearInterval(timer);clearTimeout(initial)}},[]);
 return <button className="yg-update-entry" onClick={onOpen} title="软件更新"><Download size={16}/><span>{state&&['available','ready'].includes(state.state)?`新版本 ${state.release?.version}`:state?.state==='downloading'?'更新下载中':'软件更新'}</span>{state&&['available','ready'].includes(state.state)&&<i/>}</button>;
}
export default function SoftwareUpdate({onClose}:{onClose?:()=>void}) {
 const [state,setState]=useState<Row|null>(null),[error,setError]=useState(''),[pending,setPending]=useState(''),[confirm,setConfirm]=useState(false);
 const load=useCallback(async()=>{try{setState(await api('/update/status'))}catch(e){setError(String(e))}},[]);
 useEffect(()=>{void load();const timer=setInterval(load,700);return()=>clearInterval(timer)},[load]);
 async function action(name:string){setPending(name);setError('');try{setState(await api('/update/'+name,{}))}catch(e){setError(String(e))}finally{setPending('')}}
 async function install(){setPending('install');setError('');try{if(!window.pywebview?.api.install_update)throw new Error('请在超级建筑师桌面窗口中安装更新');await window.pywebview.api.install_update(state!.release.version)}catch(e){setError(String(e));setPending('');setConfirm(false)}}
 const busy=!!pending||['checking','downloading','verifying','preparing','installing'].includes(state?.state);
 const total=state?.release?.size_bytes||0,received=state?.downloaded_bytes||0,percent=total?Math.min(100,received/total*100):0;
 const mb=(bytes:number)=>(bytes/1024/1024).toFixed(1)+' MB';
 const remaining=state?.bytes_per_second?Math.max(0,Math.ceil((total-received)/state.bytes_per_second)):null;
 return <section className="yg-update-panel" aria-label="软件更新"><header><div className="yg-brand-icon"><BrandMark size={42}/></div><div><p className="yg-eyebrow">SUPER ARCHITECT</p><h2>让创作保持向前</h2><p>当前版本 {state?.current_version||'…'}</p></div>{onClose&&<button className="icon-button" aria-label="关闭软件更新" onClick={onClose}><X size={20}/></button>}</header>
 <div className="yg-update-state" role="status"><CheckCircle2 size={20}/><div><strong>{labels[state?.state]||'正在读取更新状态'}</strong><p>{state?.release?`超级建筑师 ${state.release.version} · ${mb(total)}`:'检查并安装最新的功能与体验改进。'}</p></div></div>
 {['downloading','verifying'].includes(state?.state)&&<div className="yg-download-progress"><div><strong>{percent.toFixed(0)}%</strong><span>{mb(received)} / {mb(total)}</span></div><progress aria-label="更新下载进度" max={100} value={percent}/><p>{state?.state==='verifying'?'正在校验签名与文件完整性':`${mb(state?.bytes_per_second||0)}/s${remaining!==null?` · 预计剩余 ${remaining<60?remaining+' 秒':Math.ceil(remaining/60)+' 分钟'}`:''}`}</p></div>}
 {state?.release?.changelog&&<div className="yg-release-notes"><h3>本次更新</h3>{String(state.release.changelog).split('\n').filter(Boolean).map((line,i)=><p key={i}>{line}</p>)}</div>}
 {(error||state?.error)&&<p className="yg-alert" role="alert">{error||state?.error}</p>}
 {state?.last_result&&<p className="yg-last-update">上次升级 · {state.last_result.version}：{state.last_result.message}</p>}
 {confirm?<div className="yg-install-confirm" role="alertdialog" aria-label="确认升级"><h3>升级并重新打开超级建筑师？</h3><p>请先保存当前编辑。升级前会检查运行任务，备份已保存的项目索引与设置；独立窗口会持续显示进度。</p><div><button className="secondary-button" disabled={!!pending} onClick={()=>setConfirm(false)}>稍后安装</button><button className="primary-button" disabled={!!pending} onClick={()=>void install()}>升级并重启<ArrowUpRight size={16}/></button></div></div>:<footer>
 {state?.release&&['available','failed'].includes(state.state)&&<button className="primary-button" disabled={busy} onClick={()=>void action('download')}><ArrowDownToLine size={16}/>下载更新</button>}
 {state?.state==='ready'&&<button className="primary-button" disabled={busy||!state.install_supported||!window.pywebview?.api.install_update} onClick={()=>setConfirm(true)}>立即升级<ArrowUpRight size={16}/></button>}
 {['downloading','verifying'].includes(state?.state)?<button className="secondary-button" disabled={!!pending} onClick={()=>void action('cancel')}>取消下载</button>:<button className="secondary-button" disabled={busy} onClick={()=>void action('check')}><RefreshCw size={16} className={pending==='check'?'spin':''}/>检查更新</button>}
 </footer>}
 {state?.state==='ready'&&!state.install_supported&&<p className="yg-last-update">当前为开发环境。完整的升级与重启由正式安装版提供。</p>}
 </section>;
}
