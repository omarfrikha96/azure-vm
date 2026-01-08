import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from azure.identity import DefaultAzureCredential


SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "").strip()
ARM_BASE = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"

# Generic list API
GENERIC_LIST_API = "2021-04-01"

# Stable APIs for common sub-calls
COMPUTE_API_VERSION = "2023-09-01"
NETWORK_API_VERSION = "2023-09-01"
WEB_API_VERSION = "2022-09-01"

# DefaultAzureCredential will work on:
# - Azure VM (Managed Identity)
# - App Service (Managed Identity)
# - many other Azure hosts
_credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)

# Simple token cache (avoid fetching token for every call)
_token_cache: Dict[str, Any] = {"token": None, "expires_on": 0}

# Provider metadata cache: namespace -> provider JSON
_provider_cache: Dict[str, Dict[str, Any]] = {}

# Small resource detail cache for frequently repeated IDs (PIP, subnet, etc.)
_detail_cache: Dict[str, Dict[str, Any]] = {}


def _require_subscription() -> None:
    if not SUBSCRIPTION_ID:
        raise RuntimeError("AZURE_SUBSCRIPTION_ID environment variable is not set.")


def get_token() -> str:
    """
    Return an ARM access token using Azure Identity SDK (Managed Identity on Azure VM).
    """
    _require_subscription()

    now = int(time.time())
    if _token_cache["token"] and now < int(_token_cache["expires_on"]) - 60:
        return _token_cache["token"]

    t = _credential.get_token(ARM_SCOPE)
    _token_cache["token"] = t.token
    _token_cache["expires_on"] = getattr(t, "expires_on", now + 3000)
    return t.token


def arm_get(url: str, token: str, timeout: int = 30) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, timeout=timeout)
    if not r.ok:
        raise requests.HTTPError(
            f"{r.status_code} {r.reason} for {url}\nResponse:\n{r.text}",
            response=r,
        )
    return r.json()


def arm_get_paged(url: str, token: str, timeout: int = 30) -> List[Dict[str, Any]]:
    """
    Follow nextLink pages (ARM list endpoints can paginate).
    Returns concatenated 'value' arrays.
    """
    out: List[Dict[str, Any]] = []
    next_url = url
    while next_url:
        data = arm_get(next_url, token, timeout=timeout)
        out.extend(data.get("value", []) or [])
        next_url = data.get("nextLink")
    return out


def parse_rg_from_id(resource_id: str) -> Optional[str]:
    m = re.search(r"/resourceGroups/([^/]+)/", resource_id, flags=re.IGNORECASE)
    return m.group(1) if m else None


def _split_namespace_and_restype(full_type: str) -> Tuple[str, str]:
    # Example: Microsoft.Compute/virtualMachines -> (Microsoft.Compute, virtualMachines)
    parts = full_type.split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return full_type, ""


def _pick_best_api_version(api_versions: List[str]) -> str:
    stable = [v for v in api_versions if "preview" not in v.lower() and "beta" not in v.lower()]
    return stable[0] if stable else api_versions[0]


def get_provider_metadata(token: str, namespace: str) -> Dict[str, Any]:
    if namespace in _provider_cache:
        return _provider_cache[namespace]

    url = f"{ARM_BASE}/subscriptions/{SUBSCRIPTION_ID}/providers/{namespace}?api-version={GENERIC_LIST_API}"
    data = arm_get(url, token)
    _provider_cache[namespace] = data
    return data


def get_api_version_for_type(token: str, full_type: str) -> str:
    namespace, restype = _split_namespace_and_restype(full_type)
    provider = get_provider_metadata(token, namespace)

    # direct match
    for rt in provider.get("resourceTypes", []) or []:
        if (rt.get("resourceType") or "").lower() == restype.lower():
            versions = rt.get("apiVersions", []) or []
            if not versions:
                break
            return _pick_best_api_version(versions)

    # nested fallback: trim left segments (helps for some nested types)
    segs = restype.split("/")
    while len(segs) > 1:
        segs = segs[1:]
        candidate = "/".join(segs)
        for rt in provider.get("resourceTypes", []) or []:
            if (rt.get("resourceType") or "").lower() == candidate.lower():
                versions = rt.get("apiVersions", []) or []
                if versions:
                    return _pick_best_api_version(versions)

    raise RuntimeError(f"No api-version found for type: {full_type}")


