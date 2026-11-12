"""TF-IDF retrieval preserving the teacher's coverage-weighted ranking.

Production reads immutable postings exported FROM the actual PySpark Parquet
calculation. Optional engine=spark reads that Parquet with a live local JVM.
There is no sklearn/Python replacement TF-IDF calculation in this server.
"""
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from django.conf import settings

FORMULA = 'TF=count/eligible document tokens; IDF=ln((N+1)/(DF+1))+1; score=sum(TF-IDF)*matched_unique_terms/query_unique_terms'
TOKEN_RE = re.compile(r'[^\W\d_]+', re.UNICODE)
_spark_lock = threading.Lock()
_spark = None

class IndexUnavailable(Exception):
    pass

def tokenize(value):
    return sorted(set(word for word in TOKEN_RE.findall(str(value).lower()) if len(word) >= 2))[:20]

def data_path(relative):
    root = Path(settings.DATA_ROOT).resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError('Invalid data path')
    return candidate

def catalog():
    try:
        value = json.loads(data_path('catalog.json').read_text(encoding='utf-8'))
        if not isinstance(value, list):
            raise ValueError('Catalog root must be an array')
        return value
    except (OSError, ValueError) as exc:
        raise IndexUnavailable('The verified book catalog is not ready.') from exc

def find_book(book_id):
    # IDs only select exact known catalog records, never arbitrary filenames.
    return next((book for book in catalog() if str(book['id']) == str(book_id)), None)

def public_book(book):
    keys = ['id','filename','title','author','language','size_bytes','word_count','sha256','gutenberg_id','source_url','license_status','download_allowed','metadata_source','document_type','title_verified','rights_notice','encoding']
    result = {key: book.get(key) for key in keys}
    result['download_allowed'] = book.get('download_allowed') is True
    result['read_url'] = '/book/' + str(book['id'])
    result['download_url'] = '/books/' + str(book['id']) + '/download'
    result['preview'] = str(book.get('preview') or '')[:350] if result['download_allowed'] else ''
    return result

@contextmanager
def connection():
    path = data_path('index/search.sqlite3')
    if not path.is_file():
        raise IndexUnavailable('The PySpark index has not been built yet.')
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def index_metadata():
    with connection() as conn:
        return {row['key']: json.loads(row['value']) for row in conn.execute('SELECT key,value FROM metadata')}

def book_text(book_id):
    with connection() as conn:
        row = conn.execute('SELECT body FROM documents WHERE book_id=?', (book_id,)).fetchone()
    return row['body'] if row else None

def _snippet(text, terms, maximum=250):
    if not text:
        return ''
    found = next((match for term in terms if (match := re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text, re.IGNORECASE))), None)
    start = max(0, found.start() - 80) if found else 0
    end = min(len(text), start + maximum + 80)
    context=re.sub(r'\s+',' ',text[start:end]).strip()[:maximum]
    return ('…' if start else '') + context + ('…' if end < len(text) else '')

def _spark_rows(terms):
    global _spark
    if os.environ.get('ALLOW_LIVE_SPARK', '1') != '1':
        raise ValueError('Live Spark is disabled on this web deployment; the index was built by PySpark offline.')
    os.environ.setdefault('SPARK_LOCAL_IP', '127.0.0.1')
    from pyspark.sql import SparkSession, functions as F
    with _spark_lock:
        if _spark is None:
            _spark = SparkSession.builder.master('local[2]').appName('CPS4982-Live-Book-Query').config('spark.driver.host','127.0.0.1').config('spark.sql.shuffle.partitions','2').getOrCreate()
            _spark.sparkContext.setLogLevel('ERROR')
        frame = _spark.read.parquet(str(data_path('index/parquet'))).where(F.col('_2').isin(terms))
        grouped = frame.groupBy('_1').agg(F.sum('_3').alias('weight'), F.countDistinct('_2').alias('matched'), F.collect_set('_2').alias('terms'))
        return [{'filename': row['_1'], 'weight': row['weight'], 'matched': row['matched'], 'terms': sorted(row['terms'])} for row in grouped.collect()]

