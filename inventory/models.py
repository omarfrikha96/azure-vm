from django.db import models


class VirtualMachine(models.Model):
    """
    (Optional) Your older VM-only table. You can keep it.
    The new AzureResource table below can also cover VMs.
    """
    azure_id = models.CharField(max_length=512, unique=True)

    name = models.CharField(max_length=128)
    resource_group = models.CharField(max_length=128)
    location = models.CharField(max_length=64, blank=True, null=True)

    vm_size = models.CharField(max_length=64, blank=True, null=True)
    os_type = models.CharField(max_length=32, blank=True, null=True)
    computer_name = models.CharField(max_length=128, blank=True, null=True)
    power_state = models.CharField(max_length=32, blank=True, null=True)

    private_ips = models.JSONField(default=list)
    public_ips = models.JSONField(default=list)

    last_seen = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.name} ({self.resource_group})"


class AzureResource(models.Model):
    """
    Generic inventory table for ALL Azure resources in the subscription.
    - component_summary: small, human-friendly type-specific info (VM/storage/webapp/nsg/firewall/workspace/etc.)
    - raw_detail: optional full ARM GET JSON (can be big)
    """
    azure_id = models.CharField(max_length=512, unique=True)
    name = models.CharField(max_length=256)
    resource_group = models.CharField(max_length=128, blank=True, null=True)
    type = models.CharField(max_length=256)
    location = models.CharField(max_length=64, blank=True, null=True)
    kind = models.CharField(max_length=128, blank=True, null=True)
    tags = models.JSONField(default=dict)

    component_summary = models.JSONField(default=dict, blank=True)
    raw_detail = models.JSONField(default=dict, blank=True)
    detail_api_version = models.CharField(max_length=32, blank=True, null=True)

    last_seen = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.type} :: {self.name}"