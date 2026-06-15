"""STAC Item → ISO 19139 (MD_Metadata) XML.

State geological surveys publish to clearinghouses (data.gov, GeoPlatform, state GIS
portals) that expect ISO 19115/19139 or FGDC, not STAC. This emits a well-formed ISO
19139 record derived from a STAC item — title, abstract, geographic + temporal extent,
CRS (from proj:epsg), dates, a UGS contact, and the item's assets as distribution
transfer options. Pure string builder (xml.sax escaping; no lxml dependency).
"""
from __future__ import annotations

from xml.sax.saxutils import escape

ORG = "Utah Geological Survey"

NS = (
    'xmlns:gmd="http://www.isotc211.org/2005/gmd" '
    'xmlns:gco="http://www.isotc211.org/2005/gco" '
    'xmlns:gml="http://www.opengis.net/gml"'
)


def _cs(v: str) -> str:
    return f"<gco:CharacterString>{escape(str(v))}</gco:CharacterString>"


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
    epsg = props.get("proj:epsg")
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
    if epsg is not None:
        crs = (
            "<gmd:referenceSystemInfo><gmd:MD_ReferenceSystem><gmd:referenceSystemIdentifier>"
            f"<gmd:RS_Identifier><gmd:code>{_cs(f'EPSG:{epsg}')}</gmd:code>"
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

    topic_cat = props.get("ugs:topic_category") or "geoscientificInformation"
    topic_xml = (
        f"<gmd:topicCategory><gmd:MD_TopicCategoryCode>{escape(str(topic_cat))}"
        "</gmd:MD_TopicCategoryCode></gmd:topicCategory>"
    )

    transfers = "".join(
        _online(a["href"], a.get("title") or k)
        for k, a in (item.get("assets") or {}).items()
        if a.get("href", "").startswith("http")
    )

    contact = (
        "<gmd:CI_ResponsibleParty>"
        f"<gmd:organisationName>{_cs(ORG)}</gmd:organisationName>"
        '<gmd:role><gmd:CI_RoleCode codeList="http://standards.iso.org/ittf/'
        'PubliclyAvailableStandards/ISO_19139_Schemas/resources/Codelist/gmxCodelists.xml'
        '#CI_RoleCode" codeListValue="pointOfContact">pointOfContact</gmd:CI_RoleCode></gmd:role>'
        "</gmd:CI_ResponsibleParty>"
    )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<gmd:MD_Metadata {NS}>"
        f"<gmd:fileIdentifier>{_cs(item_id)}</gmd:fileIdentifier>"
        f"<gmd:language>{_cs('eng')}</gmd:language>"
        '<gmd:hierarchyLevel><gmd:MD_ScopeCode codeListValue="dataset" '
        'codeList="http://standards.iso.org/ittf/PubliclyAvailableStandards/'
        'ISO_19139_Schemas/resources/Codelist/gmxCodelists.xml#MD_ScopeCode">dataset'
        "</gmd:MD_ScopeCode></gmd:hierarchyLevel>"
        f"<gmd:contact>{contact}</gmd:contact>"
        f"<gmd:dateStamp><gco:Date>{escape(date_only)}</gco:Date></gmd:dateStamp>"
        f"{crs}"
        "<gmd:identificationInfo><gmd:MD_DataIdentification>"
        "<gmd:citation><gmd:CI_Citation>"
        f"<gmd:title>{_cs(title)}</gmd:title>"
        "<gmd:date><gmd:CI_Date>"
        f"<gmd:date><gco:Date>{escape(date_only)}</gco:Date></gmd:date>"
        '<gmd:dateType><gmd:CI_DateTypeCode codeListValue="publication" '
        'codeList="http://standards.iso.org/ittf/PubliclyAvailableStandards/'
        'ISO_19139_Schemas/resources/Codelist/gmxCodelists.xml#CI_DateTypeCode">publication'
        "</gmd:CI_DateTypeCode></gmd:dateType>"
        "</gmd:CI_Date></gmd:date>"
        "</gmd:CI_Citation></gmd:citation>"
        f"<gmd:abstract>{_cs(abstract)}</gmd:abstract>"
        f"<gmd:pointOfContact>{contact}</gmd:pointOfContact>"
        f"{keywords_xml}"
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
        "</gmd:MD_Metadata>"
    )
