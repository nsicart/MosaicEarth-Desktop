import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .blob_store import get_blob_store
from .db_store import get_store
from .email_service import get_email_service
from .seo_engine import get_public_site_url


def run_worker_loop(poll_seconds: int = 10) -> None:
    print("[MosaicEarth Linux Worker] Starting background job processor...", flush=True)
    store = get_store()
    blob_store = get_blob_store()
    email_svc = get_email_service()
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    prepare_model_script = scripts_dir / "prepare-model-viewer.py"

    while True:
        try:
            # Reload shared SQLite overlay so jobs queued by mosaicearth-web are visible immediately
            with store.lock:
                store._init_sqlite_overlay()

            # Resume verified paid tasks and confirm cash refunds after restarts.
            try:
                import asyncio
                import sqlite3
                from .app import _dispatch_stripe_processing
                from .stripe_payments import reconcile_processing_refunds
                with sqlite3.connect(store.sqlite_path, timeout=30) as db:
                    paid_tasks = db.execute("SELECT session_id FROM stripe_processing_outbox WHERE dispatched=0").fetchall()
                for (session_id,) in paid_tasks:
                    try:
                        _dispatch_stripe_processing(session_id)
                    except Exception as dispatch_exc:
                        print(f"[MosaicEarth Worker] Paid task dispatch will retry: {type(dispatch_exc).__name__}", flush=True)
                asyncio.run(reconcile_processing_refunds(store))
            except Exception as settlement_exc:
                print(f"[MosaicEarth Worker] Payment reconciliation will retry: {type(settlement_exc).__name__}", flush=True)

            # 0. Process Unified Persistent Background Job Queue (dbo.MOSAICEARTH_BACKGROUND_JOBS: Heavy GIS Imports & 3D Gaussian Splatting)
            try:
                # Reuse bounded queue threads so long jobs cannot starve
                # payment reconciliation, Node polling or Pod expiry.
                store.get_job_queue().ensure_workers_started()
            except Exception as bg_exc:
                print(f"[MosaicEarth Worker] BackgroundJobQueue warning: {bg_exc}", file=sys.stderr, flush=True)

            # 0b. Reconcile stale RunPod leases & shut down idle RunPod pods after grace window
            try:
                store.runpod_engine_jobs().reap()
                store.get_runpod_lease_manager().terminate_pod_if_idle()
            except Exception as rp_exc:
                print(f"[MosaicEarth Worker] RunPodLeaseManager warning: {rp_exc}", file=sys.stderr, flush=True)

            # 1. Process external asset preparation jobs (delegated to BackgroundJobQueueManager to prevent duplicate execution)
            jobs = store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSET_JOBS", [])
            for job in jobs:
                status = str(job.get("STATUS") or "").upper()
                if status in ("QUEUED", "PENDING"):
                    job_id = int(job.get("ID") or 0)
                    asset_id = int(job.get("ASSET_ID") or 0)
                    proj_id = int(job.get("PROJECT_ID") or 0)
                    existing_bg_job = store.get_job_queue().get_job(asset_id=asset_id)
                    if existing_bg_job:
                        bg_st = str(existing_bg_job.get("STATUS") or "").upper()
                        if bg_st == "COMPLETED":
                            job["STATUS"] = "READY"
                            job["FINISHED_AT_UTC"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                            store.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSET_JOBS")
                        elif bg_st == "FAILED":
                            job["STATUS"] = "FAILED"
                            job["LAST_ERROR"] = str(existing_bg_job.get("LAST_ERROR") or "Background job failed")
                            job["FINISHED_AT_UTC"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                            store.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSET_JOBS")
                        # Active or queued in BackgroundJobQueueManager -> skip legacy CLI runner so it never runs twice!
                        continue
                    print(f"[MosaicEarth Worker] Processing ExternalAssetJob #{job_id} (project={proj_id}, asset={asset_id})", flush=True)
                    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    job["STATUS"] = "PROCESSING"
                    job["STARTED_AT_UTC"] = now_iso
                    store.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSET_JOBS")

                    ext_row = None
                    for row in store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                        if int(row.get("ID") or 0) == asset_id:
                            ext_row = row
                            break

                    if ext_row:
                        rel_path = str(ext_row.get("STORAGE_RELATIVE_PATH") or "")
                        src_file = blob_store.ensure_local("assets", rel_path, force_download=True)
                        if src_file and src_file.is_file():
                            out_dir = blob_store.blobs_root / "results" / "external" / str(proj_id) / str(asset_id)
                            out_dir.mkdir(parents=True, exist_ok=True)
                            ext_sfx = src_file.suffix.lower()
                            prepare_mjs = scripts_dir / "prepare-external-asset.mjs"
                            if ext_sfx in (".obj", ".fbx", ".glb", ".gltf") and prepare_model_script.is_file():
                                cmd = [
                                    sys.executable,
                                    str(prepare_model_script),
                                    "--input-dir",
                                    str(src_file.parent),
                                    "--output-dir",
                                    str(out_dir),
                                    "--entry",
                                    src_file.name,
                                    "--toktx",
                                    "toktx",
                                ]
                            elif prepare_mjs.is_file():
                                cmd = [
                                    "node",
                                    str(prepare_mjs),
                                    "--input",
                                    str(src_file),
                                    "--output",
                                    str(out_dir),
                                    "--format",
                                    ext_sfx.lstrip("."),
                                ]
                            else:
                                cmd = ["cp", "-f", str(src_file), str(out_dir / src_file.name)]
                            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                            has_out = any(out_dir.rglob("*"))
                            if res.returncode == 0 and has_out:
                                job["STATUS"] = "READY"
                                ext_row["STATUS"] = "private-ready"
                                ext_row["STATUS_DETAIL"] = "Ready for private inspection and placement review."
                                if (out_dir / "tileset.json").is_file():
                                    ext_row["PREVIEW_RELATIVE_PATH"] = f"external/{proj_id}/{asset_id}/tileset.json"
                                if blob_store.is_zero_local_disk():
                                    blob_store.sync_folder_to_cloud("results", f"external/{proj_id}/{asset_id}", delete_local=True)
                                    blob_store.sync_folder_to_cloud("assets", f"external/{proj_id}", delete_local=True)
                            else:
                                job["STATUS"] = "FAILED"
                                job["LAST_ERROR"] = (res.stderr or res.stdout or "Converter produced no valid output")[:500]
                                ext_row["STATUS"] = "failed"
                                ext_row["STATUS_DETAIL"] = job["LAST_ERROR"]
                        else:
                            job["STATUS"] = "FAILED"
                            job["LAST_ERROR"] = f"Source file not found on disk: {rel_path}"
                            ext_row["STATUS"] = "failed"
                            ext_row["STATUS_DETAIL"] = job["LAST_ERROR"]
                    else:
                        job["STATUS"] = "FAILED"
                        job["LAST_ERROR"] = f"External asset #{asset_id} record not found"

                    job["FINISHED_AT_UTC"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    store.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSET_JOBS")
                    store.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSETS")

                    if job["STATUS"] == "FAILED" and proj_id > 0:
                        store.refund_task_on_failure(proj_id, reason=str(job.get("LAST_ERROR") or "External asset job failed"))

                    if job["STATUS"] == "READY" and ext_row:
                        owner_id = int(ext_row.get("USER_ID") or 0)
                        title = str(ext_row.get("DISPLAY_NAME") or ext_row.get("TITLE") or f"Asset {asset_id}")
                        store.add_notification(
                            owner_id,
                            "Processing complete",
                            f"Your MosaicAsset '{title}' is ready for review.",
                            link_id=proj_id,
                            type_id=1,
                        )
                        owner = store.get_user_by_id(owner_id)
                        if owner and owner.get("EMAIL"):
                            url = f"{get_public_site_url()}/Client/ExternalAssetDetail.aspx?project_id={proj_id}&asset_id={asset_id}"
                            email_svc.send_project_completed_email(
                                recipient=str(owner["EMAIL"]),
                                project_name=title,
                                project_url=url,
                                is_imported=True,
                                idempotency_key=f"external-asset-complete:{asset_id}",
                            )

            # 2. Check Gaussian Splatting jobs — strict guard: NEVER blindly retry a paid Runpod submission
            g_jobs = store.tables.get("dbo.MOSAICEARTH_GAUSSIAN_JOBS", [])
            active_gpu_jobs = sum(1 for gj in g_jobs if str(gj.get("STATUS") or "").upper() in ("RUNNING", "DISPATCHED"))
            for gj in g_jobs:
                g_status = str(gj.get("STATUS") or "").upper()
                g_pid = int(gj.get("PROJECT_ID") or 0)
                if g_status in ("FAILED", "ERROR", "CANCELLED") and g_pid > 0 and not gj.get("REFUNDED_ON_FAILURE"):
                    store.refund_task_on_failure(g_pid, reason=str(gj.get("LAST_ERROR") or "Gaussian Splatting job failed"))
                    with store.lock:
                        for current in store.tables.get("dbo.MOSAICEARTH_GAUSSIAN_JOBS", []):
                            if current.get("ID") == gj.get("ID"):
                                current["REFUNDED_ON_FAILURE"] = 1
                                store.persist_table("dbo.MOSAICEARTH_GAUSSIAN_JOBS")
                                break
            if active_gpu_jobs < 1:
                for gj in g_jobs:
                    if str(gj.get("STATUS") or "").upper() == "QUEUED" and not gj.get("PROVIDER_JOB_ID"):
                        gj["UPDATED_AT_UTC"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # 3. Standard photogrammetry dispatcher and failed-job payment reconciliation
            for proj in store.tables.get("dbo.WEB_PROJECTS", []):
                if bool(proj.get("DELETED")):
                    continue
                pid = int(proj.get("ID") or 0)
                state_id = int(proj.get("STATE_ID") or 0)
                # Failed jobs restore quota or enter the cash refund review queue.
                if state_id == 3 and not proj.get("UPFRONT_REFUND_CHECKED") and (bool(proj.get("PROCESSING_STARTED_AT")) or bool(proj.get("LAST_ERROR"))):
                    ref_res = store.refund_task_on_failure(pid, reason="NodeODM photogrammetry task failed (STATE_ID=3)")
                    with store.lock:
                        for current in store.tables.get("dbo.WEB_PROJECTS", []):
                            if int(current.get("ID") or 0) == pid:
                                current["UPFRONT_REFUND_CHECKED"] = 1
                                store.persist_table("dbo.WEB_PROJECTS")
                                break
                    if ref_res.get("refunded"):
                        print(f"[MosaicEarth Worker] Restored plan quota for failed task #{pid}", flush=True)
                elif state_id == 1:
                    proj["ENGINE_LAST_HEARTBEAT_UTC"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                elif state_id == 2:
                    # Poll active NodeODM photogrammetry tasks so they finalize automatically even if user closes browser tab
                    try:
                        store.poll_photogrammetry_nodeodm(pid, int(proj.get("CREATE_ID") or proj.get("USER_ID") or 2))
                    except Exception as poll_exc:
                        print(f"[MosaicEarth Worker] Poll warning for task #{pid}: {poll_exc}", file=sys.stderr, flush=True)

        except Exception as exc:
            print(f"[MosaicEarth Worker] Loop warning: {exc}", file=sys.stderr, flush=True)

        time.sleep(poll_seconds)


if __name__ == "__main__":
    run_worker_loop()
