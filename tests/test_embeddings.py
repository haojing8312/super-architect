"""Real ONNX E5 inference and durable vector-index tests; never mocked vectors."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import tempfile
import subprocess
from datetime import datetime,timezone

import pytest

from architecture_workbench.documents import query_terms
from architecture_workbench.embeddings import SemanticIndex,close_all_workers
from architecture_workbench.store import Store

PROJECT=Path(__file__).resolve().parents[1]
DEFAULT_MODEL=PROJECT/'.local'/'models'/'embeddings'/'multilingual-e5-small'

@pytest.fixture(scope='module')
def model_dir():
    path=Path(os.environ.get('ARCHITECTURE_EMBEDDING_MODEL_DIR',str(DEFAULT_MODEL)))
    if not (path/'embedding-manifest.json').is_file():pytest.skip('Run scripts/setup-embeddings.py to install the real local ONNX model')
    yield path
    close_all_workers()

def configured_store(path,model_dir):
    store=Store(path);store.save_settings({'embedding_model_dir':str(model_dir)});return store

def lexical_matches(store,query):
    # Inspect the previous literal retrieval criteria directly, so this test
    # remains valid when the parent integrates SemanticIndex into Store.query.
    terms=query_terms(query)
    with store.connection() as db:
        return [dict(row) for row in db.execute('SELECT * FROM knowledge_chunks') if any(term in row['content'].lower() for term in terms)]

def populate(store,folder):
    documents={
        '疏散规范.md':'紧急疏散路线应连接房间、走廊与室外出口。楼梯间应保持畅通，指示灯引导人员撤离。',
        '遮阳策略.md':'建筑南侧设置遮阳百叶，外墙使用隔热保温层降低夏季室内温度。',
        '植物种植.md':'绿化采用耐旱乡土植物，节约灌溉用水，乔木与灌木构成景观群落。',
        '财务记录.md':'员工每月领取薪资，报销凭据交由会计登记，预算按季度汇总。',
    }
    result={}
    for name,content in documents.items():
        path=folder/name;path.write_text(content,encoding='utf-8');result[name]=store.knowledge_import(path)
    return result

def test_missing_model_and_empty_configuration_preserve_fallback(tmp_path):
    store=Store(tmp_path/'data')
    assert SemanticIndex(store).query('紧急逃生') is None
    store.save_settings({'embedding_model_dir':str(tmp_path/'missing-model')})
    index=SemanticIndex(store)
    assert not index.available and index.query('紧急逃生') is None
    assert index.last_error

def test_real_chinese_synonyms_retrieve_sources_without_literal_overlap(tmp_path,model_dir):
    store=configured_store(tmp_path/'data',model_dir);items=populate(store,tmp_path)
    index=SemanticIndex(store)
    cases=[('着火了人往哪里逃？','疏散规范.md'),('如何避免屋里晒得发烫？','遮阳策略.md')]
    for question,name in cases:
        assert lexical_matches(store,question)==[]
        found=index.query(question,limit=3)
        assert found is not None,index.last_error
        assert found[0]['source_id']==items[name]['id'],found
        assert found[0]['retrieval']=='semantic'
        assert {'source_id','name','source_path','path','chunk_index','content','score'}.issubset(found[0])
        assert 0<found[0]['score']<=1

def test_incremental_index_survives_worker_and_store_restart(tmp_path,model_dir):
    store=configured_store(tmp_path/'data',model_dir);populate(store,tmp_path)
    first=SemanticIndex(store)
    assert first.sync()['indexed']==4
    with store.connection() as db:
        original=[tuple(row) for row in db.execute('SELECT chunk_id,window_index,vector,created_at FROM semantic_vectors ORDER BY chunk_id,window_index')]
    close_all_workers()
    reopened=SemanticIndex(Store(store.data_dir))
    sync=reopened.sync()
    assert sync['indexed']==0 and sync['reused']==4
    assert reopened.query('着火了人往哪里逃？')[0]['name']=='疏散规范.md'
    with store.connection() as db:
        restored=[tuple(row) for row in db.execute('SELECT chunk_id,window_index,vector,created_at FROM semantic_vectors ORDER BY chunk_id,window_index')]
    assert restored==original

def test_deleted_knowledge_cascades_vector_rows(tmp_path,model_dir):
    store=configured_store(tmp_path/'data',model_dir);items=populate(store,tmp_path);index=SemanticIndex(store)
    assert index.sync()['indexed']==4
    selected=items['财务记录.md']['id']
    with store.connection() as db:
        chunks=[row['id'] for row in db.execute('SELECT id FROM knowledge_chunks WHERE item_id=?',(selected,))]
    store.delete_knowledge(selected)
    with store.connection() as db:
        assert not any(db.execute('SELECT 1 FROM semantic_vectors WHERE chunk_id=?',(chunk,)).fetchone() for chunk in chunks)
    assert index.sync()['indexed']==0

def test_real_vectors_have_unit_norm_and_padding_is_excluded(tmp_path,model_dir):
    store=configured_store(tmp_path/'data',model_dir);index=SemanticIndex(store)
    short='夏季建筑外立面的遮阳与隔热策略。'
    long='楼梯间、走廊和室外出口构成建筑消防疏散路线。'*120
    single=index.encode([short])[0];batch=index.encode([short,long])
    assert len(single)==384 and all(math.isfinite(value) for value in single)
    assert math.sqrt(sum(value*value for value in single))==pytest.approx(1,abs=1e-6)
    assert sum(left*right for left,right in zip(single,batch[0]))>0.9999
    assert math.sqrt(sum(value*value for value in batch[1]))==pytest.approx(1,abs=1e-6)
    response=index._encoder().encode([long],'passage')
    assert len(response['groups'][0])>1
    assert all(0<length<=512 for length in response['token_lengths'][0])

def test_source_child_chunks_are_indexed_without_reencoding(tmp_path,model_dir):
    store=configured_store(tmp_path/'data',model_dir)
    document=tmp_path/'长篇设计说明.md'
    document.write_text('钢筋混凝土框架柱承担竖向荷载，基础把作用传给地基。'*100,encoding='utf-8')
    imported=store.knowledge_import(document);index=SemanticIndex(store);result=index.sync()
    assert result is not None,index.last_error
    # Source-compatible sentence children now hardwrap at 400 UTF16 units.
    # Their individual embeddings need one window for this Chinese fixture;
    # the direct encoder test above independently exercises long-text windows.
    assert result['indexed']==imported['chunk_count']
    assert result['new_windows']==imported['chunk_count']
    with store.connection() as db:
        assert all(len(row['content'].encode('utf-16-le'))//2<=400 for row in db.execute('SELECT content FROM knowledge_chunks'))
    assert index.sync()['indexed']==0

def test_long_english_window_keeps_the_first_content_token(model_dir):
    manifest=json.loads((model_dir/'embedding-manifest.json').read_text(encoding='utf-8'))
    program="""