def list_all_resources(token: str) -> List[Dict[str, Any]]:
    url = f"{ARM_BASE}/subscriptions/{SUBSCRIPTION_ID}/resources?api-version={GENERIC_LIST_API}"
    return arm_get_paged(url, token)


def list_resource_groups(token: str) -> List[Dict[str, Any]]:
    """
    List all resource groups in the subscription.
    Returns a list of resource group objects with name, location, tags, etc.
    """
    _require_subscription()
    url = f"{ARM_BASE}/subscriptions/{SUBSCRIPTION_ID}/resourcegroups?api-version={GENERIC_LIST_API}"
    return arm_get_paged(url, token)


def list_resources_by_rg(token: str, resource_group: str) -> List[Dict[str, Any]]:
    """
    List all resources in a specific resource group.
    Returns a list of resource objects.
    """
    _require_subscription()
    url = f"{ARM_BASE}/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{resource_group}/resources?api-version={GENERIC_LIST_API}"
    return arm_get_paged(url, token)


def get_resource_groups_inventory() -> List[Dict[str, Any]]:
    """
    Get all resource groups with basic info (no auth param needed - uses internal token).
    """
    token = get_token()
    rgs = list_resource_groups(token)
    
    result = []
    for rg in rgs:
        props = rg.get("properties") or {}
        result.append({
            "name": rg.get("name"),
            "location": rg.get("location"),
            "tags": rg.get("tags") or {},
            "provisioningState": props.get("provisioningState"),
            "id": rg.get("id"),
        })
    return result


def get_resources_by_rg_inventory(resource_group: str) -> List[Dict[str, Any]]:
    """
    Get all resources in a resource group with component summaries.
    """
    token = get_token()
    resources = list_resources_by_rg(token, resource_group)
    
    result = []
    for res in resources:
        # Build basic info
        item = {
            "azure_id": res.get("id"),
            "name": res.get("name"),
            "type": res.get("type"),
            "location": res.get("location"),
            "kind": res.get("kind"),
            "tags": res.get("tags") or {},
        }
        
        # Try to get component summary for supported resource types
        try:
            raw_detail, _api_version = get_resource_raw_detail(token, res["id"], res["type"])
            item["component_summary"] = build_component_summary(token, raw_detail)
        except Exception:
            item["component_summary"] = None
        
        result.append(item)
    
    return result


def get_resource_raw_detail(token: str, resource_id: str, full_type: str) -> Tuple[Dict[str, Any], str]:
    api_version = get_api_version_for_type(token, full_type)
    url = f"{ARM_BASE}{resource_id}?api-version={api_version}"
    return arm_get(url, token), api_version


# -------------------------
# Helpers for common Network/Compute resources
# -------------------------

def _cached_get(token: str, resource_id: str, api_version: str) -> Dict[str, Any]:
    key = f"{resource_id}|{api_version}"
    if key in _detail_cache:
        return _detail_cache[key]
    url = f"{ARM_BASE}{resource_id}?api-version={api_version}"
    data = arm_get(url, token)
    _detail_cache[key] = data
    return data


def get_nic(token: str, nic_id: str) -> Dict[str, Any]:
    return _cached_get(token, nic_id, NETWORK_API_VERSION)


def get_public_ip(token: str, pip_id: str) -> Dict[str, Any]:
    return _cached_get(token, pip_id, NETWORK_API_VERSION)


def get_subnet(token: str, subnet_id: str) -> Dict[str, Any]:
    return _cached_get(token, subnet_id, NETWORK_API_VERSION)


def get_vm_instance_view(token: str, rg: str, vm_name: str) -> Dict[str, Any]:
    url = (
        f"{ARM_BASE}/subscriptions/{SUBSCRIPTION_ID}"
        f"/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{vm_name}"
        f"/instanceView?api-version={COMPUTE_API_VERSION}"
    )
    return arm_get(url, token)


def get_app_service_web_config(token: str, rg: str, site_name: str) -> Dict[str, Any]:
    """
    Fetch /config/web for runtime/platform info (linuxFxVersion, netFrameworkVersion, etc.)
    """
    url = (
        f"{ARM_BASE}/subscriptions/{SUBSCRIPTION_ID}"
        f"/resourceGroups/{rg}/providers/Microsoft.Web/sites/{site_name}"
        f"/config/web?api-version={WEB_API_VERSION}"
    )
    return arm_get(url, token)


