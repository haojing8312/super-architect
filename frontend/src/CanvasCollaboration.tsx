import {useEffect, useRef, useState} from 'react';
import {Copy, Link2, RefreshCw, Share2, Unplug, X} from 'lucide-react';
import type {Row} from './api';
import {collaborationSnapshot, latestCollaboration, subscribeCollaboration} from './canvasCollaborationProtocol';
import type {CollaborationStatus} from './canvasCollaborationProtocol';

export type CollaborationActions = 'host' | 'stop' | 'join' | 'disconnect' | 'reconnect' | 'accept-remote' | 'local-copy';
type Props = {
  canvasId: string; ready: boolean; bound: boolean;
  onAction: (action: CollaborationActions, body?: Row) => Promise<void>;
  onSnapshot: (status: CollaborationStatus) => void;
  onOpenCanvas: (id: string, retainDraft?: boolean) => Promise<void>;
};

export function CanvasCollaboration(props: Props) {
  const callbacks = useRef(props); callbacks.current = props;
  const [state, setState] = useState<CollaborationStatus | null>(() => latestCollaboration()), [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [port, setPort] = useState(33822), [url, setUrl] = useState(''), [code, setCode] = useState(''), [shareUrl, setShareUrl] = useState(''), [notice, setNotice] = useState('');
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    const unsubscribe = subscribeCollaboration(value => {if (!alive.current) return; setState(value); callbacks.current.onSnapshot(value);});
    let polling = false;
    const poll = async () => {if (polling) return; polling = true; try {await collaborationSnapshot();} catch (failure) {if (alive.current) setError(String(failure));} finally {polling = false;}};
    void poll(); const timer = window.setInterval(() => void poll(), 1000);
    return () => {alive.current = false; clearInterval(timer); unsubscribe();};
  }, []);
  const addresses: string[] = Array.isArray(state?.share_urls || state?.urls) ? state!.share_urls || state!.urls : [];
  const selectedAddress = addresses.includes(shareUrl) ? shareUrl : addresses.find(value => !/^wss?:\/\/(?:127\.|\[::1\])/.test(value)) || addresses[0] || '';
  const current = state?.canvas_id === props.canvasId, role = state?.role || 'none';
  const status = ({idle: '未开启', starting: '正在开启', hosting: '分享中', connected: '已连接', disconnected: '已断开', stopped: '已停止', error: '连接错误'} as Record<string, string>)[state?.status || 'idle'] || state?.status;
  async function action(kind: CollaborationActions, body?: Row) {
    setBusy(true); setError('');
    try {await callbacks.current.onAction(kind, body);} catch (failure) {if (alive.current) {setOpen(true); setError(String(failure));}} finally {if (alive.current) setBusy(false);}
  }
  async function copyInvitation() {
    try {await navigator.clipboard.writeText(`主机地址：${selectedAddress}\n邀请码：${state?.invite_code || ''}`); setNotice('邀请信息已复制，请通过你选择的方式发送给同一局域网成员。');} catch (failure) {setError('复制失败，请手工复制地址和邀请码：' + String(failure));}
  }
  async function openCanvas(id: string, retainDraft = false) {setBusy(true); setError(''); try {await callbacks.current.onOpenCanvas(id, retainDraft);} catch (failure) {if (alive.current) setError(String(failure));} finally {if (alive.current) setBusy(false);}}
  return <section className="canvas-collaboration" aria-label="局域网画布协作">
    <div className="canvas-collaboration-heading"><button aria-expanded={open} onClick={() => setOpen(!open)}><Share2 size={14}/>局域网协作</button><span role="status" data-collaboration-role={role}>{status}{role !== 'none' ? ` · ${role === 'host' ? '主机' : '访客'}` : ''}{state?.connected ? ` · ${Number.isFinite(state.peer_count) ? state.peer_count + 1 : '?'}人` : ''}{state?.pending ? ' · 等待主机确认' : ''}</span>{role !== 'none' && !current && state?.canvas_id && <button disabled={busy} onClick={() => void openCanvas(state.canvas_id!)}><Link2 size={13}/>打开协作画布</button>}<span className="canvas-collaboration-limit">跨机器局域网尚未验收</span></div>
    {open && <div className="canvas-collaboration-content"><div className="canvas-collaboration-controls">
      {role === 'none' && <><label>本机共享端口<input aria-label="本机共享端口" type="number" min="1024" max="65535" value={port} onChange={event => setPort(Math.max(1024, Math.min(65535, Number(event.target.value) || 33822)))}/></label><button disabled={!props.ready || props.bound || busy || !state} onClick={() => void action('host', {canvas_id: props.canvasId, bind_host: '0.0.0.0', port})}><Share2 size={14}/>分享当前画布</button></>}
      {role === 'host' && <><label>分享地址<select aria-label="画布分享地址" value={selectedAddress} onChange={event => setShareUrl(event.target.value)}>{addresses.map(address => <option key={address}>{address}</option>)}</select></label><label>邀请码<input aria-label="画布分享邀请码" readOnly value={typeof state?.invite_code === 'string' ? state.invite_code : ''}/></label><button disabled={busy || !selectedAddress} onClick={() => void copyInvitation()}><Copy size={14}/>复制邀请</button><button disabled={busy} onClick={() => void action('stop')}><X size={14}/>停止分享</button></>}
      {role === 'guest' && <><button disabled={busy || !state?.connected} onClick={() => void action('disconnect')}><Unplug size={14}/>断开连接</button><button disabled={busy || !!state?.connected} onClick={() => void action('reconnect')}><RefreshCw size={14}/>重新连接</button><button disabled={busy} onClick={() => void action('stop')}>退出协作</button></>}
      {props.bound && role === 'none' && <button disabled={busy} onClick={() => void action('stop')}>退出已停止的协作</button>}
    </div>
    {role === 'none' && <form className="canvas-collaboration-join" onSubmit={event => {event.preventDefault(); void action('join', {url: url.trim(), invite_code: code.trim()});}}><label>主机地址<input aria-label="协作主机地址" placeholder="ws://192.168.1.10:33822/ws" value={url} required onChange={event => setUrl(event.target.value)}/></label><label>邀请码<input aria-label="加入邀请码" value={code} required autoComplete="off" onChange={event => setCode(event.target.value)}/></label><button disabled={!props.ready || props.bound || busy || !state} type="submit"><Link2 size={14}/>加入画布</button></form>}
    <p>仅分享选定画布。加入会创建本机项目副本；工作台主服务继续只在本机提供业务接口。</p>
    {notice && <p role="status">{notice}</p>}
    {(state?.conflict_copy_id || state?.conflict || error || state?.error) && <div className="canvas-collaboration-error" role="alert">{error || state?.conflict?.reason || state?.error || '发布未接受，本机修改已保留'}{state?.conflict_copy_id && <button disabled={busy} onClick={() => void openCanvas(state.conflict_copy_id!, true)}>打开冲突副本</button>}</div>}
    {props.bound && <div className="canvas-collaboration-recovery"><button disabled={busy || !props.ready} onClick={() => void action('local-copy')}>另存本地副本</button><button disabled={busy || !props.ready || !!state?.pending || !state?.connected || !current} onClick={() => void action('accept-remote')}>保留副本并同步共享版本</button></div>}
    </div>}
  </section>;
}
