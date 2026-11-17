"""Pure parsing / derived-value functions for the Sia + Plex dashboard.

Everything in this module is side-effect free: it takes decoded JSON payloads
(as returned by hostd, Siascan or Plex) and returns plain dicts of normalised
values. All of the "derived math" (hastings -> SC, sectors -> bytes,
per-block -> per-month prices, sync deltas) lives here so it can be unit
tested against fixtures.

The upstream JSON shapes are the ones documented in
~/workspace/research_notes/sia-host-dashboard-research-20261117-1635/report.md
(hostd v2 Go API client structs). Because the rendered OpenAPI spec could not
be cross-checked during research, every parser here is deliberately tolerant:
keys are looked up case-insensitively, currency values may arrive as JSON
numbers or decimal strings, and optional blocks may be nested or flat.
"""

from decimal import Decimal, InvalidOperation

HASTINGS_PER_SC = Decimal(10) ** 24
SECTOR_BYTES = 4 * 1024 * 1024          # one Sia sector = 4 MiB
BLOCKS_PER_MONTH = 4320                 # ~144 blocks/day * 30 days


# --------------------------------------------------------------------------
# small tolerant helpers
# --------------------------------------------------------------------------

def _get(d, *names, default=None):
    """Case-insensitive dict lookup trying several key spellings."""
    if not isinstance(d, dict):
        return default
    lowered = {str(k).lower(): v for k, v in d.items()}
    for name in names:
        if name.lower() in lowered and lowered[name.lower()] is not None:
            return lowered[name.lower()]
    return default


def _num(value, default=None):
    """Coerce an int/float/numeric-string to int when whole, else float."""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        try:
            dec = Decimal(value.strip())
        except InvalidOperation:
            return default
        return int(dec) if dec == dec.to_integral_value() else float(dec)
    return default


