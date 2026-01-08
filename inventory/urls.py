from django.urls import path
from .views import (
    vms_live,
    vms_from_db,
    resources_list,
    resource_details,
    resource_groups_list,
    resources_by_rg,
)

urlpatterns = [
    path("vms-live/", vms_live),
    path("vms-db/", vms_from_db),

    # New generic resources API
    path("resources/", resources_list),
    path("resource-details/", resource_details),
    
    # New endpoints for frontend dashboard
    path("resource-groups/", resource_groups_list),
    path("resources-by-rg/", resources_by_rg),
]

