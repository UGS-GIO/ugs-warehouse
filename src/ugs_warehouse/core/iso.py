"""STAC Item → ISO 19139 (MD_Metadata) XML.

State geological surveys publish to clearinghouses (data.gov, GeoPlatform, state GIS
portals) that expect ISO 19115/19139 or FGDC, not STAC. This emits a well-formed ISO
19139 record derived from a STAC item — title, abstract, geographic + temporal extent,
CRS (from proj:code), dates, contacts, use constraints, lineage, and the item's assets as
distribution transfer options. Pure string builder (xml.sax escaping; no lxml dependency).

Curated registry values (`ugs:point_of_contact`, `ugs:use_constraints`, `ugs:lineage`,
`ugs:topic_category`) are emitted when present and OMITTED when not — never defaulted. We
republish federal data, so a substituted value is an assertion about someone else's dataset.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

ORG = "Utah Geological Survey"

NS = (
    'xmlns:gmd="http://www.isotc211.org/2005/gmd" '
    'xmlns:gco="http://www.isotc211.org/2005/gco" '
    'xmlns:gml="http://www.opengis.net/gml"'
)

CODELIST = (
    "http://standards.iso.org/ittf/PubliclyAvailableStandards/"
    "ISO_19139_Schemas/resources/Codelist/gmxCodelists.xml"
)


def _cs(v: str) -> str:
    return f"<gco:CharacterString>{escape(str(v))}</gco:CharacterString>"


def _code(kind: str, value: str) -> str:
    """An ISO codelist element — <gmd:MD_ScopeCode codeList="…#MD_ScopeCode" …>dataset</…>."""
    return (f'<gmd:{kind} codeList="{CODELIST}#{kind}" codeListValue="{value}">'
            f"{escape(value)}</gmd:{kind}>")


def _party(org: str, role: str = "pointOfContact") -> str:
    return ("<gmd:CI_ResponsibleParty>"
            f"<gmd:organisationName>{_cs(org)}</gmd:organisationName>"
            f"<gmd:role>{_code('CI_RoleCode', role)}</gmd:role>"
            "</gmd:CI_ResponsibleParty>")


def _online(href: str, name: str) -> str:
    return (
        "<gmd:onLine><gmd:CI_OnlineResource>"
        f"<gmd:linkage><gmd:URL>{escape(href)}</gmd:URL></gmd:linkage>"
        f"<gmd:name>{_cs(name)}</gmd:name>"
        "</gmd:CI_OnlineResource></gmd:onLine>"
    )


def stac_to_iso19139(item: dict) -> str:
    """Build an ISO 19139 MD_Metadata document for a STAC item."""
    props = item.get("properties", {})
    item_id = str(item.get("id", ""))
    title = str(props.get("title") or item_id)
    abstract = str(props.get("description") or title)
    dt = props.get("datetime") or "2000-01-01T00:00:00Z"
    date_only = str(dt)[:10]
    code = props.get("proj:code")  # projection ext v2.0.0, e.g. "EPSG:4326"
    bbox = item.get("bbox") or []

    geo = ""
    if len(bbox) >= 4:
        w, s, e, n = bbox[0], bbox[1], bbox[2], bbox[3]
        geo = (
            "<gmd:geographicElement><gmd:EX_GeographicBoundingBox>"
            f"<gmd:westBoundLongitude><gco:Decimal>{w}</gco:Decimal></gmd:westBoundLongitude>"
            f"<gmd:eastBoundLongitude><gco:Decimal>{e}</gco:Decimal></gmd:eastBoundLongitude>"
            f"<gmd:southBoundLatitude><gco:Decimal>{s}</gco:Decimal></gmd:southBoundLatitude>"
            f"<gmd:northBoundLatitude><gco:Decimal>{n}</gco:Decimal></gmd:northBoundLatitude>"
            "</gmd:EX_GeographicBoundingBox></gmd:geographicElement>"
        )

    crs = ""
    if code is not None:
        crs = (
            "<gmd:referenceSystemInfo><gmd:MD_ReferenceSystem><gmd:referenceSystemIdentifier>"
            f"<gmd:RS_Identifier><gmd:code>{_cs(str(code))}</gmd:code>"
            f"<gmd:codeSpace>{_cs('EPSG')}</gmd:codeSpace>"
            "</gmd:RS_Identifier></gmd:referenceSystemIdentifier>"
            "</gmd:MD_ReferenceSystem></gmd:referenceSystemInfo>"
        )

    kws = props.get("keywords") or []
    keywords_xml = ""
    if kws:
        kw_items = "".join(f"<gmd:keyword>{_cs(k)}</gmd:keyword>" for k in kws)
        keywords_xml = (
            f"<gmd:descriptiveKeywords><gmd:MD_Keywords>{kw_items}</gmd:MD_Keywords>"
            "</gmd:descriptiveKeywords>"
        )

    # Absent means absent — no default (#53). A blanket `geoscientificInformation` was rejected
    # upstream (ugs-ingest#171) because it backfills wrong values for wetlands/boundaries, and
    # asserting it here just moves that mistake downstream of the DB, into a published record a
    # harvester can't tell from a curated one. Omitting it fails ISO validation, which is the point:
    # a visible gap prompts the curation pass, a confident wrong value never does.
    topic_cat = props.get("ugs:topic_category")
    topic_xml = ""
    if topic_cat:
        topic_xml = (
            f"<gmd:topicCategory><gmd:MD_TopicCategoryCode>{escape(str(topic_cat))}"
            "</gmd:MD_TopicCategoryCode></gmd:topicCategory>"
        )

    # Use constraints — the licence/disclaimer text a republisher requires be shown before use
    # (USGS provisional products carry one). `otherRestrictions` is what makes `otherConstraints`
    # meaningful to a harvester; without it the free text has no restriction code to hang on.
    use_constraints = props.get("ugs:use_constraints")
    constraints_xml = ""
    if use_constraints:
        constraints_xml = (
            "<gmd:resourceConstraints><gmd:MD_LegalConstraints>"
            f"<gmd:useConstraints>{_code('MD_RestrictionCode', 'otherRestrictions')}"
            "</gmd:useConstraints>"
            f"<gmd:otherConstraints>{_cs(use_constraints)}</gmd:otherConstraints>"
            "</gmd:MD_LegalConstraints></gmd:resourceConstraints>"
        )

    lineage = props.get("ugs:lineage")
    quality_xml = ""
    if lineage:
        quality_xml = (
            "<gmd:dataQualityInfo><gmd:DQ_DataQuality>"
            f"<gmd:scope><gmd:DQ_Scope><gmd:level>{_code('MD_ScopeCode', 'dataset')}"
            "</gmd:level></gmd:DQ_Scope></gmd:scope>"
            f"<gmd:lineage><gmd:LI_Lineage><gmd:statement>{_cs(lineage)}</gmd:statement>"
            "</gmd:LI_Lineage></gmd:lineage>"
            "</gmd:DQ_DataQuality></gmd:dataQualityInfo>"
        )

    transfers = "".join(
        _online(a["href"], a.get("title") or k)
        for k, a in (item.get("assets") or {}).items()
        if a.get("href", "").startswith("http")
    )

    # Two different contacts, and conflating them is how we ended up claiming authorship of a USGS
    # product (#52). <gmd:contact> is who to ask about the METADATA RECORD — always us, we wrote it.
    # <gmd:pointOfContact> is who to ask about the DATASET, which for republished federal data is
    # the originating agency. Falls back to ORG only when the registry has no curated value.
    metadata_contact = _party(ORG)
    dataset_contact = _party(str(props.get("ugs:point_of_contact") or ORG))

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<gmd:MD_Metadata {NS}>"
        f"<gmd:fileIdentifier>{_cs(item_id)}</gmd:fileIdentifier>"
        f"<gmd:language>{_cs('eng')}</gmd:language>"
        f"<gmd:hierarchyLevel>{_code('MD_ScopeCode', 'dataset')}</gmd:hierarchyLevel>"
        f"<gmd:contact>{metadata_contact}</gmd:contact>"
        f"<gmd:dateStamp><gco:Date>{escape(date_only)}</gco:Date></gmd:dateStamp>"
        f"{crs}"
        "<gmd:identificationInfo><gmd:MD_DataIdentification>"
        "<gmd:citation><gmd:CI_Citation>"
        f"<gmd:title>{_cs(title)}</gmd:title>"
        "<gmd:date><gmd:CI_Date>"
        f"<gmd:date><gco:Date>{escape(date_only)}</gco:Date></gmd:date>"
        f"<gmd:dateType>{_code('CI_DateTypeCode', 'publication')}</gmd:dateType>"
        "</gmd:CI_Date></gmd:date>"
        "</gmd:CI_Citation></gmd:citation>"
        f"<gmd:abstract>{_cs(abstract)}</gmd:abstract>"
        f"<gmd:pointOfContact>{dataset_contact}</gmd:pointOfContact>"
        f"{keywords_xml}"
        f"{constraints_xml}"
        f"{topic_xml}"
        "<gmd:extent><gmd:EX_Extent>"
        f"{geo}"
        "<gmd:temporalElement><gmd:EX_TemporalExtent><gmd:extent>"
        f'<gml:TimePeriod gml:id="tp1"><gml:beginPosition>{escape(str(dt))}</gml:beginPosition>'
        f"<gml:endPosition>{escape(str(dt))}</gml:endPosition></gml:TimePeriod>"
        "</gmd:extent></gmd:EX_TemporalExtent></gmd:temporalElement>"
        "</gmd:EX_Extent></gmd:extent>"
        "</gmd:MD_DataIdentification></gmd:identificationInfo>"
        "<gmd:distributionInfo><gmd:MD_Distribution><gmd:transferOptions>"
        f"<gmd:MD_DigitalTransferOptions>{transfers}</gmd:MD_DigitalTransferOptions>"
        "</gmd:transferOptions></gmd:MD_Distribution></gmd:distributionInfo>"
        f"{quality_xml}"
        "</gmd:MD_Metadata>"
    )
