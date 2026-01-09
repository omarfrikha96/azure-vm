from django.http import JsonResponse
from django.views.decorators.http import require_GET

from inventory.azure_arm import (
    get_vms_inventory,
    get_resource_groups_inventory,
    get_resources_by_rg_inventory,
    get_single_resource_detail,
)
from inventory.models import AzureResource, VirtualMachine


@require_GET
def vms_live(request):
    """
    GET /api/vms-live/
    Live call to Azure ARM (no DB) - useful for quick tests.
    """
    try:
        data = get_vms_inventory()
        return JsonResponse({"count": len(data), "vms": data})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_GET
def vms_from_db(request):
    """
    GET /api/vms-db/
    Reads from the old VirtualMachine table (optional).
    """
    include_deleted = request.GET.get("include_deleted") == "1"
    qs = VirtualMachine.objects.all() if include_deleted else VirtualMachine.objects.filter(is_deleted=False)
    qs = qs.order_by("name")

    data = list(qs.values(
        "name", "resource_group", "location",
        "vm_size", "os_type", "computer_name",
        "power_state", "private_ips", "public_ips",
        "azure_id", "last_seen", "is_deleted", "deleted_at"
    ))
    return JsonResponse({"count": qs.count(), "vms": data})


@require_GET
def resources_list(request):
    """
    GET /api/resources/

    List ALL resources (from DB) with filters:
      - ?region=<location>          (example: northeurope)
      - ?rg=<resourceGroup>
      - ?type=<resourceType>        (example: Microsoft.Compute/virtualMachines)
      - ?name_contains=<text>
      - ?include_deleted=1
      - ?include_details=1          (includes component_summary in list; can be big)
    """
    include_deleted = request.GET.get("include_deleted") == "1"
    include_details = request.GET.get("include_details") == "1"

    qs = AzureResource.objects.all() if include_deleted else AzureResource.objects.filter(is_deleted=False)

    region = request.GET.get("region")
    rg = request.GET.get("rg")
    rtype = request.GET.get("type")
    name_contains = request.GET.get("name_contains")

    if region:
        qs = qs.filter(location__iexact=region)
    if rg:
        qs = qs.filter(resource_group__iexact=rg)
    if rtype:
        qs = qs.filter(type__iexact=rtype)
    if name_contains:
        qs = qs.filter(name__icontains=name_contains)

    qs = qs.order_by("type", "name")

    fields = [
        "azure_id", "name", "type", "resource_group", "location", "kind",
        "tags", "last_seen", "is_deleted"
    ]
    if include_details:
        fields.append("component_summary")

    # Safety limit for big subscriptions (you can raise later)
    data = list(qs.values(*fields)[:3000])
    return JsonResponse({"count": qs.count(), "resources": data})


@require_GET
def resource_details(request):
    """
    GET /api/resource-details/?id=<azure_resource_id>

    Returns DB-stored details:
      - component_summary (VM/storage/webapp/nsg/firewall/log analytics etc.)
      - raw_detail (only if you ran sync_resources --store-raw)
    """
    azure_id = request.GET.get("id")
    if not azure_id:
        return JsonResponse({"error": "missing ?id=<azure_resource_id>"}, status=400)

    try:
        r = AzureResource.objects.get(azure_id=azure_id)
    except AzureResource.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)

    return JsonResponse({
        "azure_id": r.azure_id,
        "name": r.name,
        "type": r.type,
        "resource_group": r.resource_group,
        "location": r.location,
        "kind": r.kind,
        "tags": r.tags,
        "component_summary": r.component_summary,
        "raw_detail": r.raw_detail,
        "detail_api_version": r.detail_api_version,
        "last_seen": r.last_seen,
        "is_deleted": r.is_deleted,
        "deleted_at": r.deleted_at,
    })


@require_GET
def resource_groups_list(request):
    """
    GET /api/resource-groups/
    
    Live call to Azure ARM to list all resource groups.
    Filters:
      - ?region=<location>
    """
    try:
        data = get_resource_groups_inventory()
        
        # Apply optional region filter
        region = request.GET.get("region")
        if region:
            data = [rg for rg in data if rg.get("location", "").lower() == region.lower()]
        
        return JsonResponse({"count": len(data), "resource_groups": data})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_GET
