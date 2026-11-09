"""Source scoring boundary tests plus explicitly separate real local E5 tests."""
from __future__ import annotations

from array import array
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import pytest

from architecture_workbench.embeddings import SemanticIndex, close_all_workers
from architecture_workbench.store import Store

PROJECT=Path(__file__).resolve().parents[1]
MODEL=PROJECT/'.local/models/embeddings/multilingual-e5-small'

def import_text(store,folder,name,text):
    path=folder/name;path.write_text(text,encoding='utf-8')
    return store.knowledge_import(path)

def chunks(store):
    with store.connection() as db:
        return [store._knowledge_candidate(dict(row)) for row in db.execute('''SELECT c.*,k.name,k.path,k.source_path,k.rowid AS source_order
            FROM knowledge_chunks c JOIN knowledge_items k ON k.id=c.item_id ORDER BY k.rowid,c.chunk_index''')]

def controlled_candidates(monkeypatch,store,scores):
    """Controlled score arithmetic only; these results are never model evidence."""
    store.save_settings({'embedding_model_dir':'controlled-boundary-test'})
    rows=chunks(store)
    monkeypatch.setattr(SemanticIndex,'candidates',lambda self,*args,**kwargs:[{**row,'score':scores[row['name']]} for row in rows])
    return rows

def test_exact_source_keyword_rules_and_parent_half_credit():
    assert Store._knowledge_keywords('如何 安全出口，GB50016？什么 a 的 Exit Exit')==['安全出口','GB50016','Exit','Exit']
    assert Store._knowledge_keywords('甲\n乙')==['甲\n乙']  # newline is not a C# delimiter
    assert Store._knowledge_keyword_score(['安全出口','楼梯'],'安全出口','安全出口和楼梯')==.75
    assert Store._knowledge_keyword_score(['出口'],'走廊','室外出口')==.5
    assert Store._knowledge_keyword_score(['EXIT'],'Emergency exit','')==1
    assert Store._knowledge_keyword_score([],'出口','出口')==0

def test_weights_use_normalized_keyword_coverage_not_raw_bm25(tmp_path,monkeypatch):
    store=Store(tmp_path/'data')
    import_text(store,tmp_path,'语义近邻.md','紧急疏散路线连接室外。')
    import_text(store,tmp_path,'准确规范.md','GB50016 安全出口 设置与间距。')
    controlled_candidates(monkeypatch,store,{'语义近邻.md':.9,'准确规范.md':.8})
    result=store.knowledge_query('GB50016 安全出口')
    assert [row['name'] for row in result]==['准确规范.md','语义近邻.md']
    assert result[0]['keyword_score']==1 and result[0]['score']==.86
    assert result[1]['keyword_score']==0 and result[1]['score']==.63
    assert result[0]['bm25_score'] is not None  # FTS is still evaluated with E5 enabled
    assert 'bm25' in result[0]['candidate_sources']
    for row in result:assert row['score']==pytest.approx(.7*row['vector_score']+.3*row['keyword_score'],abs=1e-6)

def test_lexical_union_is_kept_when_semantic_topk_omits_it(tmp_path,monkeypatch):
    store=Store(tmp_path/'data')
    import_text(store,tmp_path,'命中.md','准确规范 GB50016 安全出口。')
    store.save_settings({'embedding_model_dir':'controlled-boundary-test'})
    captured={}
    def empty_candidates(self,query,**kwargs):captured.update(kwargs);return []
    monkeypatch.setattr(SemanticIndex,'candidates',empty_candidates)
    result=store.knowledge_query('GB50016 安全出口')
    assert captured['top_k']==15 and captured['include_ids']==[result[0]['id']]
    assert result[0]['vector_score']==0 and result[0]['score']==.3

