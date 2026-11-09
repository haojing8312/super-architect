import { useEffect, useRef, useState } from 'react';
import { api, openPath, pickPath, Row } from './api';
import { Box, CheckCircle2, ExternalLink, FolderOpen, Info, Loader2, Play, RefreshCw, Square } from 'lucide-react';

type Props = { settings: Row; setSettings: (value: Row) => void; onError: (message: string) => void; toast: (message: string) => void };

export default function HostSettings({ settings, setSettings, onError, toast }: Props) {
  const [status, setStatus] = useState<Row | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const epoch = useRef(0);
  const mounted = useRef(true);
  const message = (value: unknown) => value instanceof Error ? value.message : String(value);

  async function detect(clearError = true): Promise<boolean> {
    const request = ++epoch.current;
    setLoading(true);
    try {
      const response = await api('/hosts');
      if (mounted.current && request === epoch.current) { setStatus(response); if (clearError) setError(''); }
      return true;
    } catch (value) {
      if (mounted.current && request === epoch.current) { const detail = message(value); setError(detail); onError(detail); }
      return false;
    } finally { if (mounted.current && request === epoch.current) setLoading(false); }
  }

  useEffect(() => {
    mounted.current = true;
    void detect();
    return () => { mounted.current = false; epoch.current += 1; };
  }, []);

  async function savePaths() {
    const fields: Row = {};
    for (const key of ['blender_executable', 'python_executable', 'sketchup_sdk']) fields[key] = String(settings[key] || '').trim();
    const saved = await api('/settings', fields, 'PUT');
    if (mounted.current) setSettings({ ...settings, ...Object.fromEntries(Object.keys(fields).map(key => [key, saved[key] ?? fields[key]])) });
  }

  async function saveAndDetect() {
    if (busy) return;
    setBusy('save'); setError('');
    try { await savePaths(); const detected = await detect(); if (mounted.current && detected) toast('专业软件路径已保存并检测'); }
    catch (value) { if (mounted.current) { const detail = message(value); setError(detail); onError(detail); } }
    finally { if (mounted.current) setBusy(''); }
  }

  async function control(command: 'start' | 'stop') {
    if (busy || loading) return;
    if (command === 'stop' && !window.confirm('停止本项目的隔离 Blender 宿主？正在执行的操作会等待完成，不会退出你的其它 Blender。')) return;
    setBusy(command); setError(''); epoch.current += 1;
    try {
      if (command === 'start') await savePaths();
      const result = await api(`/hosts/blender/${command}`, {});
      if (command === 'start' && (!result.ready || !result.owns_process)) throw new Error('尚未验证隔离宿主连接，请查看检测结果');
      if (command === 'stop' && !result.stopped) throw new Error('隔离宿主尚未停止');
      if (mounted.current) toast(command === 'start' ? (result.already_running ? '本项目 Blender 宿主已连接' : '本项目隔离 Blender 宿主已启动') : (result.already_stopped ? '本项目隔离宿主已经停止' : '本项目隔离 Blender 宿主已停止'));
      await detect(false);
    } catch (value) {
      if (mounted.current) { const detail = message(value); setError(detail); onError(detail); }
      await detect(false);
    } finally { if (mounted.current) setBusy(''); }
  }

  async function choose(key: string) {
    const value = await pickPath();
    if (value) setSettings({ ...settings, [key]: value });
  }

  const blender = status?.blender;
  return <>
    <div className="studio-heading compact"><div><h3>专业软件连接</h3><p className="muted small">查看本机程序、插件和运行环境。</p></div><button className="secondary-button" disabled={loading || !!busy} onClick={() => void detect()}><RefreshCw size={14} className={loading ? 'spin' : ''}/>{loading ? '检测中…' : '重新检测'}</button></div>
    <div className="export-card" style={{ marginTop: 16, alignItems: 'flex-start' }}>
      <Box size={22}/><div><strong>Blender {blender?.version || ''}</strong><p style={{ whiteSpace: 'normal', lineHeight: 1.7 }}>{blender?.ready ? '本项目隔离宿主已连接 · 后台工厂模式' : blender?.error || (loading ? '检测中…' : blender?.installed ? '程序已安装，隔离宿主尚未启动' : '尚未检测')}</p>{blender?.executable && <p style={{ overflowWrap: 'anywhere', whiteSpace: 'normal' }}>{blender.executable}</p>}<p className="small muted">启停只管理本项目进程，不加载或改写你的其它 Blender 配置。</p></div>
    </div>
    <div className="button-row" style={{ marginTop: 12 }}>
      <button className="primary-button" disabled={loading || !!busy || !blender?.installed || !blender?.helper_available || (blender?.port_occupied && !blender?.ready)} onClick={() => void control('start')}>{busy === 'start' ? <Loader2 size={14} className="spin"/> : <Play size={14}/>} {blender?.ready ? '确认隔离连接' : '启动隔离宿主'}</button>
      <button className="secondary-button" disabled={loading || !!busy || !blender?.ready || !blender?.owns_process} onClick={() => void control('stop')}>{busy === 'stop' ? <Loader2 size={14} className="spin"/> : <Square size={14}/>}停止本项目宿主</button>
      <a className="text-button" href="https://www.blender.org/download/" target="_blank" rel="noopener noreferrer"><ExternalLink size={14}/>官方安装</a>
    </div>
    {blender?.selection_change_pending && <p className="small muted" style={{ marginTop: 12 }}>已保存另一 Blender 路径；当前隔离进程继续使用原程序，停止后重新启动才会应用新路径。</p>}
    <div className="settings-list" style={{ marginTop: 20 }}>
      <div><CheckCircle2 size={17}/><div><strong>本机语音识别</strong><p>{status?.asr.ready ? '本地识别配置已就绪' : status?.asr.error || '检测中…'}</p><span className="small muted">识别模型与录音设置在“本机语音”中管理。</span></div></div>
      <div><Info size={17}/><div><strong>SketchUp 模型 SDK</strong><p>{status?.sdk.sdk_found ? '已检测到 SDK 文件，模型解析使用隔离进程' : '未检测到 SDK；可在下方指定本机路径'}</p>{status?.sdk.sdk_path && <p style={{ overflowWrap: 'anywhere' }}>{status.sdk.sdk_path}</p>}</div></div>
    </div>
    <h4 style={{ marginTop: 24, marginBottom: 12 }}>商业桌面软件与插件</h4>
    <div className="settings-list">{(status?.commercial || []).map((host: Row) => <div key={host.id} style={{ alignItems: 'flex-start' }}><div style={{ minWidth: 0 }}><strong>{host.name}</strong><p>{host.error}</p><span className="small muted">{host.plugin?.prepared ? `公开插件已准备（${host.plugin.version || '已校验'}），宿主内加载仍待验证` : '公开插件尚未在此产品目录准备'}</span>{host.plugin?.path && <p style={{ overflowWrap: 'anywhere' }}>{host.plugin.path}</p>}</div><div style={{ display: 'flex', flexDirection: 'column', gap: 8, flexShrink: 0 }}><a className="text-button" href={host.official_url} target="_blank" rel="noopener noreferrer"><ExternalLink size={13}/>官方来源</a>{host.plugin?.prepared && <button className="text-button" onClick={() => openPath(host.plugin.path).catch(value => onError(message(value)))}><FolderOpen size={13}/>插件目录</button>}</div></div>)}</div>
    <h4 style={{ marginTop: 24, marginBottom: 12 }}>本机路径</h4>
    {[['blender_executable', 'Blender 程序路径', '可留空，使用本产品已安装的 portable Blender'], ['python_executable', '专业 MCP Python 路径', '可留空，使用随产品提供的 Python'], ['sketchup_sdk', 'SketchUp SDK 路径', 'SketchUpAPI.dll 或其所在文件夹']].map(([key, label, placeholder]) => <label className="field" key={key}><span>{label}</span><div className="input-row"><input value={settings[key] || ''} disabled={!!busy} onChange={event => setSettings({ ...settings, [key]: event.target.value })} placeholder={placeholder}/><button className="secondary-button" disabled={!!busy} onClick={() => choose(key)}><FolderOpen size={14}/></button></div></label>)}
    <div className="button-row"><button className="secondary-button" disabled={!!busy || loading} onClick={() => void saveAndDetect()}><RefreshCw size={14}/>{busy === 'save' ? '保存检测中…' : '保存路径并检测'}</button></div>
    {status?.python.error && <p className="small muted" style={{ marginTop: 12 }}>{status.python.error}</p>}
    {status?.plugin_setup?.available && <p className="small muted" style={{ marginTop: 12, overflowWrap: 'anywhere' }}>插件获取工具：{status.plugin_setup.script_path}</p>}
    {error && <div className="inline-error" style={{ marginTop: 16 }}><Info size={15}/>{error}</div>}
  </>;
}
