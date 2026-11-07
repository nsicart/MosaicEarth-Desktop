"""MosaicEarth connector for WebODM and OpenDroneMap WebUI (AGPL-3.0+)."""
import json
import uuid
from app.plugins import PluginBase, Menu, MountPoint
from app.plugins.worker import run_function_async
from app.api.common import get_and_check_project
from app.api.tasks import TaskNestedView
from django.contrib.auth.decorators import login_required
from .state import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException
from .mosaicearth_sync import Client, SyncError
from .transfer import transfer


def dispatch(module, arguments):
    from importlib import import_module
    return import_module(module).transfer(*arguments)


def state_key(user_id, transfer_id):
    return f"mosaicearth-transfer:{user_id}:{transfer_id}"


class Plugin(PluginBase):
    def main_menu(self):
        return [Menu("MosaicEarth", self.public_url(""), "fa fa-globe fa-fw")]

    def app_mount_points(self):
        @login_required
        def home(request):
            return render(request, self.template_path("sync.html"), {"base": self.public_url(""), "title": "MosaicEarth"})

        @login_required
        @require_POST
        def api(request):
            try:
                data = json.loads(request.body)
                action = data.get("action")
                if action == "signin":
                    client = Client()
                    user = client.signin(data["email"], data["password"])
                    request.session["mosaicearth_cloud"] = {"token": client.token, "email": user["email"]}
                    return JsonResponse({"ok": True, "email": user["email"]})
                session = request.session.get("mosaicearth_cloud")
                if not session:
                    return JsonResponse({"ok": False, "error": "Sign in to MosaicEarth Cloud first"}, status=401)
                client = Client(session["token"])
                if action == "session":
                    return JsonResponse({"ok": True, "email": session["email"]})
                if action == "signout":
                    try:
                        client.signout()
                    finally:
                        request.session.pop("mosaicearth_cloud", None)
                    return JsonResponse({"ok": True})
                if action == "catalog":
                    return JsonResponse({"ok": True, "snapshots": client.snapshots(),
                                         "projects": client.request("GET", "/api/v2/sync/projects")["projects"]})
                if action in {"status", "pause"}:
                    key = state_key(request.user.pk, str(data["transfer_id"]))
                    state = cache.get(key)
                    if state is None:
                        return JsonResponse({"ok": False, "error": "Transfer not found or expired. Resume from the same task."}, status=404)
                    if action == "pause":
                        cache.set(key + ":pause", True, 86400)
                    return JsonResponse({"ok": True, **state})
                if action == "push":
                    task = TaskNestedView().get_and_check_task(request, data["task_id"])
                    get_and_check_project(request, task.project_id, ("view_project",))
                    if task.status != 40:
                        raise ValueError("Only completed tasks can be synchronized")
                    target = str(task.pk)
                elif action == "pull":
                    project = get_and_check_project(request, int(data["project_id"]), ("change_project",))
                    target = str(project.pk)
                else:
                    raise ValueError("Unknown operation")
                transfer_id = uuid.uuid4().hex
                key = state_key(request.user.pk, transfer_id)
                cache.set(key, {"status": "queued", "message": "Preparing transfer", "done": 0, "total": 0}, 86400)
                run_function_async(dispatch, transfer.__module__, [action, target, session["token"], key,
                                   str(data.get("snapshot_id") or ""), int(data.get("cloud_project_id") or 0)])
                return JsonResponse({"ok": True, "transfer_id": transfer_id})
            except APIException as exc:
                return JsonResponse({"ok": False, "error": str(exc.detail)}, status=exc.status_code)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)

        return [MountPoint("$", home), MountPoint("api$", api)]
