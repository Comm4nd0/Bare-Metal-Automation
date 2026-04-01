"""Django views for the fleet compliance app."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from .models import FleetScan, SiteRecord, TemplateRecord


def fleet_index(request: HttpRequest) -> HttpResponse:
    """Fleet compliance landing — list all sites grouped by template."""
    sites = SiteRecord.objects.select_related("last_deployment").order_by("site_name")
    templates = TemplateRecord.objects.order_by("name")
    latest_scan = FleetScan.objects.order_by("-scanned_at").first()

    # Group sites by template name
    template_groups: dict[str, list[SiteRecord]] = {}
    for site in sites:
        template_name = (
            site.last_deployment.template_name if site.last_deployment else "Unknown"
        )
        template_groups.setdefault(template_name, []).append(site)

    return render(
        request,
        "fleet/index.html",
        {
            "sites": sites,
            "templates": templates,
            "template_groups": template_groups,
            "latest_scan": latest_scan,
        },
    )


def site_detail(request: HttpRequest, site_slug: str) -> HttpResponse:
    """Detail view for a single site — deployment history and compliance."""
    site = get_object_or_404(SiteRecord, site_slug=site_slug)
    compliance_records = site.compliance_records.select_related("scan").order_by("-scan__scanned_at")

    return render(
        request,
        "fleet/site_detail.html",
        {
            "site": site,
            "compliance_records": compliance_records,
        },
    )


def scan_detail(request: HttpRequest, scan_id: int) -> HttpResponse:
    """Detail view for a single fleet scan."""
    scan = get_object_or_404(FleetScan, pk=scan_id)
    results = scan.site_results.select_related("site").order_by("site__site_name")

    return render(
        request,
        "fleet/scan_detail.html",
        {
            "scan": scan,
            "results": results,
        },
    )
