from django.core.management.base import BaseCommand
from django.utils import timezone

from inventory.models import AzureResource
from inventory.azure_arm import (
    get_token,
    list_all_resources,
    parse_rg_from_id,
    get_resource_raw_detail,
    build_component_summary,
)


IMPORTANT_TYPES_DEFAULT = [
    "Microsoft.Compute/virtualMachines",
    "Microsoft.Storage/storageAccounts",
    "Microsoft.Web/sites",
    "Microsoft.Web/serverFarms",
    "Microsoft.Network/networkSecurityGroups",
    "Microsoft.Network/firewallPolicies",
    "Microsoft.Network/azureFirewalls",
    "Microsoft.OperationalInsights/workspaces",
    "Microsoft.Network/virtualNetworks",
    "Microsoft.Network/networkInterfaces",
    "Microsoft.Network/publicIPAddresses",
]


class Command(BaseCommand):
    help = "Sync ALL Azure resources into DB. Optionally fetch type-specific details and store summaries."

    def add_arguments(self, parser):
        parser.add_argument(
            "--with-details",
            action="store_true",
            help="Fetch per-resource GET details + store component_summary for selected types",
        )
        parser.add_argument(
            "--store-raw",
            action="store_true",
            help="Also store raw_detail JSON (can be big).",
        )
        parser.add_argument(
            "--types",
            help=(
                "Comma-separated resource types to fetch details for. "
                "Default is a recommended set (VM, Storage, WebApp, NSG, Firewall, Log Analytics, VNet, NIC, Public IP). "
                "Example: --types Microsoft.Compute/virtualMachines,Microsoft.Storage/storageAccounts"
            ),
        )
        parser.add_argument(
            "--all-types",
            action="store_true",
            help="Fetch details for ALL resources (can be heavy if subscription is large).",
        )

    def handle(self, *args, **options):
        token = get_token()
        now = timezone.now()

        resources = list_all_resources(token)

        with_details = bool(options["with_details"])
        store_raw = bool(options["store_raw"])
        all_types = bool(options["all_types"])

        if options.get("types"):
            types_for_details = {t.strip().lower() for t in options["types"].split(",") if t.strip()}
        else:
            types_for_details = {t.lower() for t in IMPORTANT_TYPES_DEFAULT}

        current_ids = set()
        upserted = 0
        detailed = 0
        errors = 0

        for r in resources:
            azure_id = r["id"]
            current_ids.add(azure_id)

            rtype = (r.get("type") or "")
            want_detail = with_details and (all_types or (rtype.lower() in types_for_details))

            component_summary = {}
            raw_detail = {}
            api_version = None

            if want_detail:
                try:
                    raw_detail, api_version = get_resource_raw_detail(token, azure_id, rtype)
                    component_summary = build_component_summary(token, raw_detail)
                    detailed += 1
                except Exception as e:
                    errors += 1
                    component_summary = {
                        "component": "error",
                        "id": azure_id,
                        "type": rtype,
                        "message": str(e),
                    }
                    raw_detail = {"error": str(e), "id": azure_id, "type": rtype} if store_raw else {}
                    api_version = api_version

            AzureResource.objects.update_or_create(
                azure_id=azure_id,
                defaults={
                    "name": r.get("name"),
                    "type": rtype,
                    "resource_group": parse_rg_from_id(azure_id),
                    "location": r.get("location"),
                    "kind": r.get("kind"),
                    "tags": r.get("tags") or {},
                    "component_summary": component_summary,
                    "raw_detail": raw_detail if store_raw else {},
                    "detail_api_version": api_version,
                    "is_deleted": False,
                    "deleted_at": None,
                },
            )
            upserted += 1

        deleted_count = (
            AzureResource.objects
            .filter(is_deleted=False)
            .exclude(azure_id__in=current_ids)
            .update(is_deleted=True, deleted_at=now)
        )

        self.stdout.write(self.style.SUCCESS(
            f"Upserted {upserted} resources. "
            f"Detailed fetched: {detailed}. Errors during detail: {errors}. "
            f"Marked deleted: {deleted_count}."
        ))
