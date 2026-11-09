import { useEffect, useState } from 'react';
import { api, Row } from './api';
import { MessageSquare, Check } from 'lucide-react';
export default function Questions({sessionId,busy,onContinue}:{sessionId:string,busy:boolean,onContinue:(s:string)=>void}){
 const [questions,setQuestions]=useState<Row[]>([]),[other,setOther]=useState(''),[error,setError]=useState(''),[pending,setPending]=useState('');
 useEffect(()=>{let cancelled=false;const load=()=>api(`/sessions/${sessionId}/questions`).then(x=>{if(!cancelled)setQuestions((x.items||[]).filter((q:Row)=>q.status==='pending'))}).catch(()=>{});load();const id=setInterval(load,busy?1000:5000);return()=>{cancelled=true;clearInterval(id)}},[sessionId,busy]);
 useEffect(()=>{if(pending&&!busy){setPending('');onContinue(pending)}},[pending,busy,onContinue]);
 async function answer(q:Row,value:string){try{const result=await api(`/sessions/${sessionId}/questions/${q.id}/answer`,{answer:value});setPending(result.next_message||'');setQuestions(items=>items.filter(x=>x.id!==q.id));setOther('');setError('')}catch(e){setError((e as Error).message)}}
 if(!questions.length)return null;
 return <div className="question-list">{questions.map(q=><article className="question-card" key={q.id}><h3><MessageSquare size={16}/>{q.question||q.title}</h3><div className="question-options">{(q.options||[]).map((option:Row|string,i:number)=>{const label=typeof option==='string'?option:option.label||option.title||option.value;return <button className="secondary-button" key={i} onClick={()=>answer(q,typeof option==='string'?option:String(option.value||option.label||option.title))}><Check size={13}/>{label}</button>})}</div>{q.allow_other!==false&&<div className="input-row"><input placeholder="也可以填写补充说明" value={other} onChange={e=>setOther(e.target.value)}/><button className="primary-button" disabled={!other.trim()} onClick={()=>answer(q,other)}>提交回答</button></div>}</article>)}{error&&<p className="inline-error">{error}</p>}</div>
}
