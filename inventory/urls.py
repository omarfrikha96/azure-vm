from django.urls import path
from .views import vms_live, vms_from_db, resources_list, resource_details

urlpatterns = [
    path("vms-live/", vms_live),
    path("vms-db/", vms_from_db),

    # New generic resources API
    path("resources/", resources_list),
    path("resource-details/", resource_details),
]