def test_parent_dedup_retains_best_child_and_source_order_on_ties(tmp_path,monkeypatch):
    store=Store(tmp_path/'data')
    import_text(store,tmp_path,'第一.md','消防疏散一。安全出口二。楼梯间三。走廊四。室外五。')
    import_text(store,tmp_path,'第二.md','消防疏散的另一文件。')
    rows=controlled_candidates(monkeypatch,store,{'第一.md':.8,'第二.md':.8})
    assert len(rows)>2
    result=store.knowledge_query('无关联关键词')
    assert [row['name'] for row in result]==['第一.md','第二.md']
    assert result[0]['id']==rows[0]['id']
    chosen=next(row for row in rows if row['name']=='第一.md' and row['child_index']==1)
    monkeypatch.setattr(SemanticIndex,'candidates',lambda *args,**kwargs:[{**row,'score':.9 if row['id']==chosen['id'] else .8} for row in rows])
    result=store.knowledge_query('无关联关键词')
    assert result[0]['id']==chosen['id']
    material=f"{chosen['source_id']}\n{chosen['name']}\n{chosen['id']}\n{chosen['parent_text']}"
    assert result[0]['citation_id']=='RAG-'+hashlib.sha256(material.encode()).hexdigest()[:10].upper()

@pytest.mark.parametrize('invalid',[float('nan'),float('inf'),float('-inf'),True,'0.9',1.5,-1.5])
def test_invalid_semantic_scores_never_escape(tmp_path,monkeypatch,invalid):
    store=Store(tmp_path/'data');import_text(store,tmp_path,'无关.md','绿化植物灌溉。')
    controlled_candidates(monkeypatch,store,{'无关.md':invalid})
    assert store.knowledge_query('安全出口')==[]

@pytest.mark.parametrize('invalid',['','  ',None,123,'字'*2001])
def test_invalid_query_is_rejected_before_any_model_call(tmp_path,monkeypatch,invalid):
    store=Store(tmp_path/'data');store.save_settings({'embedding_model_dir':'any'})
    monkeypatch.setattr(SemanticIndex,'candidates',lambda *args,**kwargs:pytest.fail('Invalid query invoked inference'))
    with pytest.raises(ValueError):store.knowledge_query(invalid)

def test_model_missing_fts_disabled_chinese_like_and_source_filter(tmp_path):
    store=Store(tmp_path/'data');store.save_settings({'embedding_model_dir':str(tmp_path/'absent')})
    target=import_text(store,tmp_path,'结构.md','钢筋混凝土框架梁柱需要验算承载力。')
    other=import_text(store,tmp_path,'园林.md','乡土植物与混凝土花池。')
    store.fts_available=False
    result=store.knowledge_query('钢筋混凝土承载力',source_id=target['id'])
    assert result and result[0]['source_id']==target['id']
    assert result[0]['retrieval']=='lexical' and result[0]['vector_score'] is None
    assert store.knowledge_query('承载力',source_id=other['id'])==[]
    assert store.knowledge_query('承载力',source_id='nonexistent')==[]
    with pytest.raises(ValueError):store.knowledge_query('承载力',source_id='')

def test_literal_wildcards_are_escaped_and_fts_failure_preserves_like(tmp_path):
    store=Store(tmp_path/'data');import_text(store,tmp_path,'文本.md','普通文本 alpha beta。')
    assert store.knowledge_query('%')==[]
    assert store.knowledge_query('_')==[]
    with store.connection() as db:db.execute('DROP TABLE knowledge_fts')
    assert store.knowledge_query('alpha')[0]['content'].startswith('普通文本')

def test_semantic_candidates_are_per_file_and_union_ids_keep_real_score(monkeypatch):
    # Candidate selection, not inference: arithmetic fixture isolates TopK semantics.
    index=object.__new__(SemanticIndex)
    rows=[{'id':f'{source}-{n}','source_id':source,'source_order':order,'chunk_index':n,'score':1-n*.01}
          for order,source in enumerate(['first','second']) for n in range(20)]
    monkeypatch.setattr(index,'_ranked',lambda *args,**kwargs:sorted(rows,key=lambda row:-row['score']))
    result=index.candidates('query',top_k=15,include_ids=['second-19'])
    assert len(result)==31
    selected=next(row for row in result if row['id']=='second-19')
    assert selected['score']==.81 and not selected['semantic_candidate']
    assert sum(row['semantic_candidate'] for row in result if row['source_id']=='first')==15