import importlib.util,sys
from pathlib import Path
folder=Path(sys.argv[1]);spec=importlib.util.spec_from_file_location('local_embeddings',folder/'embedding_worker.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
encoder=module.LocalOnnxEncoder(folder)
text='evacuation '+('architecture building design ' * 1000)
full=encoder.tokenizer.encode('passage: '+text,add_special_tokens=True).ids
windows=encoder.windows(text,'passage')
assert len(windows)>1
assert windows[0] == full[:511]+[full[-1]], 'First English content token was dropped at the prefix boundary'
print(len(windows),len(windows[0]))
"""
    result=subprocess.run([manifest['python'],'-c',program,str(model_dir)],capture_output=True,timeout=60)
    assert result.returncode==0,result.stderr.decode('utf-8',errors='replace')

def write_real_evidence():
    model=DEFAULT_MODEL
    with tempfile.TemporaryDirectory(prefix='semantic-validation-',dir=PROJECT/'.local') as temporary:
        folder=Path(temporary);store=configured_store(folder/'data',model);items=populate(store,folder);index=SemanticIndex(store)
        cases=[]
        for question,expected in [('着火了人往哪里逃？','疏散规范.md'),('如何避免屋里晒得发烫？','遮阳策略.md')]:
            literal=lexical_matches(store,question);semantic=index.query(question,3)
            assert semantic is not None,index.last_error
            assert not literal and semantic[0]['name']==expected
            cases.append({'query':question,'expected_source':expected,'literal_matches':len(literal),
                'semantic_results':[{'name':row['name'],'score':row['score'],'content':row['content']} for row in semantic]})
        first_sync=dict(index.last_sync)
        close_all_workers();restarted=SemanticIndex(Store(store.data_dir));restart_sync=restarted.sync()
        assert restart_sync['indexed']==0
        assert restarted.query(cases[0]['query'])[0]['name']==cases[0]['expected_source']
        vectors=restarted.encode(['建筑遮阳百叶和隔热保温材料'],'passage')[0]
        norm=math.sqrt(sum(value*value for value in vectors))
        manifest=json.loads((model/'embedding-manifest.json').read_text(encoding='utf-8'))
        missing=Store(folder/'fallback');missing.save_settings({'embedding_model_dir':str(folder/'no-model')})
        assert SemanticIndex(missing).query('建筑疏散') is None
        evidence={'verified_at':datetime.now(timezone.utc).isoformat(),'model_id':manifest['model_id'],'revision':manifest['revision'],
            'dimensions':len(vectors),'vector_norm':norm,'max_tokens':512,'pooling':'attention-mask mean','normalization':'L2',
            'provider':'CPUExecutionProvider','prefixes':{'query':'query: ','passage':'passage: '},'versions':manifest['versions'],
            'artifact_hashes':manifest['files'],'semantic_cases':cases,'restart_sync':restart_sync,'cache_reused_after_worker_restart':True,
            'missing_model_fallback':True,'real_onnx_inference_verified':True,'documents_uploaded':False,'model_dir':str(model),
            'source_differences':['CPU instead of optional DirectML','long chunks use overlapping token windows rather than dropping content after token 512']}
        path=PROJECT/'docs'/'evidence'/'semantic-search.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'verified':True,'cases':cases,'dimensions':len(vectors),'norm':norm,'restart_sync':restart_sync},ensure_ascii=False,indent=2))
    close_all_workers()

if __name__=='__main__':write_real_evidence()
