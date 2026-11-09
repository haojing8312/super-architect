import {getToken, Row} from './api';

export type CollaborationStatus = Row & {
  role: 'host' | 'guest' | 'none'; status: string; connected: boolean; canvas_id: string | null;
  revision: number; peer_id: string; peer_count: number; pending: boolean; pending_remote?: boolean;
  hash: string; error?: string; document?: Row; accepted_revision?: number | null;
  pending_change_id?: string | null; last_ack_change_id?: string | null; last_ack_revision?: number | null;
  conflict_copy_id?: string | null; conflict?: Row | null; physical_lan_verified: boolean;
};
export type CanvasDraft = {canvasId: string; branchId?: string; document: Row; localRevision: number; baseRevision: number | null; peerId: string | null; changeId?: string | null; submittedRevision?: number | null; dirty: boolean; updatedAt: number};
export type CollaborationBinding = {canvasId: string; peerId: string; role: 'host' | 'guest'; baseRevision: number; state: CollaborationStatus; pendingId: string | null; pendingLocalRevision: number | null; publishing: boolean};
const bindings = new Map<string, CollaborationBinding>();
const observers = new Set<(value: CollaborationStatus) => void>();
let latest: CollaborationStatus | null = null, snapshotRequest: Promise<CollaborationStatus> | null = null, commandTail: Promise<void> = Promise.resolve(), contextEpoch = 0, contextChanging = false;
let branchId: string | null = null;
export function canvasWindowBranch(): string {
  if (branchId) return branchId;
  try {
    const storageKey = 'architecture-canvas-draft-branch';
    const existing = sessionStorage.getItem(storageKey);
    const navigation = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
    // New tabs/window.open can inherit sessionStorage from their opener. Only
    // a reload resumes that document's branch; every other initial navigation
    // gets a fresh identity even when a stored UUID was copied into this tab.
    branchId = navigation?.type === 'reload' && existing && /^[a-f0-9-]{36}$/.test(existing) ? existing : crypto.randomUUID();
    sessionStorage.setItem(storageKey, branchId);
  }
  catch {branchId = crypto.randomUUID();}
  return branchId;
}

export class CollaborationError extends Error {
  constructor(message: string, public status = 0, public detail: unknown = null) {super(message); this.name = 'CollaborationError';}
}
export function observeCollaboration(value: CollaborationStatus) {
  latest = value;
  for (const binding of bindings.values()) {
    if (value.canvas_id === binding.canvasId && value.peer_id === binding.peerId && value.role === binding.role) binding.state = value;
    else binding.state = {...binding.state, connected: false, status: 'stopped', error: '协作已切换或停止，未发布内容保留在本机'};
  }
  observers.forEach(observer => observer(value));
  return value;
}
export function latestCollaboration() {return latest;}
export function subscribeCollaboration(observer: (value: CollaborationStatus) => void) {observers.add(observer); return () => {observers.delete(observer);};}
export function bindingFor(id: string) {return bindings.get(id);}
export function bindCollaboration(id: string, state: CollaborationStatus, baseRevision = state.revision) {
  if (state.canvas_id !== id || state.role === 'none') throw new CollaborationError('协作画布标识不匹配');
  const existing = bindings.get(id);
  if (existing && existing.peerId === state.peer_id && existing.role === state.role) {existing.state = state; return existing;}
  const binding: CollaborationBinding = {canvasId: id, peerId: state.peer_id, role: state.role, baseRevision, state, pendingId: state.pending_change_id || null, pendingLocalRevision: null, publishing: false};
  bindings.set(id, binding); return binding;
}
export function clearCollaborationBindings() {bindings.clear();}
export function recoverPendingDraft(id: string, draft: CanvasDraft) {const binding = bindings.get(id); if (binding && binding.peerId === draft.peerId) {binding.baseRevision = draft.baseRevision ?? binding.baseRevision; binding.pendingLocalRevision = draft.submittedRevision ?? null; if (draft.changeId && binding.state.last_ack_change_id === draft.changeId && Number.isInteger(binding.state.last_ack_revision)) binding.baseRevision = binding.state.last_ack_revision!; if (binding.state.pending) binding.pendingId = binding.state.pending_change_id || draft.changeId || null;}}
export function acceptLateAcknowledgement(id: string, state: CollaborationStatus): number | null {
  const binding = bindings.get(id);
  if (!binding || binding.publishing || !binding.pendingId || binding.peerId !== state.peer_id || state.canvas_id !== id || state.pending || state.last_ack_change_id !== binding.pendingId || !Number.isInteger(state.last_ack_revision)) return null;
  const revision = binding.pendingLocalRevision; binding.baseRevision = state.last_ack_revision!; binding.pendingId = null; binding.pendingLocalRevision = null; return revision;
}
export function acceptCollaborationVersion(id: string, state: CollaborationStatus) {const binding = bindings.get(id); if (binding && state.canvas_id === id && binding.peerId === state.peer_id && !binding.publishing) {binding.baseRevision = state.revision; binding.state = state;}}