def resources_by_rg(request):
    """
    GET /api/resources-by-rg/?rg=<resource_group_name>
    
    Live call to Azure ARM to list all resources in a resource group.
    
    Filters:
      - ?rg=<resource_group_name>  (required)
      - ?type=<resource_type>      (optional)
      - ?include_details=1         (optional, slower - fetches component summaries)
    """
    rg_name = request.GET.get("rg")
    if not rg_name:
        return JsonResponse({"error": "missing ?rg=<resource_group_name>"}, status=400)
    
    include_details = request.GET.get("include_details") == "1"
    
    try:
        data = get_resources_by_rg_inventory(rg_name, include_details=include_details)
        
        # Apply optional type filter
        rtype = request.GET.get("type")
        if rtype:
            data = [r for r in data if r.get("type", "").lower() == rtype.lower()]
        
        return JsonResponse({"count": len(data), "resources": data})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_GET
def resource_detail_live(request):
    """
    GET /api/resource-detail/?id=<azure_resource_id>
    
    Live call to Azure ARM to get full details for a single resource.
    Returns component_summary with all properties.
    """
    azure_id = request.GET.get("id")
    if not azure_id:
        return JsonResponse({"error": "missing ?id=<azure_resource_id>"}, status=400)
    
    try:
        data = get_single_resource_detail(azure_id)
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_GET
def sync_status(request):
    """
    GET /api/sync-status/
    
    Returns the sync status:
      - last_synced: timestamp of the most recent resource update
      - resource_count: total number of resources in database
      - resource_groups: list of unique resource groups
    """
    from django.db.models import Max
    
    last_synced = AzureResource.objects.filter(is_deleted=False).aggregate(last=Max('last_seen'))['last']
    resource_count = AzureResource.objects.filter(is_deleted=False).count()
    resource_groups = list(
        AzureResource.objects.filter(is_deleted=False)
        .values_list('resource_group', flat=True)
        .distinct()
    )
    
    return JsonResponse({
        "last_synced": last_synced.isoformat() if last_synced else None,
        "resource_count": resource_count,
        "resource_groups": resource_groups,
    })


@require_GET
def resource_groups_db(request):
    """
    GET /api/resource-groups-db/
    
    Returns resource groups from the database (instant, no Azure API call).
    Aggregates unique resource groups and their resource counts from synced data.
    """
    from django.db.models import Count, Max
    
    qs = AzureResource.objects.filter(is_deleted=False).values('resource_group').annotate(
        resource_count=Count('id'),
        last_seen=Max('last_seen')
    ).order_by('resource_group')
    
    # Build response similar to live endpoint
    resource_groups = []
    for item in qs:
        rg_name = item['resource_group']
        if rg_name:
            # Get location from first resource in this group
            sample = AzureResource.objects.filter(
                resource_group=rg_name, 
                is_deleted=False
            ).values('location').first()
            
            resource_groups.append({
                "name": rg_name,
                "location": sample.get('location') if sample else None,
                "resource_count": item['resource_count'],
                "last_synced": item['last_seen'].isoformat() if item['last_seen'] else None,
            })
    
    return JsonResponse({
        "count": len(resource_groups),
        "resource_groups": resource_groups,
        "source": "database"
    })


@require_GET
def resources_by_rg_db(request):
    """
    GET /api/resources-by-rg-db/?rg=<resource_group_name>
    
    Returns resources from database (instant, no Azure API call).
    Includes component_summary from last sync.
    """
    rg_name = request.GET.get("rg")
    if not rg_name:
        return JsonResponse({"error": "missing ?rg=<resource_group_name>"}, status=400)
    
    qs = AzureResource.objects.filter(
        resource_group__iexact=rg_name,
        is_deleted=False
    ).order_by('type', 'name')
    
    # Optional type filter
    rtype = request.GET.get("type")
    if rtype:
        qs = qs.filter(type__iexact=rtype)
    
    data = []
    for r in qs[:500]:  # Safety limit
        data.append({
            "azure_id": r.azure_id,
            "name": r.name,
            "type": r.type,
            "location": r.location,
            "kind": r.kind,
            "tags": r.tags,
            "component_summary": r.component_summary,
            "last_synced": r.last_seen.isoformat() if r.last_seen else None,
        })
    
    return JsonResponse({
        "count": len(data),
        "resources": data,
        "source": "database"
    })

