from django.urls import path
from .views import (
    vms_live,
    vms_from_db,
    resources_list,
    resource_details,
    resource_groups_list,
    resources_by_rg,
    resource_detail_live,
    sync_status,
    resource_groups_db,
    resources_by_rg_db,
    global_search,
    trigger_sync,
    security_posture,
    resource_stats,
)

urlpatterns = [
    path("vms-live/", vms_live),
    path("vms-db/", vms_from_db),

    # Generic resources API (from DB)
    path("resources/", resources_list),
    path("resource-details/", resource_details),
    
    # Live endpoints (call Azure ARM API)
    path("resource-groups/", resource_groups_list),
    path("resources-by-rg/", resources_by_rg),
    path("resource-detail/", resource_detail_live),
    
    # Database-backed endpoints (instant loading)
    path("sync-status/", sync_status),
    path("resource-groups-db/", resource_groups_db),
    path("resources-by-rg-db/", resources_by_rg_db),
    
    # Features
    path("search/", global_search),
    path("trigger-sync/", trigger_sync),
    path("security-posture/", security_posture),
    path("resource-stats/", resource_stats),
]

