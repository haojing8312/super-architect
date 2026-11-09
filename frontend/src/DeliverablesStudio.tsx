import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, Download, FileText, Image, Plus, Presentation, Save, Search, Trash2, X } from 'lucide-react';
import { api, fileUrl, openPath, pickPath, Row, upload } from './api';
import './deliverables.css';

export type DeliverableDraft = Row & { title?: string; sections?: Row[] };
type Props = { contextId: string; initialDraft?: DeliverableDraft | null; onError: (message: string) => void; toast: (message: string) => void; onAgent?: (message: string) => Promise<void> };
const formatNames: Record<string, string> = { html: '离线网页', pptx: '可编辑 PPTX', docx: 'Word 文档', xlsx: 'Excel 资料表', json: '编辑配置' };
const blankSection = (): Row => ({ id: crypto.randomUUID(), title: '新章节', content: '', notes: '', assets: [], sources: [], layout: 'split' });
function draft(value?: Row | null): Row {
  return { title: '', subtitle: '', theme: 'minimal', mode: 'slides', transitions: ['slide', 'reveal', 'zoom'], formats: ['html', 'pptx', 'docx', 'xlsx', 'json'], ...value, sections: value?.sections?.length ? value.sections.map((s: Row) => ({ ...blankSection(), ...s })) : [{ ...blankSection(), title: '项目概况' }] };
}
async function previews(items: Row[]): Promise<Row[]> {
  return Promise.all(items.map(async item => ({ ...item, preview_url: item.type?.startsWith('image/') && !item.name?.toLowerCase().endsWith('.svg') ? await fileUrl(item.url) : '' })));
}

