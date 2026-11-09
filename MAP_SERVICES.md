# Online map services

The source edition works without a MosaicEarth account. Local projects and local
processing do not require an Esri account or a map subscription. Detailed online
basemaps require an Internet connection and remain subject to their providers'
terms; they are not part of the AGPL-licensed application or its downloadable data.

## Satellite imagery

The shared web/Desktop viewer uses Esri World Imagery for global detail and
PNOA/IGN at closer zooms in Spain. Capture dates, resolution and positional
accuracy vary by location. A contextual basemap is not survey evidence.

The default MosaicEarth service issues an individual Esri basemap session, valid
for up to 12 hours, without receiving a project file or requiring a cloud login.
The client loads imagery directly from Esri using that temporary session token.
There is no permanent Esri application key in the repository or installers and no
shared proxy/cache of Esri imagery. Session tokens are bearer credentials; do not
publish browser network traces containing them.

When the detailed service is unavailable or its allowance is exhausted, the
viewer displays a notice and uses the lower-resolution EOX Sentinel 2016 mosaic.
Existing local projects remain accessible. The viewer retries temporary failures
and replaces the fallback imagery after recovery. Topographic uses OpenStreetMap.

Keep the visible “Powered by Esri” and data-source credits. See the [full map
notices](https://app.mosaicearth.app/map-sources) and [ArcGIS Location Platform
terms](https://www.esri.com/content/dam/esrisites/en-us/media/legal/platform/platform-legal.pdf).
MosaicEarth does not provide bulk/offline downloads of these basemaps.

## Configuring a source deployment

These are environment variables for the **platform container**, not the shell
launcher. Restart/recreate that container after a configuration change:

| Variable | Meaning |
| --- | --- |
| `MOSAICEARTH_MAP_OPEN_ONLY=1` | Use EOX/PNOA and OSM without the Esri session service. |
| `MOSAICEARTH_MAP_SESSION_URL` | Your own compatible session endpoint; defaults to `https://app.mosaicearth.app/api/v2/maps/session`. |

For a fork/distributed derivative, choose open-only imagery or operate your own
licensed service. MosaicEarth's hosted allowance is shared and is not an SLA or
an unlimited redistribution entitlement.

## Operating a session broker

The shared FastAPI application contains the optional broker. Configure it only on
a trusted server; never distribute these credentials with a Desktop package:

| Private server variable | Meaning |
| --- | --- |
| `MOSAICEARTH_ESRI_CLIENT_ID` | Private OAuth application's client ID. |
| `MOSAICEARTH_ESRI_CLIENT_SECRET` | Private OAuth application's secret. |
| `MOSAICEARTH_ESRI_MAX_SESSIONS` | Maximum reserved sessions per billing period; default 1,000. |
| `MOSAICEARTH_ESRI_BILLING_DAY` | UTC anniversary day, from 1 to 28; default 1. Match your account. |
| `MOSAICEARTH_MAP_ALLOWED_ORIGINS` | Optional comma-separated exact HTTPS application origins. |

Grant only the Basemap Styles privilege (`premium:user:basemaps`), with no account
content or administrator permissions. Check Esri's current pricing and terms
before setting a budget. The local limit controls sessions issued by this broker;
it does not control other applications or charges in the Esri account.

The persistent ledger is `MOSAICEARTH_DATA_DIR/runtime/maps/sessions.sqlite3` with
owner-only file permissions. Share this same ledger across any staging/production
instances using the same budget. Never reset or restore an older ledger during a
code rollback: its reservations protect against exceeding the budget. Unknown
outcomes (including timeouts) remain counted. Existing valid sessions can be
returned after the limit is reached; new ones are refused. Both the configured
billing anniversary and calendar-month totals are bounded conservatively.

The anonymous endpoint restricts browser origins and rate-limits new sessions,
but origin checks are not authentication against non-browser callers. The global
budget remains the financial protection. The ledger stores hashed random client
identifiers and daily pseudonymous connection identifiers, not viewed coordinates
or account identities. No OAuth application access token is returned to clients.

## Terrain

Re:Earth/Mapterhorn quantized meshes, the MosaicEarth terrain gateway and bundled
NOAA global/polar fallbacks are independent of the satellite session service.
The globe retains terrain when detailed imagery is unavailable. Terrain and
basemaps are for visualization and do not change project coordinate systems.
