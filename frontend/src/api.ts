export type Row = Record<string, any>;
declare global { interface Window { pywebview?: { api: { pick_file: () => Promise<string | null>; pick_folder: () => Promise<string | null>; open_path: (path: string) => Promise<void>; minimize: () => void; close: () => void; maximize: () => void; pick_color: () => Promise<string> } }; __WORKBENCH_TOKEN__?: string; } }
let token: string | null = null, bootstrapRequest: Promise<void> | null = null, connectionLost = false;
export class ApiError extends Error {
  constructor(message: string, public status: number, public detail: unknown = null) {super(message); this.name = 'ApiError';}
}
export function reloadAfterConnectionLoss() {
  // Do not re-acquire a capability and retry work that belonged to the old page.
  connectionLost = true; token = null; bootstrapRequest = null; window.__WORKBENCH_TOKEN__ = undefined;
  window.location.reload();
}
async function bootstrap() {
  if (connectionLost) throw new ApiError('本机工作台正在重新连接', 401);
  if (!bootstrapRequest) bootstrapRequest = (async () => {
    const response = await fetch('/api/bootstrap', {cache: 'no-store'});
    if (!response.ok) throw new ApiError('无法建立本机工作台连接，请重新打开客户端', response.status);
    const value = await response.json();
    if (connectionLost) throw new ApiError('本机工作台正在重新连接', 401);
    if (typeof value.token !== 'string' || !value.token) throw new ApiError('本机工作台未返回有效连接凭据', 502);
    token = value.token;
  })().finally(() => {bootstrapRequest = null;});
  await bootstrapRequest;
}
export async function getToken(): Promise<string> {
  if (connectionLost) throw new ApiError('本机工作台正在重新连接', 401);
  if (token) return token;
  await bootstrap(); return token!;
}
export async function api(path: string, body?: any, method?: string): Promise<any> {
  const headers: Record<string,string> = { 'X-Workbench-Token': await getToken() };
  if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const res = await fetch('/api' + path, {method: method || (body === undefined ? 'GET' : 'POST'), headers, body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body)});
  if (!res.ok) {
    const error = await res.json().catch(() => ({})), detail = error.detail || error.message;
    if (res.status === 401 && detail === '工作台连接已失效，请重新打开客户端') {reloadAfterConnectionLoss(); throw new ApiError('本机连接已变化，工作台正在重新加载', 401);}
    throw new ApiError(typeof detail === 'string' ? detail : '请求失败 (' + res.status + ')', res.status, detail);
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
