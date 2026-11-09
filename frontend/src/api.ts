export type Row = Record<string, any>;
declare global { interface Window { pywebview?: { api: { install_update: (version:string) => Promise<{ready:boolean}>; pick_file: () => Promise<string | null>; pick_folder: () => Promise<string | null>; open_path: (path: string) => Promise<void>; minimize: () => void; close: () => void; maximize: () => void; pick_color: () => Promise<string> } }; __WORKBENCH_TOKEN__?: string; } }
let token: string | null = null, accountScope: string | null = null, bootstrapRequest: Promise<void> | null = null, closingScope = false;
export class ApiError extends Error {
  constructor(message: string, public status: number, public detail: unknown = null) {super(message); this.name = 'ApiError';}
}
export function reloadAfterAccountChange() {
  // Never reacquire a new account capability to retry old page operations.
  closingScope = true; token = null; bootstrapRequest = null; window.__WORKBENCH_TOKEN__ = undefined;
  window.location.reload();
}
async function bootstrap() {
  if (closingScope) throw new ApiError('账号已切换，请等待工作台重新加载', 401);
  if (!bootstrapRequest) bootstrapRequest = (async () => {
    const response = await fetch('/api/bootstrap', {cache: 'no-store'});
    if (!response.ok) throw new ApiError('无法建立本机工作台连接，请重新打开客户端', response.status);
    const value = await response.json();
    if (closingScope) throw new ApiError('账号已切换，请等待工作台重新加载', 401);
    if (typeof value.token !== 'string' || !value.token) throw new ApiError('本机工作台未返回有效连接凭据', 502);
    token = value.token; accountScope = typeof value.account_scope === 'string' && value.account_scope ? value.account_scope : 'anonymous';
  })().finally(() => {bootstrapRequest = null;});
  await bootstrapRequest;
}
export async function getToken(): Promise<string> {
  if (closingScope) throw new ApiError('账号已切换，请等待工作台重新加载', 401);
  if (token) return token;
  await bootstrap(); return token!;
}
export async function getAccountScope(): Promise<string> {
  // An old page's pending private-cache write stays in its captured old scope.
  // A full reload creates new module state and reads the new bootstrap scope.
  if (accountScope) return accountScope;
  await bootstrap(); return accountScope!;
}
function errorMessage(detail: unknown, status: number, auth: boolean): string {
  if (auth) {
    const value = typeof detail === 'string' ? detail : '';
    if (status === 409 && value === '请先完成或停止正在执行的 Agent 任务，再切换会员账号') return value;
    if (status === 409 && value === '请先结束画布协作，再切换会员账号') return value;
    if (/账号.*停用|账号.*禁用|用户.*禁用/.test(value)) return '此会员账号已停用，请联系管理端管理员';
    if (/密码.*错误|密码不正确|账号或密码|用户名或密码/.test(value)) return '会员账号或密码不正确';
    if (/验证码/.test(value)) return '管理服务要求验证码，请使用管理端支持的验证方式';
    if (/租户|tenant/i.test(value)) return '租户信息无效，请核对租户编号';
    return `会员会话请求失败 (${status})，请核对账号、租户与管理服务连接`;
  }
  return typeof detail === 'string' ? detail : '请求失败 (' + status + ')';
}
export async function api(path: string, body?: any, method?: string): Promise<any> {
  const headers: Record<string,string> = { 'X-Workbench-Token': await getToken() };
  if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const res = await fetch('/api' + path, {method: method || (body === undefined ? 'GET' : 'POST'), headers, body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body)});
  const observedScope = res.headers.get('X-Workbench-Account-Scope');
  const accountOperation = path === '/remote/login' || path === '/remote/logout';
  if (observedScope && accountScope && observedScope !== accountScope) {
    if (accountOperation && res.ok) {closingScope = true; token = null;}
    else {reloadAfterAccountChange(); throw new ApiError('账号数据范围已变化，工作台正在重新加载', 401);}
  }
  if (!res.ok) {
    const error = await res.json().catch(() => ({})), detail = error.detail || error.message;
    if (res.status === 401 && detail === '工作台连接已失效，请重新打开客户端') {reloadAfterAccountChange(); throw new ApiError('本机连接已变化，工作台正在重新加载', 401);}
    const auth = /^\/remote\/(?:login|logout|me)$/.test(path);
    // Authentication validation responses can contain submitted fields; never
    // stringify them or expose passwords/tokens in the product error surface.
    throw new ApiError(errorMessage(detail, res.status, auth), res.status, auth ? null : detail);
  }
  return res.json();
}
export async function pickPath(folder = false): Promise<string | null> {
  if (window.pywebview) return folder ? window.pywebview.api.pick_folder() : window.pywebview.api.pick_file();
  return prompt(folder ? '输入本机文件夹的完整路径' : '输入本机文件的完整路径');
}
export async function openPath(path: string) {
  if (window.pywebview) await window.pywebview.api.open_path(path);
  else await navigator.clipboard.writeText(path);
}
export async function upload(file: File): Promise<Row> { const data = new FormData(); data.append('file', file); return api('/attachments', data); }
export async function fileUrl(url: string): Promise<string> { return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(await getToken()); }