export default function DeliverablesStudio({ contextId, initialDraft, onError, toast, onAgent }: Props) {
  const [project, setProject] = useState<Row>(() => draft(initialDraft)), [selected, setSelected] = useState(0), [resources, setResources] = useState<Row[]>([]), [history, setHistory] = useState<Row[]>([]), [result, setResult] = useState<Row | null>(null), [preview, setPreview] = useState(''), [busy, setBusy] = useState(false), [ready, setReady] = useState(false), [saving, setSaving] = useState(''), [query, setQuery] = useState(''), [citations, setCitations] = useState<Row[]>([]), [searching, setSearching] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null), configInput = useRef<HTMLInputElement>(null), alive = useRef(true), initial = useRef(initialDraft), latest = useRef(project);
  latest.current = project;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const initialKey = JSON.stringify(initialDraft || null);
  useEffect(() => { initial.current = initialDraft; }, [initialKey]);
  useEffect(() => {
    let cancelled = false;
    setReady(false); setResult(null); setPreview(''); setSelected(0);
    (async () => {
      const [state, assets, exports] = await Promise.all([api('/canvas/deliverables-draft-' + encodeURIComponent(contextId)), api('/resources'), api('/deliverables')]);
      if (cancelled) return;
      const rows = await previews(assets.items); if (cancelled) return;
      setProject(draft(initial.current || state.deliverable_draft)); setResources(rows); setHistory(exports.items); setReady(true);
    })().catch(e => { if (!cancelled) onError((e as Error).message); });
    return () => { cancelled = true; };
  }, [contextId]);
  useEffect(() => {
    if (ready && initialDraft) { setProject(draft(initialDraft)); setSelected(0); setResult(null); setPreview(''); }
  }, [initialKey]);
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      setSaving('正在保存');
      try { await api('/canvas/deliverables-draft-' + encodeURIComponent(contextId), { elements: [], viewport: {}, deliverable_draft: project }, 'PUT'); if (!cancelled) setSaving('已保存'); }
      catch (e) { if (!cancelled) { setSaving('保存失败'); onError((e as Error).message); } }
    }, 600);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [project, ready, contextId]);

  const section = project.sections[selected] || project.sections[0];
  function patchSection(change: Row) { setProject(p => ({ ...p, sections: p.sections.map((s: Row, i: number) => i === selected ? { ...s, ...change } : s) })); }
  function attach(reference: string) { if (!section.assets.includes(reference)) patchSection({ assets: [...section.assets, reference] }); }
  async function refresh() {
    const [assets, exports] = await Promise.all([api('/resources'), api('/deliverables')]);
    const rows = await previews(assets.items);
    if (alive.current) { setResources(rows); setHistory(exports.items); }
  }
  async function importLocal() {
    const path = await pickPath();
    if (!path) return;
    try {
      const asset = await api('/resources/import', { path });
      if (alive.current) { if (asset.type?.startsWith('image/')) attach(asset.url); else toast('文件已导入资料库；图像章节请选择图片，文字文档可从资料摘录加入。'); await refresh(); }
    } catch (e) { onError((e as Error).message); }
  }
  async function importUpload(file: File) {
    try {
      const item = await upload(file);
      if (!alive.current) return;
      if (!item.type?.startsWith('image/')) { onError('请选择 PNG、JPEG、WebP、GIF 或 BMP 图片'); return; }
      item.preview_url = await fileUrl(item.url);
      setResources(rows => [item, ...rows]); attach(item.url);
    } catch (e) { onError((e as Error).message); }
  }
  async function importConfig(file: File) {
    if (file.size > 2 * 1024 * 1024) { onError('编辑配置超过 2 MB，请拆分章节后导入'); return; }
    try {
      const value = JSON.parse((await file.text()).replace(/^\uFEFF/, ''));
      const valid = await api('/deliverables/validate', value);
      if (alive.current) { setProject(draft(valid.spec)); setSelected(0); setResult(null); setPreview(''); toast('已载入编辑配置，可继续调整章节和导出文档。'); }
    } catch (e) { onError((e as Error).message || '编辑配置无法读取，请使用 JSON 配置文件'); }
  }
  async function generate() {
    setBusy(true);
    try {
      const value = await api('/deliverables', latest.current);
      if (!alive.current) return;
      setResult(value);
      setHistory((await api('/deliverables')).items);
      if (value.formats.html) setPreview(await fileUrl(value.formats.html.url + '&preview=true'));
      toast('作品汇报与所选文档已生成，可以下载或继续编辑。');
    } catch (e) { if (alive.current) onError((e as Error).message); }
    finally { if (alive.current) setBusy(false); }
  }
  async function download(item: Row, fmt: string) {
    const a = document.createElement('a'); a.href = await fileUrl(item.formats[fmt].url); a.download = item.title + '.' + fmt; a.click();
  }
  async function reopen(item: Row) {
    try { const value = await api('/deliverables/' + item.id); if (alive.current) { setProject(draft(value.spec)); setSelected(0); setResult(value); setPreview(''); toast('已载入原配置；重新生成将保留旧版成果。'); } }
    catch (e) { onError((e as Error).message); }
  }
  async function search() {
    if (!query.trim()) return;
    setSearching(true);
    try { const results = await api('/knowledge/query', { query, limit: 12 }); if (alive.current) setCitations(results.items); }
    catch (e) { onError((e as Error).message); }
    finally { if (alive.current) setSearching(false); }
  }
  function addCitation(row: Row) {
    const source = { name: row.name || row.title || '资料摘录', content: row.content || row.snippet || '', source_id: row.source_id || row.id || '', ...(row.chunk_id ? { chunk_id: row.chunk_id } : {}), ...(row.collection_id ? { collection_id: row.collection_id } : {}) };
    patchSection({ sources: [...(section.sources || []), source] }); toast('原文摘录已加入章节引用。');
  }
  function move(delta: number) {
    const target = selected + delta;
    if (target < 0 || target >= project.sections.length) return;
    const items = [...project.sections]; [items[target], items[selected]] = [items[selected], items[target]];
    setProject(p => ({ ...p, sections: items })); setSelected(target);
  }
  const images = resources.filter(r => r.type?.startsWith('image/') && !r.name?.toLowerCase().endsWith('.svg'));
  if (!ready) return <div className="deliverables-loading" role="status">正在载入项目与资料…</div>;
  return <div className="deliverables-studio">
    <aside className="deliverables-sidebar">
      <h2><Presentation size={19} /> 文档与作品汇报</h2>
      <p className="deliverables-intro">组织项目资料，生成独立网页与可编辑文档。</p>
      <label className="field"><span>项目标题</span><input aria-label="项目标题" value={project.title} onChange={e => setProject(p => ({ ...p, title: e.target.value }))} placeholder="填写你的项目名称" /></label>
      <label className="field"><span>副标题 / 项目说明</span><textarea aria-label="项目说明" value={project.subtitle} onChange={e => setProject(p => ({ ...p, subtitle: e.target.value }))} /></label>
      <div className="deliverables-config-grid">
        <label className="field"><span>配色</span><select aria-label="汇报配色" value={project.theme} onChange={e => setProject(p => { const { palette, ...rest } = p; return { ...rest, theme: e.target.value }; })}><option value="minimal">素白 · 灰绿</option><option value="dark">深蓝 · 暖金</option><option value="warm">砂岩 · 陶土</option><option value="blueprint">蓝图 · 石墨</option></select></label>
        <label className="field"><span>网页浏览</span><select aria-label="网页浏览模式" value={project.mode} onChange={e => setProject(p => ({ ...p, mode: e.target.value }))}><option value="slides">逐页演示</option><option value="scroll">长页滚动</option></select></label>
      </div>
      <label className="field"><span>网页动效组合</span><select aria-label="网页动效组合" value={JSON.stringify(project.transitions)} onChange={e => setProject(p => ({ ...p, transitions: JSON.parse(e.target.value) }))}>
        {!['["slide","reveal","zoom"]', '["curtain","flip","focus"]', '["gallery","vertical","light"]', '["none"]'].includes(JSON.stringify(project.transitions)) && <option value={JSON.stringify(project.transitions)}>保留已有动效组合</option>}
        <option value='["slide","reveal","zoom"]'>推进 · 揭示 · 缩放</option><option value='["curtain","flip","focus"]'>幕帘 · 翻页 · 聚焦</option><option value='["gallery","vertical","light"]'>画廊 · 垂直 · 光感</option><option value='["none"]'>静态，无动效</option></select></label>
      <div className="deliverables-section-heading"><strong>章节</strong><button title="添加章节" aria-label="添加章节" onClick={() => { setProject(p => ({ ...p, sections: [...p.sections, blankSection()] })); setSelected(project.sections.length); }}><Plus size={16} /></button></div>
      <div className="deliverables-chapters">{project.sections.map((s: Row, i: number) => <button key={s.id || i} className={selected === i ? 'active' : ''} onClick={() => setSelected(i)}><span>{String(i + 1).padStart(2, '0')}</span>{s.title || '未命名章节'}</button>)}</div>
      <fieldset className="deliverables-formats"><legend>导出格式</legend>{Object.entries(formatNames).map(([fmt, name]) => <label key={fmt}><input type="checkbox" checked={project.formats.includes(fmt)} onChange={e => setProject(p => ({ ...p, formats: e.target.checked ? [...p.formats, fmt] : p.formats.filter((v: string) => v !== fmt) }))} />{name}</label>)}</fieldset>
      <button className="primary-button" disabled={busy || !ready || !project.title.trim() || !project.formats.length} onClick={generate}><Download size={16} />{busy ? '正在生成文档…' : '生成所选文档'}</button>
      {onAgent && <button className="secondary-button" onClick={() => onAgent('请根据以下用户已提供的资料，完善作品汇报章节；保留引用出处，禁止把摘录自动认定为已确认的规范结论。先将结构化配置写入当前工作区 deliverables-config.json，完成后说明改动，使用本机 Agent。\n' + JSON.stringify(latest.current, null, 2)).catch(e => onError(String(e)))}><FileText size={15} />由 AI 完善章节</button>}
      <span className="deliverables-save"><Save size={12} />{saving || '编辑内容自动保存'}</span>
      <button className="text-button" onClick={() => configInput.current?.click()}>导入编辑配置</button>
      <input ref={configInput} type="file" accept=".json" hidden aria-label="导入编辑配置文件" onChange={e => { const file=e.target.files?.[0]; if (file) importConfig(file); e.target.value=''; }} />
    </aside>
    <main className="deliverables-main">
      <div className="deliverables-editor-heading"><div><h1>{section.title || '编辑章节'}</h1><p>图片保留长宽比；PPT中的文字、图形和图片分别可编辑。</p></div><div className="button-row"><button className="text-button" title="章节前移" aria-label="章节前移" disabled={selected === 0} onClick={() => move(-1)}><ArrowUp size={16} /></button><button className="text-button" title="章节后移" aria-label="章节后移" disabled={selected === project.sections.length - 1} onClick={() => move(1)}><ArrowDown size={16} /></button><button className="text-button" title="移除章节" aria-label="移除章节" disabled={project.sections.length === 1} onClick={() => { setProject(p => ({ ...p, sections: p.sections.filter((_: Row, i: number) => i !== selected) })); setSelected(Math.max(0, selected - 1)); }}><Trash2 size={16} /></button></div></div>
      <div className="deliverables-config-grid"><label className="field"><span>章节标题</span><input aria-label="章节标题" value={section.title} onChange={e => patchSection({ title: e.target.value })} /></label><label className="field"><span>章节版式</span><select aria-label="章节版式" value={section.layout} onChange={e => patchSection({ layout: e.target.value })}><option value="split">左右图文</option><option value="gallery">图片画廊</option><option value="text">文字说明</option></select></label></div>
      <label className="field"><span>章节正文</span><textarea className="deliverables-body" aria-label="章节正文" value={section.content} onChange={e => patchSection({ content: e.target.value })} placeholder="填写设计说明、空间策略或项目故事。导出按实际文字分页，不截断正文。" /></label>
      <label className="field"><span>演讲备注（PPT备注页与网页演讲模式）</span><textarea aria-label="演讲备注" value={section.notes} onChange={e => patchSection({ notes: e.target.value })} placeholder="提醒自己讲述的要点" /></label>
      <div className="deliverables-section-heading"><strong><Image size={16} />本章图片 · {section.assets.length}</strong><div className="button-row"><button className="secondary-button" onClick={importLocal}>选择本机文件</button><button className="secondary-button" onClick={() => fileInput.current?.click()}>上传图片</button></div></div>
      <input ref={fileInput} type="file" accept=".png,.jpg,.jpeg,.gif,.webp,.bmp" hidden aria-label="导入章节图片文件" onChange={e => { const f = e.target.files?.[0]; if (f) importUpload(f); e.target.value = ''; }} />
      <div className="deliverables-selected">{section.assets.map((reference: string, i: number) => <div key={reference}><span>{resources.find(r => r.url === reference)?.name || `项目素材 ${i + 1}`}</span><button title="移除图片" aria-label={`移除图片 ${i + 1}`} onClick={() => patchSection({ assets: section.assets.filter((v: string) => v !== reference) })}><X size={13} /></button></div>)}</div>
      <div className="deliverables-library">{images.map(r => <button key={r.id} disabled={section.assets.includes(r.url)} onClick={() => attach(r.url)} title={'加入章节：' + r.name}>{r.preview_url ? <img src={r.preview_url} alt={r.name} /> : <Image size={18} />}<span>{r.name}</span><Plus size={14} /></button>)}{!images.length && <p className="muted small">资料库还没有图片。可导入本机图纸、效果图或从画布带入素材。</p>}</div>
      <details className="deliverables-citations"><summary>资料引用 · {(section.sources || []).length}</summary><p className="muted small">保留原文出处与摘录。适用范围和结论请在章节正文中确认。</p><div className="button-row"><input aria-label="检索资料" value={query} onChange={e => setQuery(e.target.value)} placeholder="在知识库检索" onKeyDown={e => { if (e.key === 'Enter') search(); }} /><button className="secondary-button" disabled={searching} onClick={search}><Search size={14} />{searching ? '检索中' : '检索'}</button></div>{citations.map((c, i) => <div className="deliverables-citation" key={c.chunk_id || i}><strong>{c.name || c.title}</strong><p>{c.content || c.snippet}</p><button className="text-button" onClick={() => addCitation(c)}>加入本章引用</button></div>)}{(section.sources || []).map((s: Row, i: number) => <div className="deliverables-citation" key={i}><strong>{s.name}</strong><p>{s.content}</p><button className="text-button" onClick={() => patchSection({ sources: section.sources.filter((_: Row, n: number) => n !== i) })}>移除引用</button></div>)}</details>
      {result && <div className="deliverables-results"><h2>成果已生成</h2><p>{result.title} · 保留编辑配置，重新生成不会覆盖此版本。</p><div className="button-row">{Object.keys(result.formats).map(fmt => <button key={fmt} className="secondary-button" onClick={() => download(result, fmt)}><Download size={14} />{formatNames[fmt]}</button>)}{result.formats.html && <><button className="secondary-button" onClick={async () => setPreview(await fileUrl(result.formats.html.url + '&preview=true'))}>预览网页</button><button className="secondary-button" onClick={() => openPath(result.formats.html.path)}>打开离线网页</button></>}</div>{preview && <iframe title="离线作品汇报预览" src={preview} sandbox="allow-scripts allow-modals" allow="fullscreen" />}</div>}
      <section className="deliverables-history"><h2>已生成的文档</h2>{!history.length && <p className="muted small">生成后，可以在这里下载文件或继续编辑。</p>}{history.map(item => <div key={item.id}><FileText size={19} /><span><strong>{item.title}</strong><small>{item.created_at?.slice(0, 16).replace('T', ' ')} · {Object.keys(item.formats).map(f => f.toUpperCase()).join(' / ')}</small></span><button className="secondary-button" onClick={() => reopen(item)}>继续编辑</button></div>)}</section>
    </main>
  </div>;
}
