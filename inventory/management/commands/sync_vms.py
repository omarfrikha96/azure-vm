from django.core.management.base import BaseCommand
from django.utils import timezone
from inventory.azure_arm import get_vms_inventory
from inventory.models import VirtualMachine


class Command(BaseCommand):
    help = "Fetch VM inventory from Azure ARM and upsert into database; mark missing VMs as deleted."

    def handle(self, *args, **options):
        vms = get_vms_inventory()
        now = timezone.now()

        current_ids = set()
        upserted = 0

        # 1) Upsert all currently ????? VMs (and un-delete if it was deleted before)
        for vm in vms:
            azure_id = vm["id"]
            current_ids.add(azure_id)

            VirtualMachine.objects.update_or_create(
                azure_id=azure_id,
                defaults={
                    "name": vm["name"],
                    "resource_group": vm["resourceGroup"],
                    "location": vm.get("location"),
                    "vm_size": vm.get("vmSize"),
                    "os_type": vm.get("osType"),
                    "computer_name": vm.get("computerName"),
                    "power_state": vm.get("powerState"),
                    "private_ips": vm.get("privateIPs", []),
                    "public_ips": vm.get("publicIPs", []),

                    # ? if it exists again, undelete it
                    "is_deleted": False,
                    "deleted_at": None,
                },
            )
            upserted += 1

        # 2) Mark as deleted anything we have in DB that wasn't returned by Azure
        # (only mark those not already deleted)
        deleted_count = (
            VirtualMachine.objects
            .filter(is_deleted=False)
            .exclude(azure_id__in=current_ids)
            .update(is_deleted=True, deleted_at=now)
        )

        self.stdout.write(self.style.SUCCESS(
            f"Upserted {upserted} VM(s). Marked deleted: {deleted_count} VM(s)."
        ))
