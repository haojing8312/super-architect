import SoftwareUpdate from './SoftwareUpdate';
import {useEffect, useRef, useState} from 'react';
import {AlertCircle, CheckCircle2, Download, LogIn, LogOut, Plug, RefreshCw} from 'lucide-react';
import {api, openPath, reloadAfterAccountChange} from './api';
import type {Row} from './api';
import './AccountSettings.css';

const scalar = (value: unknown): string => typeof value === 'string' ? value : typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
const decimal = (value: unknown): string => typeof value === 'number' && Number.isFinite(value) ? String(value) : typeof value === 'string' && /^-?\d+(?:\.\d+)?$/.test(value) ? value : '';
const date = (value: unknown): string => {const text = scalar(value); if (!text) return ''; const parsed = new Date(text); return Number.isNaN(parsed.getTime()) ? text : parsed.toLocaleString('zh-CN');};

export default function AccountSettings({draft, setDraft, toast}: {draft: Row; setDraft: (value: Row) => void; onSave: () => Promise<void>; toast: (message: string) => void}) {
  const [account, setAccount] = useState(''), [password, setPassword] = useState(''), [member, setMember] = useState<Row | null>(null), [manifest, setManifest] = useState<Row | null>(null);
  const [release, setRelease] = useState<Row | null>(null), [releaseChecked, setReleaseChecked] = useState(false), [updateAvailable, setUpdateAvailable] = useState(false), [installer, setInstaller] = useState(''), [error, setError] = useState('');
  const [busy, setBusy] = useState(''), [memberState, setMemberState] = useState('loading');
  const [savedRealm, setSavedRealm] = useState<string | null>(null);
  const busyRef = useRef(false), mounted = useRef(true), operation = useRef(0), callbacks = useRef({draft, toast}); callbacks.current = {draft, toast};
  const configured = !!String(draft.admin_url || '').trim();
  const realm = `${String(draft.admin_url || '').trim().replace(/\/$/, '')}\n${draft.admin_tenant_id ?? 0}`;
  const pendingConnection = savedRealm === null || realm !== savedRealm;
  useEffect(() => {
    mounted.current = true; const ticket = ++operation.current;
    api('/settings').then(saved => {if (mounted.current && ticket === operation.current) setSavedRealm(`${String(saved.admin_url || '').trim().replace(/\/$/, '')}\n${saved.admin_tenant_id ?? 0}`);}).catch(failure => {if (mounted.current && ticket === operation.current) setError(String(failure));});
    api('/remote/me').then(value => {if (mounted.current && ticket === operation.current) {setMember(value); setMemberState(value.logged_in ? 'connected' : 'anonymous');}}).catch(failure => {if (mounted.current && ticket === operation.current) {setMember(null); setMemberState('unavailable'); setError(String(failure));}});
    return () => {mounted.current = false; operation.current++;};
  }, []);
  useEffect(() => {
    if (savedRealm === null || realm === savedRealm) return;
    operation.current++; setMember(null); setManifest(null); setRelease(null); setReleaseChecked(false); setUpdateAvailable(false); setInstaller(''); setPassword(''); setMemberState('configuration'); setError('');
  }, [realm, savedRealm]);

  function validateConnection(allowEmpty = false) {
    const value = String(callbacks.current.draft.admin_url || '').trim();
    if (!value && !allowEmpty) throw new Error('可继续本机创作；使用会员服务前请填写独立管理端地址');
    if (value) {let url: URL; try {url = new URL(value);} catch {throw new Error('管理端地址格式无效');}
      if (url.username || url.password || url.search || url.hash) throw new Error('管理端地址不能包含账号密码、查询参数或片段');
      if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) throw new Error('管理端使用HTTPS；本机调试可使用回环HTTP地址');}
    const tenant = Number(callbacks.current.draft.admin_tenant_id ?? 0);
    if (!Number.isSafeInteger(tenant) || tenant < 0 || tenant > 2147483647) throw new Error('租户编号须为0至2147483647的整数');
    return tenant;
  }
  async function run(label: string, task: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(label); setError('');
    try {await task();}
    catch (failure) {if (mounted.current) setError(failure instanceof Error ? failure.message : '操作未完成，请检查服务连接');}
    finally {busyRef.current = false; if (mounted.current) setBusy('');}
  }
  function returnToAccountSettings() {history.replaceState(null, '', location.pathname + location.search + '#settings-account');}
  async function saveConnection() {
    const tenant = validateConnection(true); returnToAccountSettings();
    await api('/settings', {admin_url: String(callbacks.current.draft.admin_url || '').trim().replace(/\/$/, ''), admin_tenant_id: tenant}, 'PUT');
    setPassword(''); reloadAfterAccountChange();
  }
  async function login() {
    if (pendingConnection) throw new Error('请先保存连接并重新加载，再登录会员账号');
    const tenant = validateConnection(), submittedPassword = password, submittedAccount = account.trim();
    if (!submittedAccount || !submittedPassword) throw new Error('请填写会员账号和密码');
    returnToAccountSettings();
    try {await api('/remote/login', {account: submittedAccount, password: submittedPassword, tenant_id: tenant}); setPassword(''); reloadAfterAccountChange();}
    finally {if (mounted.current) setPassword('');}
  }
  async function logout() {returnToAccountSettings(); await api('/remote/logout', {}); setPassword(''); setMember(null); reloadAfterAccountChange();}
  async function syncManifest() {if (pendingConnection) throw new Error('请先保存连接并重新加载'); const value = await api('/remote/manifest'); if (mounted.current) {setManifest(value); callbacks.current.toast('产品清单已同步');}}
  async function checkRelease() {
    setRelease(null); setUpdateAvailable(false); setReleaseChecked(false); setInstaller('');
    const value = await api('/remote/releases/latest');
    if (!mounted.current) return;
    const item = value.release && typeof value.release === 'object' && !Array.isArray(value.release) ? value.release : null;
    setRelease(item); setUpdateAvailable(value.update_available === true && !!item); setReleaseChecked(true);
  }
  async function downloadUpdate() {
    if (!release || !updateAvailable) throw new Error('请先检查可用签名版本');
    const result = await api('/remote/download-update', {});
    if (result.signature_verified !== true || result.verified !== true || typeof result.path !== 'string' || !result.path) throw new Error('版本包没有返回有效签名验证结果，未作为可用更新展示');
    if (mounted.current) {setInstaller(result.path); callbacks.current.toast('签名版本包已下载并校验；尚未安装');}
  }
  const profile = member?.profile && typeof member.profile === 'object' ? member.profile : {};
  const user = profile.user && typeof profile.user === 'object' && !Array.isArray(profile.user) ? profile.user : {};
  const displayName = scalar(user.display_name || user.nickname || user.username) || '会员账号';
  const level = scalar(user.level?.name ?? profile.level?.name ?? user.level_name ?? profile.level_name);
  const points = decimal(user.point ?? user.points ?? profile.point ?? profile.points);
  const rawRights = profile.entitlements ?? profile.benefits ?? profile.rights ?? user.entitlements;
  const rights = Array.isArray(rawRights) ? rawRights.map(value => typeof value === 'string' ? value : value && typeof value === 'object' ? scalar(value.name || value.title) : '').filter(Boolean) : [];
  const legacyVersion = manifest?.version;

  return <section className="account-settings">
    <h3>账号与管理端</h3><p className="muted">会员会话由管理服务提供；本机Agent额度在对应工具账户查看。</p>
    <div className="account-local-mode">{!configured ? '未设置管理端，可以继续本机创作。' : memberState === 'unavailable' ? '管理服务暂时不可用，可以继续本机创作；会员状态尚未确认。' : '本机创作可独立使用。'}<br/>登录后使用该会员的工作区，退出后返回本机工作区；已有项目会保留。</div>
    <label className="field"><span>管理端 API 地址</span><input aria-label="管理端 API 地址" disabled={!!busy} placeholder="https://你的独立管理端域名" value={draft.admin_url || ''} onChange={event => setDraft({...draft, admin_url: event.target.value})}/></label>
    <label className="field account-tenant"><span>租户编号</span><input aria-label="租户编号" type="number" min="0" max="2147483647" step="1" disabled={!!busy} value={draft.admin_tenant_id ?? 0} onChange={event => {const value = event.target.value; if (value !== '') setDraft({...draft, admin_tenant_id: Number(value)});}}/><small>默认0；以管理端提供的租户编号为准。</small></label>
    <div className="button-row">{pendingConnection && <button className="secondary-button" disabled={!!busy || savedRealm === null} onClick={() => void run('保存连接', saveConnection)}><RefreshCw size={15} className={busy === '保存连接' ? 'spin' : ''}/>{busy === '保存连接' ? '正在保存并重新加载…' : '保存连接并重新加载'}</button>}<button className="secondary-button" disabled={!!busy || !configured || pendingConnection} onClick={() => void run('同步配置', syncManifest)}><RefreshCw size={15} className={busy === '同步配置' ? 'spin' : ''}/>{busy === '同步配置' ? '正在同步…' : '同步产品清单'}</button><button className="secondary-button" disabled={!!busy || !configured || pendingConnection} onClick={() => void run('刷新会话', async () => {const value = await api('/remote/me'); if (mounted.current) {setMember(value); setMemberState(value.logged_in ? 'connected' : 'anonymous');}})}>刷新会员会话</button></div>
    {pendingConnection && savedRealm !== null && <p className="small muted">第一步：保存连接并重新加载。第二步：回到这里填写账号密码并登录；当前登录状态会清理，已有项目保留。</p>}
    <div className="account-divider"/>
    {member?.logged_in === true ? <div className="account-profile"><strong><CheckCircle2 size={16}/>已登录：{displayName}</strong><dl>{scalar(user.username) && <><dt>用户名</dt><dd>{scalar(user.username)}</dd></>}{scalar(user.id) && <><dt>会员编号</dt><dd>{scalar(user.id)}</dd></>}{date(profile.expires_at || profile.expiresAt) && <><dt>会话有效期</dt><dd>{date(profile.expires_at || profile.expiresAt)}</dd></>}{level && <><dt>会员等级</dt><dd>{level}</dd></>}{points !== '' && <><dt>会员积分</dt><dd>{points}</dd></>}{rights.length > 0 && <><dt>服务权益</dt><dd>{rights.join('、')}</dd></>}</dl><button className="secondary-button" disabled={!!busy} onClick={() => void run('退出', logout)}><LogOut size={15}/>{busy === '退出' ? '正在退出…' : '退出会员账号'}</button></div> : <form className="account-login" onSubmit={event => {event.preventDefault(); void run('登录', login);}}><label className="field"><span>会员账号</span><input aria-label="会员账号" autoComplete="username" maxLength={100} disabled={!!busy || pendingConnection} value={account} onChange={event => setAccount(event.target.value)}/></label><label className="field"><span>密码</span><input aria-label="会员密码" type="password" autoComplete="current-password" maxLength={200} disabled={!!busy || pendingConnection} value={password} onChange={event => setPassword(event.target.value)}/></label><button className="secondary-button" type="submit" disabled={!!busy || pendingConnection || !configured || !account.trim() || !password}><LogIn size={15}/>{busy === '登录' ? '正在登录…' : '登录'}</button></form>}
    {manifest?.notice && <div className="export-card"><div><strong>{scalar(manifest.notice.title)}</strong><p className="account-prewrap">{scalar(manifest.notice.content)}</p></div></div>}
    {Array.isArray(manifest?.skills) && manifest!.skills.length > 0 && <div className="settings-list">{manifest!.skills.map((skill: Row) => <div key={scalar(skill.skillKey)}><div><strong>{scalar(skill.name)}</strong><p>{scalar(skill.description)}</p><span className="small muted">版本 {scalar(skill.version)}</span></div><button className="secondary-button" disabled={!!busy || !skill.downloadUrl || !skill.sha256} onClick={() => void run('安装技能', async () => {await api(`/remote/skills/${encodeURIComponent(skill.skillKey)}/install`, {}); callbacks.current.toast('技能已下载、校验并导入');})}><Plug size={14}/>{skill.downloadUrl && skill.sha256 ? '安装' : '本机技能'}</button></div>)}</div>}
    <div className="account-divider"/><SoftwareUpdate/>
    {error && <div className="inline-error" role="alert"><AlertCircle size={15}/>{error}</div>}
  </section>;
}