def _currency_sc(value):
    """Convert a Sia currency value (hastings) to SC as a float.

    hostd serialises types.Currency as a decimal string of hastings, but we
    also accept plain JSON numbers. Returns None when unparseable.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return float(dec / HASTINGS_PER_SC)


def _bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return default


# --------------------------------------------------------------------------
# derived math (unit tested)
# --------------------------------------------------------------------------

def sectors_to_bytes(sectors):
    """Sectors -> bytes (1 sector = 4 MiB). None passes through."""
    if sectors is None:
        return None
    return int(sectors) * SECTOR_BYTES


def storage_price_sc_per_tb_month(price_hastings_per_tb_block):
    """hostd/Siascan storage prices are quoted per TB per block.

    Per-month price in SC = per-block price (SC) * 4320 blocks/month.
    """
    sc = _currency_sc(price_hastings_per_tb_block)
    if sc is None:
        return None
    return sc * BLOCKS_PER_MONTH


def price_sc_per_tb(price_hastings_per_tb):
    """Ingress/egress prices are quoted per TB outright -> SC per TB."""
    return _currency_sc(price_hastings_per_tb)


def compute_sync(consensus_height, index_height, network_height):
    """Build the 'am I synced?' traffic-light view.

    Returns deltas of the local tips against the Siascan network height and
    a status: 'synced' (within 2 blocks), 'catching_up' (within 10),
    'stalled' (further behind / tips disagree) or 'unknown' (missing data).
    """
    result = {
        "consensus_height": consensus_height,
        "index_height": index_height,
        "network_height": network_height,
        "delta_consensus": None,
        "delta_index": None,
        "status": "unknown",
    }
    if consensus_height is None or index_height is None:
        return result
    if network_height is not None:
        result["delta_consensus"] = consensus_height - network_height
        result["delta_index"] = index_height - network_height
        worst = max(abs(result["delta_consensus"]), abs(result["delta_index"]))
    else:
        worst = abs(consensus_height - index_height)
    if abs(consensus_height - index_height) > 2:
        result["status"] = "stalled" if worst > 10 else "catching_up"
    elif worst <= 2:
        result["status"] = "synced"
    elif worst <= 10:
        result["status"] = "catching_up"
    else:
        result["status"] = "stalled"
    return result


# --------------------------------------------------------------------------
# hostd parsers
# --------------------------------------------------------------------------

def parse_state(payload):
    """GET /state -> identity / version / start time / last announcement."""
    if not isinstance(payload, dict):
        return {}
    ann = _get(payload, "lastAnnouncement", "last_announcement", default={})
    return {
        "name": _get(payload, "name"),
        "public_key": _get(payload, "publicKey", "public_key"),
        "version": _get(payload, "version"),
        "commit": _get(payload, "commit"),
        "os": _get(payload, "os"),
        "start_time": _get(payload, "startTime", "start_time"),
        "last_announcement_address": _get(ann, "address"),
        "last_announcement_height": _num(_get(ann, "height")),
    }


def _revenue_split(block):
    if not isinstance(block, dict):
        return {"rpc": None, "storage": None, "ingress": None, "egress": None,
                "total": None}
    split = {
        "rpc": _currency_sc(_get(block, "rpc")),
        "storage": _currency_sc(_get(block, "storage")),
        "ingress": _currency_sc(_get(block, "ingress")),
        "egress": _currency_sc(_get(block, "egress")),
    }
    parts = [v for v in split.values() if v is not None]
    split["total"] = sum(parts) if parts else None
    return split


def parse_metrics(payload):
    """GET /metrics -> revenue, contracts, storage aggregates, data, wallet."""
    if not isinstance(payload, dict):
        return {}
    revenue = _get(payload, "revenue", default={})
    contracts = _get(payload, "contracts", default={})
    storage = _get(payload, "storage", default={})
    data = _get(payload, "data", default={})
    rhp = _get(data, "rhp", default={})
    syncer = _get(data, "syncer", default={})
    wallet = _get(payload, "wallet", default={})

    total_sectors = _num(_get(storage, "totalSectors", "total_sectors"))
    physical = _num(_get(storage, "physicalSectors", "physical_sectors"))
    contract_s = _num(_get(storage, "contractSectors", "contract_sectors"))
    temp_s = _num(_get(storage, "tempSectors", "temp_sectors"))
    return {
        "revenue": {
            "potential": _revenue_split(_get(revenue, "potential", default={})),
            "earned": _revenue_split(_get(revenue, "earned", default={})),
        },
        "contracts": {
            "active": _num(_get(contracts, "active")),
            "successful": _num(_get(contracts, "successful")),
            "renewed": _num(_get(contracts, "renewed")),
            "rejected": _num(_get(contracts, "rejected")),
            "failed": _num(_get(contracts, "failed")),
            "locked_collateral_sc": _currency_sc(
                _get(contracts, "lockedCollateral", "locked_collateral")),
            "risked_collateral_sc": _currency_sc(
                _get(contracts, "riskedCollateral", "risked_collateral")),
        },
        "storage": {
            "total_sectors": total_sectors,
            "physical_sectors": physical,
            "contract_sectors": contract_s,
            "temp_sectors": temp_s,
            "lost_sectors": _num(_get(storage, "lostSectors", "lost_sectors")),
            "total_bytes": sectors_to_bytes(total_sectors),
            "physical_bytes": sectors_to_bytes(physical),
            "contract_bytes": sectors_to_bytes(contract_s),
            "temp_bytes": sectors_to_bytes(temp_s),
            "reads": _num(_get(storage, "reads")),
            "writes": _num(_get(storage, "writes")),
            "read_bytes": _num(_get(storage, "readBytes", "read_bytes")),
            "write_bytes": _num(_get(storage, "writeBytes", "write_bytes")),
        },
        "data": {
            "rhp_ingress_bytes": _num(_get(rhp, "ingress")),
            "rhp_egress_bytes": _num(_get(rhp, "egress")),
            "syncer_ingress_bytes": _num(_get(syncer, "ingress")),
            "syncer_egress_bytes": _num(_get(syncer, "egress")),
        },
        "wallet_balance_sc": _currency_sc(_get(wallet, "balance")),
        "wallet_immature_sc": _currency_sc(
            _get(wallet, "immatureBalance", "immature_balance")),
        "pricing": parse_pricing(payload),
        "timestamp": _get(payload, "timestamp"),
    }


def parse_pricing(container):
    """Extract a pricing block from /settings or /metrics payloads.

    Prices may sit at the top level or under a "pricing"/"prices" key.
    Returns raw hastings values plus converted SC figures.
    """
    if not isinstance(container, dict):
        return {}
    block = _get(container, "pricing", "prices", default=None)
    if not isinstance(block, dict):
        block = container
    storage_raw = _get(block, "storagePrice", "storage_price")
    ingress_raw = _get(block, "ingressPrice", "ingress_price")
    egress_raw = _get(block, "egressPrice", "egress_price")
    if storage_raw is None and ingress_raw is None and egress_raw is None:
        return {}
    return {
        "contract_sc": _currency_sc(
            _get(block, "contractPrice", "contract_price")),
        "storage_sc_per_tb_month": storage_price_sc_per_tb_month(storage_raw),
        "ingress_sc_per_tb": price_sc_per_tb(ingress_raw),
        "egress_sc_per_tb": price_sc_per_tb(egress_raw),
        "base_rpc_sc": _currency_sc(
            _get(block, "baseRPCPrice", "base_rpc_price", "baseRpcPrice")),
        "sector_access_sc": _currency_sc(
            _get(block, "sectorAccessPrice", "sector_access_price")),
        "collateral_multiplier": _num(
            _get(block, "collateralMultiplier", "collateral_multiplier")),
    }


def parse_settings(payload):
    """GET /settings -> accepting flag + pricing block."""
    if not isinstance(payload, dict):
        return {}
    return {
        "accepting_contracts": _bool(
            _get(payload, "acceptingContracts", "accepting_contracts"),
            default=None),
        "net_address": _get(payload, "netAddress", "net_address"),
        "max_contract_duration_blocks": _num(
            _get(payload, "maxContractDuration", "max_contract_duration")),
        "max_collateral_sc": _currency_sc(
            _get(payload, "maxCollateral", "max_collateral")),
        "pricing": parse_pricing(payload),
    }


def parse_volumes(payload):
    """GET /volumes -> per-volume normalised list + aggregates.

    Volume stats (read/write counters) may be flat on the volume object or
    nested under a "stats" key; both shapes are handled.
    """
    if isinstance(payload, dict):
        payload = _get(payload, "volumes", default=[])
    volumes = []
    if not isinstance(payload, list):
        return {"volumes": volumes, "used_bytes": 0, "total_bytes": 0,
                "available_total_bytes": 0, "unavailable_count": 0}
    used_bytes = 0
    total_bytes = 0
    available_total_bytes = 0
    unavailable_count = 0
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        stats = _get(raw, "stats", default={})
        if not isinstance(stats, dict):
            stats = {}

        def field(*names):
            value = _get(raw, *names)
            return value if value is not None else _get(stats, *names)

        used_s = _num(field("usedSectors", "used_sectors"))
        total_s = _num(field("totalSectors", "total_sectors"))
        available = _bool(field("available"), default=True)
        status = field("status")
        if not status:
            status = "ready" if available else "unavailable"
        if not available or str(status).lower() == "unavailable":
            unavailable_count += 1
        used_b = sectors_to_bytes(used_s) or 0
        total_b = sectors_to_bytes(total_s) or 0
        used_bytes += used_b
        total_bytes += total_b
        if available:
            available_total_bytes += total_b
        errors = field("errors") or []
        if isinstance(errors, str):
            errors = [errors]
        volumes.append({
            "id": _num(field("id")),
            "path": field("localPath", "local_path", "path"),
            "used_sectors": used_s,
            "total_sectors": total_s,
            "used_bytes": used_b,
            "total_bytes": total_b,
            "pct_full": (100.0 * used_b / total_b) if total_b else 0.0,
            "read_only": _bool(field("readOnly", "read_only")),
            "available": available,
            "status": status,
            "successful_reads": _num(field("successfulReads", "successful_reads")),
            "failed_reads": _num(field("failedReads", "failed_reads")),
            "successful_writes": _num(field("successfulWrites", "successful_writes")),
            "failed_writes": _num(field("failedWrites", "failed_writes")),
            "errors": errors,
        })
    return {
        "volumes": volumes,
        "used_bytes": used_bytes,
        "total_bytes": total_bytes,
        "available_total_bytes": available_total_bytes,
        "unavailable_count": unavailable_count,
    }


def parse_wallet(payload):
    """GET /wallet -> spendable/confirmed/unconfirmed/immature in SC."""
    if not isinstance(payload, dict):
        return {}
    return {
        "spendable_sc": _currency_sc(_get(payload, "spendable")),
        "confirmed_sc": _currency_sc(_get(payload, "confirmed")),
        "unconfirmed_sc": _currency_sc(_get(payload, "unconfirmed")),
        "immature_sc": _currency_sc(_get(payload, "immature")),
    }


def parse_alerts(payload):
    """GET /alerts -> list + counts by severity."""
    if isinstance(payload, dict):
        payload = _get(payload, "alerts", default=[])
    alerts = []
    counts = {}
    if isinstance(payload, list):
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            severity = str(_get(raw, "severity", default="info")).lower()
            counts[severity] = counts.get(severity, 0) + 1
            alerts.append({
                "id": _get(raw, "id"),
                "severity": severity,
                "message": _get(raw, "message"),
                "timestamp": _get(raw, "timestamp", "time"),
            })
    return {"alerts": alerts, "counts": counts, "total": len(alerts)}


def parse_tip(payload):
    """GET /consensus/tip or /index/tip -> height (int) or None."""
    if not isinstance(payload, dict):
        return None
    return _num(_get(payload, "height"))


def parse_peers(payload):
    """GET /syncer/peers -> peer count."""
    if isinstance(payload, dict):
        payload = _get(payload, "peers", default=[])
    if isinstance(payload, list):
        return {"peer_count": len(payload)}
    return {"peer_count": None}


# --------------------------------------------------------------------------
# Siascan parsers
# --------------------------------------------------------------------------

def _find_prices_block(container, depth=0):
    """Locate a dict that carries storage/ingress/egress price fields.

    Siascan's HostMetrics nests host settings (v2 settings carry a "prices"
    sub-block); search the likely nests in priority order, then shallowly
    recurse so small shape changes don't break the panel.
    """
    if not isinstance(container, dict) or depth > 3:
        return None
    if (_get(container, "storagePrice", "storage_price") is not None or
            _get(container, "ingressPrice", "ingress_price") is not None or
            _get(container, "egressPrice", "egress_price") is not None):
        return container
    for key in ("prices", "pricing", "v2Settings", "v2_settings",
                "settings", "priceTable", "price_table",
                "averageSettings", "medianSettings", "median_settings"):
        sub = _get(container, key)
        found = _find_prices_block(sub, depth + 1)
        if found is not None:
            return found
    return None


def parse_siascan_host(payload):
    """Siascan HostMetrics -> network context + median prices."""
    if not isinstance(payload, dict):
        return {}
    total = _num(_get(payload, "totalStorage", "total_storage"))
    remaining = _num(_get(payload, "remainingStorage", "remaining_storage"))
    used = None
    if total is not None and remaining is not None:
        used = total - remaining
    prices = parse_pricing(_find_prices_block(payload) or {})
    return {
        "active_hosts": _num(_get(payload, "activeHosts", "active_hosts")),
        "total_storage_bytes": total,
        "remaining_storage_bytes": remaining,
        "used_storage_bytes": used,
        "median_storage_sc_per_tb_month": prices.get("storage_sc_per_tb_month"),
        "median_ingress_sc_per_tb": prices.get("ingress_sc_per_tb"),
        "median_egress_sc_per_tb": prices.get("egress_sc_per_tb"),
    }


def parse_siascan_block(payload):
    """Siascan block Metrics -> network height + contract counts."""
    if not isinstance(payload, dict):
        return {}
    index = _get(payload, "index", default={})
    height = _num(_get(index, "height"))
    if height is None:
        height = _num(_get(payload, "height"))
    return {
        "network_height": height,
        "total_hosts": _num(_get(payload, "totalHosts", "total_hosts")),
        "active_contracts": _num(
            _get(payload, "activeContracts", "active_contracts")),
        "failed_contracts": _num(
            _get(payload, "failedContracts", "failed_contracts")),
        "successful_contracts": _num(
            _get(payload, "successfulContracts", "successful_contracts")),
    }


def parse_exchange_rate(payload, currency="usd"):
    """Siascan ExchangeRate -> value of 1 SC in the given fiat currency.

    The explored API returns a bare JSON number; dict shapes are tolerated.
    """
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        return float(payload)
    if isinstance(payload, dict):
        for key in ("rate", "value", "price", currency.lower(),
                    currency.upper(), "usd"):
            value = _num(_get(payload, key))
            if value is not None:
                return float(value)
    if isinstance(payload, str):
        value = _num(payload)
        return float(value) if value is not None else None
    return None


# --------------------------------------------------------------------------
# Plex parsers
# --------------------------------------------------------------------------

def _media_container(payload):
    if isinstance(payload, dict):
        container = _get(payload, "MediaContainer", "mediacontainer")
        if isinstance(container, dict):
            return container
    return {}


def parse_plex_identity(payload):
    """GET / on the Plex server -> name / version / platform."""
    container = _media_container(payload)
    return {
        "name": _get(container, "friendlyName", "friendly_name"),
        "version": _get(container, "version"),
        "platform": _get(container, "platform"),
        "machine_id": _get(container, "machineIdentifier", "machine_id"),
    }


def _session_mode(item):
    """Direct Play vs Direct Stream vs Transcode for one session item."""
    if isinstance(_get(item, "TranscodeSession", "transcodesession"), dict):
        return "Transcode"
    media = _get(item, "Media", "media", default=[])
    if isinstance(media, list) and media:
        parts = _get(media[0], "Part", "part", default=[])
        if isinstance(parts, list) and parts:
            decision = str(_get(parts[0], "decision", default="")).lower()
            if decision == "transcode":
                return "Transcode"
            if decision == "copy":
                return "Direct Stream"
            if decision == "directplay":
                return "Direct Play"
    return "Direct Play"


def parse_plex_sessions(payload):
    """GET /status/sessions -> now-playing list + total bandwidth (Mbps)."""
    container = _media_container(payload)
    items = _get(container, "Metadata", "metadata", default=[])
    sessions = []
    total_kbps = 0
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            user = _get(item, "User", "user", default={})
            player = _get(item, "Player", "player", default={})
            session = _get(item, "Session", "session", default={})
            bandwidth = _num(_get(session, "bandwidth")) or 0
            total_kbps += bandwidth
            title = _get(item, "title")
            parent = _get(item, "grandparentTitle", "grandparent_title")
            if parent and title:
                title = "%s — %s" % (parent, title)
            sessions.append({
                "user": _get(user, "title"),
                "title": title,
                "type": _get(item, "type"),
                "client": _get(player, "title"),
                "device": _get(player, "device", default=None) or
                          _get(player, "product"),
                "state": _get(player, "state"),
                "mode": _session_mode(item),
                "bandwidth_kbps": bandwidth,
                "location": _get(session, "location"),
            })
    return {
        "sessions": sessions,
        "count": len(sessions),
        "bandwidth_mbps": round(total_kbps / 1000.0, 2),
    }


def parse_plex_sections(payload):
    """GET /library/sections -> section list (counts filled in separately)."""
    container = _media_container(payload)
    dirs = _get(container, "Directory", "directory", default=[])
    sections = []
    if isinstance(dirs, list):
        for entry in dirs:
            if not isinstance(entry, dict):
                continue
            sections.append({
                "key": _get(entry, "key"),
                "title": _get(entry, "title"),
                "type": _get(entry, "type"),
                "count": None,
            })
    return sections


def parse_plex_section_count(payload):
    """GET /library/sections/:id/all?X-Plex-Container-Size=0 -> totalSize."""
    container = _media_container(payload)
    return _num(_get(container, "totalSize", "totalsize"))
