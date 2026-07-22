from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('scheduling.urls')),
    path('api/auth/token/', obtain_auth_token),
    path('api-auth/', include('rest_framework.urls')),
]