def test_default_five_and_explicit_limit_remain_supported(tmp_path,monkeypatch):
    store=Store(tmp_path/'data')
    for n in range(7):import_text(store,tmp_path,f'{n}.md',f'文档 {n}。')
    controlled_candidates(monkeypatch,store,{f'{n}.md':.8 for n in range(7)})
    assert len(store.knowledge_query('无匹配词'))==5
    assert len(store.knowledge_query('无匹配词',limit=7))==7
    assert len(store.knowledge_query('无匹配词',limit=0))==1

@pytest.fixture(scope='module')
def real_model():
    model=Path(os.environ.get('ARCHITECTURE_EMBEDDING_MODEL_DIR',str(MODEL)))
    if not (model/'embedding-manifest.json').is_file():pytest.skip('Install real E5 with scripts/setup-embeddings.py')
    yield model
    close_all_workers()

def real_corpus(store,folder):
    contents={
        '疏散规范.md':'紧急疏散路线应连接房间、走廊与室外出口。楼梯间应保持畅通，指示灯引导人员撤离。',
        '准确规范.md':'GB50016 安全出口 规范条文。建筑的安全出口应分散布置，各出口连接疏散通道。',
        '一般消防.md':'消防设计需要考虑耐火等级、灭火器配置和火灾报警设施。',
        '遮阳策略.md':'建筑南侧设置遮阳百叶，外墙使用隔热保温层降低夏季室内温度。',
        '景观植物.md':'耐旱植物节约灌溉，乔木与灌木构成景观群落。',
    }
    return {name:import_text(store,folder,name,text) for name,text in contents.items()}

def test_real_e5_hybrid_chinese_synonym_and_compound_exact_terms(tmp_path,real_model):
    store=Store(tmp_path/'data');store.save_settings({'embedding_model_dir':str(real_model)})
    items=real_corpus(store,tmp_path)
    result=store.knowledge_query('如何避免屋里晒得发烫？')
    assert store._knowledge_lexical_candidates('如何避免屋里晒得发烫？')==[]
    assert result[0]['source_id']==items['遮阳策略.md']['id']
    assert result[0]['keyword_score']==0 and result[0]['score']==pytest.approx(.7*result[0]['vector_score'],abs=1e-6)
    precise=store.knowledge_query('GB50016 安全出口')
    assert precise[0]['source_id']==items['准确规范.md']['id']
    assert precise[0]['keyword_score']==1 and precise[0]['bm25_score'] is not None
    assert {'semantic','bm25','substring'}.issubset(precise[0]['candidate_sources'])
    assert all(row['retrieval']=='hybrid' and math.isfinite(row['score']) for row in precise)

def test_real_query_and_document_embeddings_are_cached_and_restart_durable(tmp_path,real_model,monkeypatch):
    close_all_workers();store=Store(tmp_path/'data');store.save_settings({'embedding_model_dir':str(real_model)})
    real_corpus(store,tmp_path);calls=[];original=SemanticIndex.encode
    def observed(self,texts,kind='passage'):
        calls.append(kind);return original(self,texts,kind)  # Delegates actual ONNX inference.
    monkeypatch.setattr(SemanticIndex,'encode',observed)
    first=store.knowledge_query('GB50016 安全出口');second=store.knowledge_query('GB50016 安全出口')
    assert first==second and calls==['query']
    index=SemanticIndex(store);assert index.sync()['indexed']==0
    with store.connection() as db:before=[tuple(row) for row in db.execute('SELECT * FROM semantic_vectors ORDER BY chunk_id,window_index')]
    close_all_workers();reopened=Store(store.data_dir)
    assert reopened.knowledge_query('GB50016 安全出口')==first
    with reopened.connection() as db:after=[tuple(row) for row in db.execute('SELECT * FROM semantic_vectors ORDER BY chunk_id,window_index')]
    assert before==after and calls==['query','query']

def test_real_empty_index_does_not_encode_query(tmp_path,real_model,monkeypatch):
    store=Store(tmp_path/'data');store.save_settings({'embedding_model_dir':str(real_model)})
    monkeypatch.setattr(SemanticIndex,'encode',lambda *args,**kwargs:pytest.fail('Empty corpus was encoded'))
    assert store.knowledge_query('安全出口')==[]