def _name_from_id(resource_id: Optional[str]) -> Optional[str]:
    if not resource_id:
        return None
    return resource_id.rstrip("/").split("/")[-1]


def _parse_vnet_subnet_from_subnet_id(subnet_id: str) -> Tuple[Optional[str], Optional[str]]:
    # .../virtualNetworks/<vnet>/subnets/<subnet>
    m = re.search(r"/virtualNetworks/([^/]+)/subnets/([^/]+)", subnet_id, flags=re.IGNORECASE)
    if not m:
        return None, None
    return m.group(1), m.group(2)


# -------------------------
# Component summary builders
# -------------------------

def build_vm_summary(token: str, vm_raw: Dict[str, Any]) -> Dict[str, Any]:
    vm_id = vm_raw["id"]
    rg = parse_rg_from_id(vm_id) or "UNKNOWN_RG"
    name = vm_raw.get("name")
    location = vm_raw.get("location")

    props = vm_raw.get("properties") or {}
    hw = props.get("hardwareProfile") or {}
    storage = props.get("storageProfile") or {}
    os_profile = props.get("osProfile") or {}
    net = props.get("networkProfile") or {}

    vm_size = hw.get("vmSize")
    zones = vm_raw.get("zones") or []

    image_ref = storage.get("imageReference") or {}
    os_disk = storage.get("osDisk") or {}
    data_disks = storage.get("dataDisks") or []

    # Power state
    power_state = None
    try:
        iv = get_vm_instance_view(token, rg, name)
        for s in iv.get("statuses", []) or []:
            code = (s.get("code") or "")
            if code.startswith("PowerState/"):
                power_state = code.split("/", 1)[1]
                break
    except Exception:
        power_state = None

    # NIC/IP/VNET/SUBNET
    nic_refs = net.get("networkInterfaces") or []
    nic_summaries = []
    private_ips = set()
    public_ips = set()
    vnet_names = set()
    subnet_names = set()

    for nicref in nic_refs:
        nic = get_nic(token, nicref["id"])
        nic_props = nic.get("properties") or {}
        nic_name = nic.get("name")

        nsg_id = (nic_props.get("networkSecurityGroup") or {}).get("id")
        nsg_name = _name_from_id(nsg_id)

        ipconfs = nic_props.get("ipConfigurations") or []
        nic_private = []
        nic_public = []
        nic_subnets = []

        for ipc in ipconfs:
            ipprops = ipc.get("properties") or {}
            priv = ipprops.get("privateIPAddress")
            if priv:
                private_ips.add(priv)
                nic_private.append(priv)

            subnet_id = (ipprops.get("subnet") or {}).get("id")
            if subnet_id:
                vnet, subnet = _parse_vnet_subnet_from_subnet_id(subnet_id)
                if vnet:
                    vnet_names.add(vnet)
                if subnet:
                    subnet_names.add(subnet)
                nic_subnets.append(
                    {
                        "subnetId": subnet_id,
                        "vnet": vnet,
                        "subnet": subnet,
                    }
                )

            pip_id = (ipprops.get("publicIPAddress") or {}).get("id")
            if pip_id:
                pip_obj = get_public_ip(token, pip_id)
                pip_addr = (pip_obj.get("properties") or {}).get("ipAddress")
                if pip_addr:
                    public_ips.add(pip_addr)
                    nic_public.append(pip_addr)

        nic_summaries.append(
            {
                "name": nic_name,
                "id": nic.get("id"),
                "nsg": {"id": nsg_id, "name": nsg_name},
                "privateIPs": sorted(set(nic_private)),
                "publicIPs": sorted(set(nic_public)),
                "subnets": nic_subnets,
            }
        )

    disks = {
        "osDisk": {
            "name": os_disk.get("name"),
            "diskSizeGB": os_disk.get("diskSizeGB"),
            "caching": os_disk.get("caching"),
            "storageAccountType": (os_disk.get("managedDisk") or {}).get("storageAccountType"),
            "managedDiskId": (os_disk.get("managedDisk") or {}).get("id"),
        },
        "dataDisks": [
            {
                "name": d.get("name"),
                "diskSizeGB": d.get("diskSizeGB"),
                "lun": d.get("lun"),
                "caching": d.get("caching"),
                "storageAccountType": (d.get("managedDisk") or {}).get("storageAccountType"),
                "managedDiskId": (d.get("managedDisk") or {}).get("id"),
            }
            for d in data_disks
        ],
    }

    return {
        "component": "vm",
        "id": vm_id,
        "name": name,
        "resourceGroup": rg,
        "location": location,
        "status": power_state,
        "size": vm_size,
        "zones": zones,
        "osType": os_disk.get("osType"),
        "computerName": os_profile.get("computerName"),
        "imageReference": {
            "publisher": image_ref.get("publisher"),
            "offer": image_ref.get("offer"),
            "sku": image_ref.get("sku"),
            "version": image_ref.get("version"),
        },
        "privateIPs": sorted(private_ips),
        "publicIPs": sorted(public_ips),
        "virtualNetworks": sorted(vnet_names),
        "subnets": sorted(subnet_names),
        "nics": nic_summaries,
        "disks": disks,
    }