async function request(path = '', body?: Row, emit = true): Promise<CollaborationStatus> {
  const controller = new AbortController(), timer = window.setTimeout(() => controller.abort(), 15000);
  try {
    const headers: Record<string, string> = {'X-Workbench-Token': await getToken()}; if (body) headers['Content-Type'] = 'application/json';
    const response = await fetch('/api/canvas-collaboration' + path, {method: body ? 'POST' : 'GET', headers, ...(body ? {body: JSON.stringify(body)} : {}), signal: controller.signal});
    const value = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = value.detail ?? value;
      if (detail && typeof detail === 'object' && ['host', 'guest', 'none'].includes(detail.role)) observeCollaboration(detail as CollaborationStatus);
      throw new CollaborationError(typeof detail === 'string' ? detail : detail?.conflict?.reason || detail?.error || `协作请求失败 (${response.status})`, response.status, detail);
    }
    if (!['host', 'guest', 'none'].includes(value.role) || !Number.isInteger(value.revision)) throw new CollaborationError('协作状态返回无效');
    return emit ? observeCollaboration(value as CollaborationStatus) : value as CollaborationStatus;
  } finally {clearTimeout(timer);}
}
export function collaborationSnapshot(): Promise<CollaborationStatus> {
  if (!snapshotRequest) {const epoch = contextEpoch; snapshotRequest = request('/snapshot', undefined, false).then(value => epoch === contextEpoch && !contextChanging ? observeCollaboration(value) : latest || value).finally(() => {snapshotRequest = null;});}
  return snapshotRequest;
}
function serialize<T>(operation: () => Promise<T>): Promise<T> {
  const job = commandTail.catch(() => {}).then(operation); commandTail = job.then(() => {}, () => {}); return job;
}
export function collaborationAction(action: 'host' | 'stop' | 'join' | 'disconnect' | 'reconnect', body: Row = {}) {return serialize(async () => {contextChanging = true; contextEpoch++; try {return await request('/' + action, body);} finally {contextChanging = false; contextEpoch++; snapshotRequest = null;}});}

