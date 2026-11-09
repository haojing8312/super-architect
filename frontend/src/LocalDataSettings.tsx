import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle2, Database, FolderOpen, HardDrive, RefreshCw } from 'lucide-react';
import { api, openPath, pickPath, Row } from './api';
import './LocalDataSettings.css';

export default function LocalDataSettings({toast}:{toast:(message:string)=>void}) {
  const [info,setInfo]=useState<Row|null>(null),[imports,setImports]=useState<Row[]>([]),[source,setSource]=useState('');
  const [scan,setScan]=useState<Row|null>(null),[busy,setBusy]=useState(''),[error,setError]=useState('');
  const load=useCallback(async()=>{
    const [local,history]=await Promise.all([api('/local/info'),api('/local/imports')]);
    setInfo(local);setImports(history.items||[]);
  },[]);
  useEffect(()=>{load().catch(e=>setError(String(e)))},[load]);
  async function choose(){const path=await pickPath(true);if(path){setSource(path);setScan(null);setError('')}}
  async function run(label:string,task:()=>Promise<void>){setBusy(label);setError('');try{await task()}catch(e){setError((e as Error).message)}finally{setBusy('')}}
  async function scanSource(){if(!source.trim()){setError('请选择旧版本数据目录');return}await run('scan',async()=>{setScan(await api('/local/legacy/scan',{path:source.trim()}))})}
  async function importSource(){
    if(!scan)return;
    if(!confirm('导入会先把旧目录备份到当前数据目录的 backups 下，再为每个旧账号建立独立本地工作区。不会上传或联系任何服务器。继续？'))return;
    await run('import',async()=>{const result=await api('/local/legacy/import',{path:source.trim(),confirm:true});setScan(null);setSource('');await load();toast(`已导入 ${result.workspaces.length} 个本地工作区`)});
  }
  return <section className="local-data-settings">
    <h3><HardDrive size={17}/> 本地数据</h3>
    <p className="muted">项目、对话、附件与成果都保存在本机。没有工作台账号，也不会连接云端。</p>
    {info&&<div className="export-card"><div><strong>数据目录</strong><p className="account-prewrap">{info.data_dir}</p><p className="small muted">{info.path_policy}</p></div><button className="secondary-button" onClick={()=>openPath(info.data_dir)}><FolderOpen size={15}/>打开目录</button></div>}
    {info&&<div className="settings-list">{Object.entries(info.optional||{}).map(([key,value]:[string,any])=><div key={key}><div><strong>{key==='cad_viewer'?'DWG / DXF 完整查看器':key==='report_editor'?'完整作品汇报编辑器':key}</strong><p>{value.available?'已在本机找到可选组件':value.fallback}</p></div><span className="small">{value.available?<CheckCircle2 size={16}/>:<AlertCircle size={16}/>}</span></div>)}</div>}
    <div className="account-divider"/>
    <h3><Database size={17}/> 导入旧版本数据</h3>
    <p className="muted">旧匿名目录与不同旧会员目录不会被合并：每个来源先备份，再成为独立的本地数据目录。导入不访问生产服务，也不修改旧目录。</p>
    <label className="field"><span>旧版本数据目录</span><div className="input-row"><input value={source} onChange={event=>{setSource(event.target.value);setScan(null)}} placeholder="选择旧版本的工作台数据文件夹"/><button className="secondary-button" disabled={!!busy} onClick={()=>void choose()}>浏览</button></div></label>
    <div className="button-row"><button className="secondary-button" disabled={!!busy||!source.trim()} onClick={()=>void scanSource()}><RefreshCw size={15} className={busy==='scan'?'spin':''}/>{busy==='scan'?'正在扫描…':'扫描旧数据'}</button>{scan&&<button className="primary-button" disabled={!!busy} onClick={()=>void importSource()}>备份并导入</button>}</div>
    {scan&&<div className="settings-list">{scan.scopes.map((scope:Row)=><div key={scope.key}><div><strong>{scope.label}</strong><p>{scope.sessions} 个会话 · {scope.messages} 条消息 · {scope.files} 个附件 · {scope.knowledge} 份资料 · {scope.reports} 份汇报</p></div></div>)}</div>}
    {imports.length>0&&<><div className="account-divider"/><h3>已导入的工作区</h3><div className="settings-list">{imports.map((item:Row)=><div key={item.path}><div><strong>{item.label}</strong><p className="account-prewrap">{item.path}</p><p className="small muted">独立数据目录；启动时使用 --data-dir 指向该目录即可打开。备份：{item.backup}</p></div><button className="secondary-button" onClick={()=>openPath(item.path)}><FolderOpen size={15}/>打开</button></div>)}</div></>}
    {error&&<div className="inline-error" role="alert"><AlertCircle size={15}/>{error}</div>}
  </section>;
}