def build_storage_summary(_token: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    props = raw.get("properties") or {}
    nacls = props.get("networkAcls") or {}

    ip_rules = nacls.get("ipRules") or []
    vnet_rules = nacls.get("virtualNetworkRules") or []

    return {
        "component": "storage",
        "id": raw.get("id"),
        "name": raw.get("name"),
        "location": raw.get("location"),
        "kind": raw.get("kind"),
        "sku": raw.get("sku") or {},
        "provisioningState": props.get("provisioningState"),
        "accessTier": props.get("accessTier"),
        "supportsHttpsTrafficOnly": props.get("supportsHttpsTrafficOnly"),
        "minimumTlsVersion": props.get("minimumTlsVersion"),
        "allowBlobPublicAccess": props.get("allowBlobPublicAccess"),
        "publicNetworkAccess": props.get("publicNetworkAccess"),
        "primaryEndpoints": props.get("primaryEndpoints") or {},
        "networkAcls": {
            "defaultAction": nacls.get("defaultAction"),
            "bypass": nacls.get("bypass"),
            "ipRulesCount": len(ip_rules),
            "virtualNetworkRulesCount": len(vnet_rules),
        },
        "encryption": props.get("encryption") or {},
        "privateEndpointConnectionsCount": len(props.get("privateEndpointConnections") or []),
    }


def build_app_service_summary(token: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    rid = raw.get("id")
    rg = parse_rg_from_id(rid or "") or "UNKNOWN_RG"
    name = raw.get("name")

    props = raw.get("properties") or {}

    # Pull config/web for runtime details (best effort)
    runtime = {}
    try:
        cfg = get_app_service_web_config(token, rg, name)
        cprops = cfg.get("properties") or {}
        runtime = {
            "linuxFxVersion": cprops.get("linuxFxVersion"),
            "windowsFxVersion": cprops.get("windowsFxVersion"),
            "pythonVersion": cprops.get("pythonVersion"),
            "phpVersion": cprops.get("phpVersion"),
            "nodeVersion": cprops.get("nodeVersion"),
            "netFrameworkVersion": cprops.get("netFrameworkVersion"),
            "javaVersion": cprops.get("javaVersion"),
            "javaContainer": cprops.get("javaContainer"),
            "javaContainerVersion": cprops.get("javaContainerVersion"),
        }
    except Exception:
        runtime = {}

    plan_id = props.get("serverFarmId")
    vnet_subnet_id = props.get("virtualNetworkSubnetId")
    vnet_name, subnet_name = (None, None)
    if vnet_subnet_id:
        vnet_name, subnet_name = _parse_vnet_subnet_from_subnet_id(vnet_subnet_id)

    return {
        "component": "app_service",
        "id": rid,
        "name": name,
        "resourceGroup": rg,
        "location": raw.get("location"),
        "kind": raw.get("kind"),
        "state": props.get("state"),
        "defaultHostName": props.get("defaultHostName"),
        "hostNames": (props.get("hostNames") or [])[:50],
        "httpsOnly": props.get("httpsOnly"),
        "clientAffinityEnabled": props.get("clientAffinityEnabled"),
        "reservedLinux": props.get("reserved"),  # True often means Linux
        "scmSiteAlsoStopped": props.get("scmSiteAlsoStopped"),
        "appServicePlan": {"id": plan_id, "name": _name_from_id(plan_id)},
        "outboundIpAddresses": (props.get("outboundIpAddresses") or "").split(",") if props.get("outboundIpAddresses") else [],
        "possibleOutboundIpAddresses": (props.get("possibleOutboundIpAddresses") or "").split(",") if props.get("possibleOutboundIpAddresses") else [],
        "vnetIntegration": {
            "subnetId": vnet_subnet_id,
            "vnet": vnet_name,
            "subnet": subnet_name,
        } if vnet_subnet_id else None,
        "identity": raw.get("identity") or {},
        "runtime": runtime,
    }


def build_app_service_plan_summary(_token: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    props = raw.get("properties") or {}
    return {
        "component": "app_service_plan",
        "id": raw.get("id"),
        "name": raw.get("name"),
        "location": raw.get("location"),
        "sku": raw.get("sku") or {},
        "status": props.get("status"),
        "numberOfWorkers": props.get("numberOfWorkers"),
        "maximumElasticWorkerCount": props.get("maximumElasticWorkerCount"),
        "reservedLinux": props.get("reserved"),
        "perSiteScaling": props.get("perSiteScaling"),
        "isXenon": props.get("isXenon"),
        "hyperV": props.get("hyperV"),
    }


def _compact_nsg_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    p = rule.get("properties") or {}

    def _first(x):
        if isinstance(x, list):
            return x[:10]
        return x

    return {
        "name": rule.get("name"),
        "priority": p.get("priority"),
        "direction": p.get("direction"),
        "access": p.get("access"),
        "protocol": p.get("protocol"),
        "sourceAddressPrefix": _first(p.get("sourceAddressPrefix") or p.get("sourceAddressPrefixes")),
        "sourcePortRange": _first(p.get("sourcePortRange") or p.get("sourcePortRanges")),
        "destinationAddressPrefix": _first(p.get("destinationAddressPrefix") or p.get("destinationAddressPrefixes")),
        "destinationPortRange": _first(p.get("destinationPortRange") or p.get("destinationPortRanges")),
        "description": p.get("description"),
    }


def build_nsg_summary(_token: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    props = raw.get("properties") or {}
    rules = props.get("securityRules") or []
    default_rules = props.get("defaultSecurityRules") or []

    inbound = [r for r in rules if (r.get("properties") or {}).get("direction") == "Inbound"]
    outbound = [r for r in rules if (r.get("properties") or {}).get("direction") == "Outbound"]

    # sort by priority (lower is more important)
    inbound_sorted = sorted(inbound, key=lambda r: (r.get("properties") or {}).get("priority", 9999))
    outbound_sorted = sorted(outbound, key=lambda r: (r.get("properties") or {}).get("priority", 9999))

    return {
        "component": "nsg",
        "id": raw.get("id"),
        "name": raw.get("name"),
        "location": raw.get("location"),
        "provisioningState": props.get("provisioningState"),
        "counts": {
            "securityRules": len(rules),
            "defaultSecurityRules": len(default_rules),
            "inboundRules": len(inbound),
            "outboundRules": len(outbound),
        },
        "topInboundRules": [_compact_nsg_rule(r) for r in inbound_sorted[:20]],
        "topOutboundRules": [_compact_nsg_rule(r) for r in outbound_sorted[:20]],
    }


def build_public_ip_summary(_token: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    props = raw.get("properties") or {}
    dns = props.get("dnsSettings") or {}
    return {
        "component": "public_ip",
        "id": raw.get("id"),
        "name": raw.get("name"),
        "location": raw.get("location"),
        "ipAddress": props.get("ipAddress"),
        "allocationMethod": props.get("publicIPAllocationMethod"),
        "ipVersion": props.get("publicIPAddressVersion"),
        "sku": raw.get("sku") or {},
        "fqdn": dns.get("fqdn"),
        "domainNameLabel": dns.get("domainNameLabel"),
    }


def build_nic_summary(_token: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    props = raw.get("properties") or {}
    ipconfs = props.get("ipConfigurations") or []
    nsg_id = (props.get("networkSecurityGroup") or {}).get("id")

    private_ips = []
    subnets = []
    public_ip_ids = []

    for ipc in ipconfs:
        ipprops = ipc.get("properties") or {}
        priv = ipprops.get("privateIPAddress")
        if priv:
            private_ips.append(priv)

        subnet_id = (ipprops.get("subnet") or {}).get("id")
        if subnet_id:
            vnet, subnet = _parse_vnet_subnet_from_subnet_id(subnet_id)
            subnets.append({"subnetId": subnet_id, "vnet": vnet, "subnet": subnet})

        pip_id = (ipprops.get("publicIPAddress") or {}).get("id")
        if pip_id:
            public_ip_ids.append(pip_id)

    return {
        "component": "nic",
        "id": raw.get("id"),
        "name": raw.get("name"),
        "location": raw.get("location"),
        "macAddress": props.get("macAddress"),
        "enableAcceleratedNetworking": props.get("enableAcceleratedNetworking"),
        "networkSecurityGroup": {"id": nsg_id, "name": _name_from_id(nsg_id)},
        "privateIPs": sorted(set(private_ips)),
        "subnets": subnets,
        "publicIpResourceIds": sorted(set(public_ip_ids)),
    }


def build_vnet_summary(_token: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    props = raw.get("properties") or {}
    addr = (props.get("addressSpace") or {}).get("addressPrefixes") or []
    dhcp = props.get("dhcpOptions") or {}
    subnets = props.get("subnets") or []

    sub_summary = []
    for s in subnets[:100]:
        sp = s.get("properties") or {}
        sub_summary.append(
            {
                "name": s.get("name"),
                "addressPrefix": sp.get("addressPrefix"),
                "addressPrefixes": sp.get("addressPrefixes"),
                "nsg": {"id": (sp.get("networkSecurityGroup") or {}).get("id"),
                        "name": _name_from_id((sp.get("networkSecurityGroup") or {}).get("id"))},
                "routeTable": {"id": (sp.get("routeTable") or {}).get("id"),
                               "name": _name_from_id((sp.get("routeTable") or {}).get("id"))},
            }
        )

    return {
        "component": "virtual_network",
        "id": raw.get("id"),
        "name": raw.get("name"),
        "location": raw.get("location"),
        "addressPrefixes": addr,
        "dnsServers": dhcp.get("dnsServers") or [],
        "subnets": sub_summary,
        "enableDdosProtection": props.get("enableDdosProtection"),
    }


def _list_firewall_policy_rule_collection_groups(token: str, policy_id: str) -> List[Dict[str, Any]]:
    url = f"{ARM_BASE}{policy_id}/ruleCollectionGroups?api-version={NETWORK_API_VERSION}"
    return arm_get_paged(url, token)


def build_firewall_policy_summary(token: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    props = raw.get("properties") or {}
    sku = raw.get("sku") or {}

    # rule collection groups (best effort)
    rcg_info = {"count": None, "groups": []}
    try:
        groups = _list_firewall_policy_rule_collection_groups(token, raw.get("id"))
        rcg_info["count"] = len(groups)

        for g in groups[:25]:
            gp = g.get("properties") or {}
            collections = gp.get("ruleCollections") or []
            rule_count = 0
            collection_names = []
            for c in collections:
                cp = c.get("properties") or {}
                collection_names.append(c.get("name"))
                rules = cp.get("rules") or []
                rule_count += len(rules)
            rcg_info["groups"].append(
                {
                    "name": g.get("name"),
                    "priority": gp.get("priority"),
                    "ruleCollectionsCount": len(collections),
                    "rulesCount": rule_count,
                    "ruleCollections": collection_names[:30],
                }
            )
    except Exception:
        rcg_info = {"count": None, "groups": []}

    return {
        "component": "firewall_policy",
        "id": raw.get("id"),
        "name": raw.get("name"),
        "location": raw.get("location"),
        "sku": sku,
        "provisioningState": props.get("provisioningState"),
        "threatIntelMode": props.get("threatIntelMode"),
        "dnsSettings": props.get("dnsSettings") or {},
        "basePolicy": {"id": (props.get("basePolicy") or {}).get("id"),
                       "name": _name_from_id((props.get("basePolicy") or {}).get("id"))},
        "ruleCollectionGroups": rcg_info,
    }


def build_azure_firewall_summary(token: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    props = raw.get("properties") or {}
    ip_confs = props.get("ipConfigurations") or []

    private_ips = set()
    public_ips = set()

    for c in ip_confs:
        cp = c.get("properties") or {}
        priv = cp.get("privateIPAddress")
        if priv:
            private_ips.add(priv)

        pip_id = (cp.get("publicIPAddress") or {}).get("id")
        if pip_id:
            pip = get_public_ip(token, pip_id)
            addr = (pip.get("properties") or {}).get("ipAddress")
            if addr:
                public_ips.add(addr)

    return {
        "component": "azure_firewall",
        "id": raw.get("id"),
        "name": raw.get("name"),
        "location": raw.get("location"),
        "sku": props.get("sku") or raw.get("sku") or {},
        "provisioningState": props.get("provisioningState"),
        "threatIntelMode": props.get("threatIntelMode"),
        "firewallPolicy": {"id": (props.get("firewallPolicy") or {}).get("id"),
                           "name": _name_from_id((props.get("firewallPolicy") or {}).get("id"))},
        "privateIPs": sorted(private_ips),
        "publicIPs": sorted(public_ips),
    }


def build_log_analytics_summary(_token: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    props = raw.get("properties") or {}
    sku = raw.get("sku") or {}
    features = props.get("features") or {}
    return {
        "component": "log_analytics",
        "id": raw.get("id"),
        "name": raw.get("name"),
        "location": raw.get("location"),
        "sku": sku,
        "customerId": props.get("customerId"),
        "retentionInDays": props.get("retentionInDays"),
        "publicNetworkAccessForIngestion": props.get("publicNetworkAccessForIngestion"),
        "publicNetworkAccessForQuery": props.get("publicNetworkAccessForQuery"),
        "features": features,
        "provisioningState": props.get("provisioningState"),
    }


def build_generic_summary(_token: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "component": "generic",
        "id": raw.get("id"),
        "name": raw.get("name"),
        "type": raw.get("type"),
        "location": raw.get("location"),
        "kind": raw.get("kind"),
        "tags": raw.get("tags") or {},
        "propertiesKeys": list((raw.get("properties") or {}).keys())[:40],
    }


def build_component_summary(token: str, raw_detail: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dispatch by resource type.
    """
    rtype = (raw_detail.get("type") or "").lower()

    builders = {
        "microsoft.compute/virtualmachines": build_vm_summary,
        "microsoft.storage/storageaccounts": build_storage_summary,
        "microsoft.web/sites": build_app_service_summary,
        "microsoft.web/serverfarms": build_app_service_plan_summary,
        "microsoft.network/networksecuritygroups": build_nsg_summary,
        "microsoft.network/firewallpolicies": build_firewall_policy_summary,
        "microsoft.network/azurefirewalls": build_azure_firewall_summary,
        "microsoft.operationalinsights/workspaces": build_log_analytics_summary,
        "microsoft.network/virtualnetworks": build_vnet_summary,
        "microsoft.network/networkinterfaces": build_nic_summary,
        "microsoft.network/publicipaddresses": build_public_ip_summary,
    }

    fn = builders.get(rtype)
    if fn:
        return fn(token, raw_detail)
    return build_generic_summary(token, raw_detail)


# -------------------------
# Backward-compatible VM list for your live endpoint
# -------------------------

def list_vms(token: str) -> List[Dict[str, Any]]:
    url = (
        f"{ARM_BASE}/subscriptions/{SUBSCRIPTION_ID}"
        f"/providers/Microsoft.Compute/virtualMachines?api-version={COMPUTE_API_VERSION}"
    )
    return arm_get_paged(url, token)


def get_vms_inventory() -> List[Dict[str, Any]]:
    token = get_token()
    vms = list_vms(token)

    # vms list already returns VM objects (not just basic), but to ensure consistent,
    # we can fetch raw detail if needed; for most cases VM list is enough.
    out = []
    for vm in vms:
        # Some list calls return minimal; safest is raw GET:
        try:
            raw, _ver = get_resource_raw_detail(token, vm["id"], vm["type"])
        except Exception:
            raw = vm
        out.append(build_vm_summary(token, raw))
    return out