async function openDraftDatabase(): Promise<IDBDatabase> {
  // Local mode has one workspace; a full page reload creates new module state.
  return new Promise((resolve, reject) => {
    if (!globalThis.indexedDB) {reject(new CollaborationError('当前窗口不能持久化协作草稿，请先另存本地项目')); return;}
    const databaseName = 'architecture-canvas-collaboration-drafts';
    const operation = indexedDB.open(databaseName, 1);
    operation.onupgradeneeded = () => {operation.result.createObjectStore('drafts', {keyPath: 'canvasId'});};
    operation.onsuccess = () => resolve(operation.result); operation.onerror = () => reject(operation.error || new Error('协作草稿存储无法打开'));
  });
}
export async function readCanvasDraft(canvasId: string): Promise<CanvasDraft | null> {
  const database = await openDraftDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction('drafts'), operation = transaction.objectStore('drafts').get(canvasId);
    operation.onsuccess = () => {
      const envelope = operation.result;
      if (!envelope) {resolve(null); return;}
      const branch = canvasWindowBranch(), entries = envelope.branches && typeof envelope.branches === 'object' ? envelope.branches : {legacy: {...envelope, branchId: envelope.branchId || 'legacy'}};
      resolve(entries[branch] || Object.values(entries).filter((value: any) => value?.dirty).sort((a: any, b: any) => b.updatedAt - a.updatedAt)[0] || null);
    }; operation.onerror = () => reject(operation.error);
    transaction.oncomplete = () => database.close(); transaction.onabort = () => {database.close(); reject(transaction.error);};
  });
}
export async function writeCanvasDraft(draft: CanvasDraft): Promise<void> {
  const database = await openDraftDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction('drafts', 'readwrite'), store = transaction.objectStore('drafts'), existing = store.get(draft.canvasId);
    existing.onsuccess = () => {
      const envelope = existing.result, branch = canvasWindowBranch();
      const entries: Record<string, CanvasDraft> = envelope?.branches && typeof envelope.branches === 'object' ? {...envelope.branches} : envelope ? {legacy: {...envelope, branchId: envelope.branchId || 'legacy'}} : {};
      const previous = entries[branch];
      // Window-local counters only order writes within the same branch. Keep
      // every other window's complete draft instead of silently skipping it.
      if (previous && previous.localRevision > draft.localRevision) return;
      const value = {...draft, branchId: branch, ...(draft.changeId === undefined && previous ? {changeId: previous.changeId, submittedRevision: previous.submittedRevision} : {})};
      entries[branch] = value; store.put({...value, branches: entries});
    };
    transaction.oncomplete = () => {database.close(); resolve();}; transaction.onerror = () => {database.close(); reject(transaction.error || new Error('协作草稿保存失败'));}; transaction.onabort = () => {database.close(); reject(transaction.error || new Error('协作草稿保存失败'));};
  });
}
function assertBinding(binding: CollaborationBinding, state: CollaborationStatus) {
  if (state.canvas_id !== binding.canvasId || state.peer_id !== binding.peerId || state.role !== binding.role) throw new CollaborationError('协作对象已变化，原画布未提交', 409, state);
  if (!state.connected) throw new CollaborationError('当前连接已断开，内容已保留本机草稿；重连后发布或另存本地副本', 409, state);
}
async function waitForAcknowledgement(binding: CollaborationBinding, changeId: string): Promise<CollaborationStatus> {
  const deadline = Date.now() + 15000;
  do {
    const state = await collaborationSnapshot(); assertBinding(binding, state);
    if (state.conflict?.change_id === changeId) throw new CollaborationError(state.conflict.reason || '发布冲突，内容已保留副本', 409, state);
    if (!state.pending && state.last_ack_change_id === changeId && Number.isInteger(state.last_ack_revision)) {
      binding.baseRevision = state.last_ack_revision!; binding.pendingId = null; return state;
    }
    await new Promise(resolve => window.setTimeout(resolve, 250));
  } while (Date.now() < deadline);
  throw new CollaborationError('主机尚未确认，内容已保留本机草稿；请等待或重连', 408, latest);
}
export async function publishCanvas(id: string, document: Row, localRevision: number): Promise<void> {
  const binding = bindings.get(id); if (!binding) throw new CollaborationError('协作绑定不存在，未提交');
  await writeCanvasDraft({canvasId: id, document, localRevision, baseRevision: binding.baseRevision, peerId: binding.peerId, dirty: true, updatedAt: Date.now()});
  return serialize(async () => {
    binding.publishing = true;
    const changeId = crypto.randomUUID();
    try {
      if (binding.pendingId) await waitForAcknowledgement(binding, binding.pendingId);
      const state = await collaborationSnapshot(); assertBinding(binding, state);
      await writeCanvasDraft({canvasId: id, document, localRevision, baseRevision: binding.baseRevision, peerId: binding.peerId, changeId, submittedRevision: localRevision, dirty: true, updatedAt: Date.now()});
      // Arm correlation before POST: the server may accept a request even when
      // its HTTP response is lost. A later authoritative ACK can still settle it.
      binding.pendingId = changeId; binding.pendingLocalRevision = localRevision;
      let response: CollaborationStatus;
      try {response = await request('/publish', {base_revision: binding.baseRevision, document, change_id: changeId, canvas_id: id, peer_id: binding.peerId});}
      catch (error) {if (error instanceof CollaborationError && error.status >= 400 && error.status < 500) {binding.pendingId = null; binding.pendingLocalRevision = null;} throw error;}
      assertBinding(binding, response);
      if (Number.isInteger(response.accepted_revision) && !response.pending) binding.baseRevision = response.accepted_revision!;
      else {binding.pendingId = response.pending_change_id || changeId; binding.pendingLocalRevision = localRevision; await waitForAcknowledgement(binding, changeId);}
      binding.pendingId = null; binding.pendingLocalRevision = null;
      await writeCanvasDraft({canvasId: id, document, localRevision, baseRevision: binding.baseRevision, peerId: binding.peerId, changeId, submittedRevision: localRevision, dirty: false, updatedAt: Date.now()});
    } finally {binding.publishing = false;}
  });
}

const ELEMENT_KEYS = new Set(['id', 'type', 'x', 'y', 'w', 'h', 'color', 'text', 'href', 'points', 'groupId', 'fontFamily', 'fontSize', 'fontWeight', 'fontStyle']);
const GROUP_KEYS = new Set(['id', 'groupId', 'GroupId', 'name', 'GroupName', 'fontSize', 'FontSize', 'fontFamily', 'fontWeight', 'fontStyle', 'color']);
const extra = (value: Row, keys: Set<string>): Row => Object.fromEntries(Object.entries(value).filter(([key]) => !keys.has(key)));
const groupId = (value: Row) => typeof (value.GroupId ?? value.groupId ?? value.id) === 'string' ? value.GroupId ?? value.groupId ?? value.id : '';
export function mergeSharedDocument(local: Row, remote: Row): Row {
  const oldElements = new Map((local.elements || []).map((element: Row) => [element.id, element]));
  const oldGroups = Array.isArray(local.meta?.groups) ? local.meta.groups : [];
  const groupMap = new Map(oldGroups.filter((value: unknown) => value && typeof value === 'object' && !Array.isArray(value)).map((value: Row) => [groupId(value), value]));
  const groups = (remote.meta?.groups || []).map((value: Row) => ({...extra((groupMap.get(groupId(value)) || {}) as Row, GROUP_KEYS), ...value}));
  const unknownGroups = oldGroups.filter((value: unknown) => !value || typeof value !== 'object' || Array.isArray(value) || !groupId(value as Row));
  return {...local, ...remote, elements: (remote.elements || []).map((element: Row) => ({...extra((oldElements.get(element.id) || {}) as Row, ELEMENT_KEYS), ...element})), viewport: {...extra(local.viewport || {}, new Set(['x', 'y', 'zoom', 'zoomY'])), ...remote.viewport}, meta: {...local.meta, ...remote.meta, groups: [...groups, ...unknownGroups]}};
}
