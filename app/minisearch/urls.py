from django.urls import path
from . import views
urlpatterns=[
    path('',views.index,name='index'),
    path('search',views.index),
    path('book/<str:book_id>',views.reader,name='reader'),
    path('book/<str:book_id>/',views.reader),
    path('api/catalog',views.api_catalog),
    path('api/search',views.api_search),
    path('api/books/<str:book_id>',views.api_book),
    path('api/books/<str:book_id>/read',views.api_read),
    path('api/stats',views.api_stats),
    path('api/health',views.api_health),
    path('books/<str:book_id>/download',views.download),
    path('books/<str:book_id>/download/',views.download),
]
