import { useEffect, useState } from 'react';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { X, Download } from 'lucide-react';
import { api, fileUrl, Row } from './api';

function localReference(href:string){
 if(/^(https?:|data:|blob:|#|\/api\/|\/assets\/)/i.test(href))return false;
 return /^[A-Za-z]:[\\/]|^[.\\/]/.test(href)||/\.(?:png|jpg|jpeg|webp|gif|bmp|pdf|docx|xlsx|pptx|html|htm|txt|md|csv|glb|obj|stl|skp|dxf|dwg)$/i.test(href);
}

export default function MessageMarkdown({text,sessionId}:{text:string,sessionId?:string}){
 const [html,setHtml]=useState(''),[zoom,setZoom]=useState('');
 useEffect(()=>{let cancelled=false;const tokens=marked.lexer(text);const paths=new Set<string>();marked.walkTokens(tokens,token=>{if((token.type==='image'||token.type==='link')&&localReference(token.href))paths.add(token.href)});
 (async()=>{const artifacts=new Map<string,Row>();if(sessionId)await Promise.all(Array.from(paths).map(async path=>{try{const resolved=await api('/artifacts/resolve',{session_id:sessionId,path});artifacts.set(path,{...resolved,url:await fileUrl(resolved.url)})}catch{}}));if(cancelled)return;
 marked.walkTokens(tokens,token=>{if((token.type==='image'||token.type==='link')&&artifacts.has(token.href)){const result=artifacts.get(token.href)!;if(token.type==='image'&&!result.image){Object.assign(token,{type:'link',tokens:[{type:'text',raw:token.text,text:token.text}]})}token.href=result.url}});
 const document=new DOMParser().parseFromString(marked.parser(tokens),'text/html');for(const anchor of document.querySelectorAll('a')){const result=Array.from(artifacts.values()).find(a=>anchor.getAttribute('href')===a.url);if(result){anchor.dataset.workbenchPath=result.path;anchor.title='打开 '+result.name}else if(/^https?:/i.test(anchor.getAttribute('href')||'')){anchor.target='_blank';anchor.rel='noopener noreferrer'}}
 setHtml(DOMPurify.sanitize(document.body.innerHTML));})().catch(()=>{if(!cancelled)setHtml(DOMPurify.sanitize(marked.parse(text,{async:false}) as string))});return()=>{cancelled=true}},[text,sessionId]);
 return <><div className="markdown" onClick={e=>{const element=e.target as HTMLElement;if(element.tagName==='IMG'){e.preventDefault();setZoom((element as HTMLImageElement).src);return}const link=element.closest('a');if(!link)return;if(link.dataset.workbenchPath&&window.pywebview){e.preventDefault();window.pywebview.api.open_path(link.dataset.workbenchPath).catch(()=>{window.pywebview!.api.open_path(link.dataset.workbenchPath!.replace(/[\\/][^\\/]+$/,''))});return}const href=link.getAttribute('href')||'';if(/^https?:/i.test(href)&&new URL(href).origin!==location.origin){e.preventDefault();if(window.pywebview)(window.pywebview.api as any).open_url(href);else window.open(href,'_blank','noopener,noreferrer')}}} dangerouslySetInnerHTML={{__html:html}}/>{zoom&&<div className="image-lightbox" onClick={()=>setZoom('')}><div className="lightbox-controls"><a href={zoom} download onClick={e=>e.stopPropagation()}><Download size={19}/></a><button title="关闭图片预览" onClick={()=>setZoom('')}><X size={22}/></button></div><img src={zoom} alt="图片预览" onClick={e=>e.stopPropagation()}/></div>}</>
}