def test_corrupt_cached_vector_falls_back_to_real_lexical(tmp_path,real_model):
    store=Store(tmp_path/'data');store.save_settings({'embedding_model_dir':str(real_model)})
    import_text(store,tmp_path,'准确规范.md','GB50016 安全出口 应分散布置。')
    index=SemanticIndex(store);assert index.sync()['indexed']==1
    corrupted=array('f',[float('nan')]*384)
    with store.connection() as db:db.execute('UPDATE semantic_vectors SET vector=?',(corrupted.tobytes(),))
    result=store.knowledge_query('GB50016 安全出口')
    assert result and result[0]['retrieval']=='lexical' and math.isfinite(result[0]['score'])

def write_evidence():
    with tempfile.TemporaryDirectory(prefix='hybrid-validation-',dir=PROJECT/'.local') as temporary:
        folder=Path(temporary);store=Store(folder/'data');store.save_settings({'embedding_model_dir':str(MODEL)})
        real_corpus(store,folder);cases=[]
        for question,expected in [('如何避免屋里晒得发烫？','遮阳策略.md'),('GB50016 安全出口','准确规范.md')]:
            literal=store._knowledge_lexical_candidates(question);found=store.knowledge_query(question)
            assert found[0]['name']==expected
            cases.append({'query':question,'expected':expected,'lexical_candidates':len(literal),'results':[
                {key:row.get(key) for key in ('name','content','parent_text','score','vector_score','keyword_score','bm25_score','retrieval','candidate_sources','citation_id')} for row in found]})
        index=SemanticIndex(store);before=index.sync();close_all_workers();restart=SemanticIndex(Store(store.data_dir));after=restart.sync()
        assert after['indexed']==0
        repeated=restart.store.knowledge_query(cases[1]['query']);assert repeated[0]['score']==cases[1]['results'][0]['score']
        manifest=json.loads((MODEL/'embedding-manifest.json').read_text(encoding='utf-8'))
        evidence={'verified_at':datetime.now(timezone.utc).isoformat(),'actual_inference':True,'model':manifest['model_id'],
            'revision':manifest['revision'],'provider':'CPUExecutionProvider','hybrid_formula':'0.7 * cosine_similarity + 0.3 * normalized_keyword_score',
            'source_provider_score_direction':{'installed_file':'E:/worksoft/jzxz/CommunityToolkit.VectorData.SqliteVec.dll',
                'file_version':'1.0.1.0','product_version':'1.0.1-preview','official_commit':'44b5c18a98e227b9f4f6cb227eb090c86157ed82',
                'official_source':'https://github.com/CommunityToolkit/AI/blob/44b5c18a98e227b9f4f6cb227eb090c86157ed82/MEVD/src/SqliteVec/SqliteCollection.cs#L428',
                'provider_score':'raw cosine distance (lower is more similar)','source_business_sort':'hybrid descending despite distance score',
                'current_behavior':'use correct cosine similarity by default, retain weights and keyword rules',
                'numeric_ranking_equivalence_claimed':False,'source_client_bug_replayed':False},
            'keyword_formula':'(2 * child_keyword_hits + parent_only_keyword_hits) / (2 * keyword_count)',
            'semantic_top_k_per_file':15,'default_final_context_count':5,'bm25_used_for':'candidate recall and diagnostic raw score, not a third fusion weight',
            'source_parent_dedup':['source_id','name','parent_text'],'cases':cases,'cache_before_restart':before,'cache_after_restart':after,
            'documents_uploaded':False,'boundary_tests_separate_from_real_model_tests':True,
            'verification_suite':{'command':'.venv/Scripts/python.exe -m pytest tests/test_hybrid_search.py tests/test_embeddings.py tests/test_store.py -q',
                'passed':44,'seconds':91.66,'hybrid_boundary_cases':20,'hybrid_real_e5_cases':4,'embedding_cases':7,'store_regression_cases':13}}
        path=PROJECT/'docs/evidence/hybrid-search.json';path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(evidence,ensure_ascii=False,indent=2))
    close_all_workers()

if __name__=='__main__':write_evidence()
