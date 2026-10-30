import hashlib
import json
from pathlib import Path
import numpy as np
from vibradiag.infrastructure.llm.ollama import embed, metadata
from vibradiag.core.paths import PROJECT_ROOT, RUNTIME_ROOT, KNOWLEDGE_CORPUS

ROOT = PROJECT_ROOT
CORPUS = KNOWLEDGE_CORPUS

def normalized(vectors):
    array = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if not np.all(np.isfinite(array)) or np.any(norms == 0):
        raise ValueError('Embedding 输出无效')
    return array/norms

def query_text(model, query):
    if model.startswith('qwen3-embedding'):
        return 'Instruct: Given a bearing vibration analysis question, retrieve passages explaining the relevant diagnostic conditions and limitations.\nQuery: ' + query
    return query

class KnowledgeIndex:
    def __init__(self, model='bge-m3:567m', cache=True):
        self.model = model
        self.documents = json.loads(CORPUS.read_text(encoding='utf-8'))
        self.metadata = metadata(model)
        self.corpus_sha256 = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
        identity = hashlib.sha256((self.metadata['digest']+self.corpus_sha256).encode()).hexdigest()
        folder = RUNTIME_ROOT / 'indexes'
        folder.mkdir(parents=True,exist_ok=True)
        file = folder / (identity+'.npz')
        self.index_timing = None
        if cache and file.exists():
            with np.load(file, allow_pickle=False) as saved:
                self.vectors = saved['vectors']
        else:
            vectors, self.index_timing = embed(model,[doc['title']+'\n'+doc['text'] for doc in self.documents])
            self.vectors = normalized(vectors)
            np.savez_compressed(file,vectors=self.vectors)

    def search(self, query, top_k=5):
        vectors, timing = embed(self.model,[query_text(self.model,query)])
        scores = (self.vectors @ normalized(vectors)[0]).tolist()
        ranking = sorted(range(len(scores)),key=lambda i:scores[i],reverse=True)[:top_k]
        return [{'id':self.documents[i]['id'],'title':self.documents[i]['title'],
                 'text':self.documents[i]['text'],'score':scores[i],
                 'source':'开发自编知识卡片，未经现场专家审核',
                 'corpus_sha256':self.corpus_sha256} for i in ranking], timing