def search(query, mode='any', sort='relevance', page=1, page_size=12, author='', language='', engine='sqlite'):
    started = time.perf_counter()
    terms = tokenize(query)
    books = catalog()
    mapping = {book['filename']: book for book in books}
    if mode not in {'any','all','phrase'}:
        raise ValueError('Search mode must be any, all, or phrase.')
    if sort not in {'relevance','title','author'}:
        raise ValueError('Sort must be relevance, title, or author.')
    if engine not in {'sqlite','spark'}:
        raise ValueError('Engine must be sqlite or spark.')
    rows=[]
    if terms:
        if engine == 'spark':
            rows = _spark_rows(terms)
        else:
            marks = ','.join('?' for _ in terms)
            with connection() as conn:
                found = conn.execute(f'SELECT d.filename,SUM(p.tfidf) AS weight,COUNT(*) AS matched,GROUP_CONCAT(p.term) AS terms FROM postings p JOIN documents d ON p.book_id=d.book_id WHERE p.term IN ({marks}) GROUP BY p.book_id', terms).fetchall()
            rows = [dict(row, terms=row['terms'].split(',')) for row in found]
    accepted=[]
    with connection() as conn:
        for row in rows:
            book = mapping.get(row['filename'])
            if not book or not book.get('download_allowed') or (author and str(book.get('author','')).casefold() != author.casefold()) or (language and str(book.get('language','')).casefold() != language.casefold()):
                continue
            if mode in {'all','phrase'} and row['matched'] != len(terms):
                continue
            if mode == 'phrase':
                stored = conn.execute('SELECT search_text FROM documents WHERE book_id=?', (book['id'],)).fetchone()
                text = stored['search_text'] if stored else ''
                phrase = [w for w in TOKEN_RE.findall(query.lower()) if len(w)>=2]
                pattern = r'(?<!\w)' + r'[^\w]+'.join(re.escape(word) for word in phrase) + r'(?!\w)'
                if not phrase or not re.search(pattern,text,re.IGNORECASE):
                    continue
            item=public_book(book)
            item.update(score=float(row['weight']) * row['matched'] / len(terms),matched_terms=sorted(row['terms']),coverage=row['matched']/len(terms))
            accepted.append(item)
    accepted.sort(key=(lambda item:(str(item.get('title','')).casefold(),item['id'])) if sort=='title' else (lambda item:(str(item.get('author','')).casefold(),str(item.get('title','')).casefold(),item['id'])) if sort=='author' else (lambda item:(-item['score'],str(item.get('title','')).casefold(),item['id'])))
    total=len(accepted)
    pages=max(1,math.ceil(total/page_size))
    page=min(page,pages)
    visible=accepted[(page-1)*page_size:page*page_size]
    with connection() as conn:
        for item in visible:
            stored=conn.execute('SELECT search_text FROM documents WHERE book_id=?',(item['id'],)).fetchone()
            item['snippet']=_snippet(stored['search_text'] if stored else '',terms)
    return {'books':visible,'total':total,'page':page,'page_size':page_size,'pages':pages,'query':query,'terms':terms,'mode':mode,'sort':sort,'authors':sorted(set(str(book.get('author') or 'Not stated in source') for book in books)),'languages':sorted(set(str(book.get('language') or 'Unknown') for book in books)),'elapsed_ms':round((time.perf_counter()-started)*1000,2),'engine':'live-pyspark' if engine=='spark' else 'spark-built-sqlite','index_method':'TF-IDF','formula':FORMULA}

def catalog_page(query='',author='',language='',sort='title',page=1,page_size=12):
    books=catalog()
    authors=sorted(set(str(book.get('author') or 'Not stated in source') for book in books))
    languages=sorted(set(str(book.get('language') or 'Unknown') for book in books))
    terms=query.casefold().strip()
    filtered=[book for book in books if (not terms or terms in ' '.join(str(book.get(key) or '') for key in ['title','author','filename','gutenberg_id']).casefold()) and (not author or str(book.get('author','')).casefold()==author.casefold()) and (not language or str(book.get('language','')).casefold()==language.casefold())]
    if sort=='length':
        filtered.sort(key=lambda book:(-int(book.get('word_count') or 0),str(book['id'])))
    elif sort=='author':
        filtered.sort(key=lambda book:(str(book.get('author','')).casefold(),str(book.get('title','')).casefold()))
    else:
        filtered.sort(key=lambda book:(str(book.get('title','')).casefold(),str(book['id'])))
    total=len(filtered); pages=max(1,math.ceil(total/page_size));page=min(page,pages)
    return {'books':[public_book(book) for book in filtered[(page-1)*page_size:page*page_size]],'total':total,'page':page,'page_size':page_size,'pages':pages,'authors':authors,'languages':languages}

def book_details(book):
    result=public_book(book)
    # Same-author suggestions are transparent, not fabricated AI recommendations.
    similar=[public_book(other) for other in catalog() if other['id']!=book['id'] and other.get('author')==book.get('author')][:4]
    result['similar']=similar
    result['recommendation_method']='same author'
    with connection() as conn:
        row=conn.execute('SELECT COUNT(*) AS unique_terms,COALESCE(SUM(tf),0) AS indexed_tokens FROM postings WHERE book_id=?',(book['id'],)).fetchone()
    result['unique_terms']=row['unique_terms'];result['indexed_tokens']=row['indexed_tokens']
    return result

def stats():
    books=catalog()
    metadata=index_metadata()
    metadata.update(book_count=len(books),verified_ebooks=sum(book.get('title_verified') is True and book.get('document_type')=='ebook' for book in books),restricted_documents=sum(book.get('download_allowed') is not True for book in books),downloadable_books=sum(book.get('download_allowed') is True for book in books),total_bytes=sum(int(book.get('size_bytes') or 0) for book in books),total_words=sum(int(book.get('word_count') or 0) for book in books),author_count=len(set(book.get('author') for book in books)),languages=sorted(set(str(book.get('language') or 'Unknown') for book in books)),featured_books=[public_book(book) for book in sorted((book for book in books if book.get('download_allowed')),key=lambda book:-(int(book.get('word_count') or 0)))[:6]],engine='spark-built-sqlite',formula=FORMULA)
    return metadata
