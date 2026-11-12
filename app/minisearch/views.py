import hashlib
import math
import re
from pathlib import Path
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from . import search_call as library

def response(value,status=200):
    result=JsonResponse(value,status=status,json_dumps_params={'ensure_ascii':False,'allow_nan':False})
    result['Cache-Control']='no-store'
    result['X-Content-Type-Options']='nosniff'
    return result

def api_guard(fn):
    def wrapped(request,*args,**kwargs):
        try:
            return fn(request,*args,**kwargs)
        except library.IndexUnavailable as exc:
            return response({'error':'index_unavailable','message':str(exc)},503)
        except ValueError as exc:
            return response({'error':'invalid_request','message':str(exc)},400)
    return require_GET(wrapped)

def integer(request,key,default,maximum):
    try:
        value=int(request.GET.get(key,default))
    except (TypeError,ValueError):
        raise ValueError(key+' must be an integer.')
    if value<1 or value>maximum:
        raise ValueError(key+' is outside its allowed range.')
    return value

@require_GET
def index(request):
    return render(request,'index.html')

@require_GET
def reader(request,book_id):
    try:
        book=library.find_book(book_id)
    except library.IndexUnavailable:
        book=None
    if not book:
        raise Http404('Book not found')
    return render(request,'reader.html',{'book_id':book_id,'book':library.public_book(book)})

@api_guard
def api_catalog(request):
    query=request.GET.get('q','').strip()
    if len(query)>200:
        raise ValueError('The query is limited to 200 characters.')
    return response(library.catalog_page(query,request.GET.get('author',''),request.GET.get('language',''),request.GET.get('sort','title'),integer(request,'page',1,100000),integer(request,'page_size',12,200)))

@api_guard
def api_search(request):
    query=request.GET.get('q',request.GET.get('keyword','')).strip()
    if len(query)>200:
        raise ValueError('The query is limited to 200 characters.')
    return response(library.search(query,request.GET.get('mode','any'),request.GET.get('sort','relevance'),integer(request,'page',1,100000),integer(request,'page_size',12,48),request.GET.get('author',''),request.GET.get('language',''),request.GET.get('engine','sqlite')))

@api_guard
def api_book(request,book_id):
    book=library.find_book(book_id)
    if not book:
        return response({'error':'book_not_found'},404)
    details=library.book_details(book)
    return response({'book':details,'similar':details['similar'],'recommendation_method':'same author'})

@api_guard
def api_read(request,book_id):
    book=library.find_book(book_id)
    if not book:
        return response({'error':'book_not_found'},404)
    # The public reader and download endpoint share the same verified rights gate.
    if not book.get('download_allowed'):
        return response({'error':'rights_not_verified','message':'Public text access is disabled until redistribution rights are verified.'},403)
    text=library.book_text(book_id)
    if text is None:
        return response({'error':'book_not_indexed'},404)
    pages=max(1,math.ceil(len(text)/12000));page=min(integer(request,'page',1,100000),pages)
    return response({'book':library.public_book(book),'text':text[(page-1)*12000:page*12000],'page':page,'pages':pages,'total_characters':len(text),'characters_per_page':12000,'text_includes_original_source_notices':True})

@api_guard
def api_stats(request):
    return response(library.stats())

@require_GET
def api_health(request):
    try:
        metadata=library.index_metadata()
        return response({'status':'ok','framework':'Django','index_builder':'PySpark','serving_engine':'spark-built-sqlite','read_only':True,'book_count':metadata.get('book_count'),'index_sha256':metadata.get('parquet_fingerprint'),'live_spark_available':__import__('os').environ.get('ALLOW_LIVE_SPARK','1')=='1'})
    except library.IndexUnavailable:
        return response({'status':'index_not_ready','read_only':True},503)

@require_GET
def download(request,book_id):
    try:
        book=library.find_book(book_id)
        if not book:
            raise Http404('Book not found')
        if book.get('download_allowed') is not True:
            return response({'error':'rights_not_verified','message':'Download disabled until redistribution rights are verified.'},403)
        path=library.data_path(Path('books')/book['filename'])
        if not path.is_file() or not path.is_relative_to(library.data_path('books')):
            raise Http404('Book not found')
        # Preserve the original bytes and all source/license notices.
        title=re.sub(r'[<>:"/\\|?*\x00-\x1f]',' ',str(book.get('title') or book['id'])).strip(' .')[:120]
        result=FileResponse(path.open('rb'),as_attachment=True,filename=(title or str(book['id']))+'.txt',content_type='text/plain; charset='+str(book.get('encoding') or 'utf-8'))
        result['X-Content-SHA256']=str(book.get('sha256') or '')
        result['Content-Length']=str(path.stat().st_size)
        result['Cache-Control']='public, max-age=3600'
        result['X-Content-Type-Options']='nosniff'
        return result
    except library.IndexUnavailable as exc:
        return response({'error':'index_unavailable','message':str(exc)},503)
