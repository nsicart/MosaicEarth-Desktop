from .asset_metadata import format_coordinates
import html
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .db_store import (
    DEFAULT_CATEGORIES,
    ICON_3D,
    ICON_CLOUD,
    ICON_ORTHOPHOTO,
    KNOWN_SOURCE_EXTENSIONS,
    family_display,
    get_store,
)
from .pages import render_home_master
from .seo_engine import build_head_metadata_html, normalize_lang


def _ext_options_html() -> str:
    return "".join(f'<option value="{ext}">.{ext}</option>' for ext in KNOWN_SOURCE_EXTENSIONS)


def _social_login_html(return_url: str, signup: bool = False) -> str:
    """Offer only providers whose server credentials are actually configured."""
    store = get_store()
    icons = {'google': '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M21.6 12.2c0-.7-.1-1.4-.2-2H12v3.9h5.4a4.6 4.6 0 0 1-2 3v2.5h3.3c1.9-1.8 2.9-4.4 2.9-7.4Z"/><path fill="#34A853" d="M12 22c2.7 0 5-.9 6.7-2.4l-3.3-2.5c-.9.6-2.1 1-3.4 1a5.9 5.9 0 0 1-5.5-4.1H3.1v2.6A10.1 10.1 0 0 0 12 22Z"/><path fill="#FBBC05" d="M6.5 14a6 6 0 0 1 0-3.9V7.5H3.1a10.1 10.1 0 0 0 0 9.1Z"/><path fill="#EA4335" d="M12 6a5.5 5.5 0 0 1 3.9 1.5l2.9-2.9A9.8 9.8 0 0 0 3.1 7.5l3.4 2.6A5.9 5.9 0 0 1 12 6Z"/></svg>', 'microsoft': '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#f25022" d="M2 2h9.4v9.4H2z"/><path fill="#7fba00" d="M12.6 2H22v9.4h-9.4z"/><path fill="#00a4ef" d="M2 12.6h9.4V22H2z"/><path fill="#ffb900" d="M12.6 12.6H22V22h-9.4z"/></svg>', 'github': '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#fff" d="M12 .7a11.6 11.6 0 0 0-3.7 22.6c.6.1.8-.3.8-.6v-2.3c-3.4.7-4.1-1.4-4.1-1.4-.6-1.4-1.4-1.8-1.4-1.8-1.1-.8.1-.8.1-.8 1.2.1 1.9 1.3 1.9 1.3 1.1 1.9 2.9 1.3 3.6 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.4-5.5-6a4.7 4.7 0 0 1 1.2-3.2c-.1-.3-.5-1.6.1-3.2 0 0 1-.3 3.2 1.2a11 11 0 0 1 5.9 0c2.2-1.5 3.2-1.2 3.2-1.2.6 1.6.2 2.9.1 3.2a4.7 4.7 0 0 1 1.2 3.2c0 4.6-2.8 5.7-5.5 6 .4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6A11.6 11.6 0 0 0 12 .7Z"/></svg>'}
    links = []
    for key, title in (("google", "Google"), ("microsoft", "Microsoft"), ("github", "GitHub")):
        if not all(store.get_setting(f"OAuth.{title}.{field}", "") for field in ("ClientId", "ClientSecret")):
            continue
        url = "/OAuth.ashx?provider=" + key + "&amp;returnUrl=" + html.escape(return_url, quote=True)
        links.append(f'<a class="social-button" href="{url}" aria-label="Continue with {title}">{icons[key]}<span>{title}</span></a>')
    if not links:
        return ""
    action = "up" if signup else "in"
    return f'<div class="login-divider"><span>or sign {action} with</span></div><div class="social-login" aria-label="Social sign {action}">{"".join(links)}</div>'


def render_login_page(
    error_msg: Optional[str] = None,
    return_url: str = "/en/Map",
    is_lan: bool = False,
) -> str:
    store = get_store()
    error_notice = ""
    if error_msg:
        error_notice = (
            '<div role="alert" class="login-error" tabindex="-1" autofocus style="margin:16px 0;padding:14px;'
            'border:1px solid #b9695a;border-radius:8px;background:#321d1d;'
            'color:#ffe1dc;text-align:left;overflow-wrap:anywhere;">'
            + html.escape(error_msg) + '</div>'
        )

    lan_quick_bar = ""
    is_desktop_local = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
    if is_desktop_local:
        lic_email_display = "your licensed MosaicEarth account"
        try:
            for cand in (Path(__file__).resolve().parent.parent / "desktop_license.json", Path(__file__).resolve().parent / "desktop_license.json"):
                if cand.is_file():
                    lic_obj = json.loads(cand.read_text(encoding="utf-8"))
                    if lic_obj.get("licensed_email"):
                        lic_email_display = str(lic_obj["licensed_email"])
                        break
        except Exception:
            pass
        lan_quick_bar = f"""
        <div style="margin-top:16px;padding:14px;border-radius:8px;background:rgba(16,23,19,0.95);border:1px solid rgba(153,204,0,0.45);text-align:left;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#99CC00;font-weight:700;margin-bottom:6px;">
                🔒 MosaicEarth Desktop &middot; Session-Bound Licensed Workstation
            </div>
            <div style="font-size:12px;color:#d1d5db;line-height:1.45;">
                This Desktop installation is cryptographically bound to <strong>{html.escape(lic_email_display)}</strong>. Sign in above with that account to unlock local + cloud processing and storage.
            </div>
        </div>"""
    elif is_lan:
        oauth_users = []
        for u in store.tables.get("dbo.USERS", []):
            if not u.get("ACTIVO", True):
                continue
            uid = int(u.get("ID") or 0)
            enriched = store.get_user_by_id(uid)
            if enriched and uid in (38769, 38770, 38758, 181, 145):
                oauth_users.append(enriched)
        buttons = []
        for u in oauth_users:
            uid = int(u["ID"])
            email = html.escape(str(u.get("EMAIL") or ""))
            name = html.escape(str(u.get("USERNAME") or u.get("NAME") or email))
            provider = "Google" if u.get("IsExternalAccount") else "Account"
            buttons.append(
                f'<a href="/Auth/ExternalLogin.ashx?provider=lan&uid={uid}&returnUrl={html.escape(return_url, quote=True)}" '
                f'style="display:inline-flex;align-items:center;gap:6px;padding:6px 11px;border-radius:4px;'
                f'background:rgba(153,204,0,0.14);border:1px solid rgba(153,204,0,0.45);color:#fff;font-size:12px;text-decoration:none;">'
                f'<span style="color:#99CC00;font-weight:700;">[{provider}]</span> {name} <small style="color:#aaa;">({email})</small></a>'
            )
        if buttons:
            lan_quick_bar = f"""
            <div style="margin-top:16px;padding:12px;border-radius:6px;background:rgba(17,24,20,0.92);border:1px solid rgba(153,204,0,0.35);text-align:left;">
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#99CC00;font-weight:700;margin-bottom:6px;">
                    LAN Testing Mode (192.168.0.143) · Instant OAuth / Account Switcher
                </div>
                <div style="font-size:11px;color:#bbb;margin-bottom:8px;">
                    Google OAuth callbacks require the public domain <code>app.mosaicearth.app</code>. While testing on LAN IP, click any account below to sign in directly:
                </div>
                <div style="display:flex;flex-wrap:wrap;gap:6px;">
                    {''.join(buttons)}
                </div>
            </div>"""

    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>MosaicEarth Studio &amp; Public 3D Geospatial Globe &middot; Sign In or Explore</title>
    <meta name="description" content="Sign in to MosaicEarth Studio or explore our public 3D geospatial globe with 228+ georeferenced orthophotos, 3D photogrammetry models, Gaussian Splats and LiDAR point clouds." />
    <meta name="robots" content="noindex, nofollow" />
    <link rel="icon" type="image/png" href="/Resources/mosaicearth-site-favicon.png" />
    <link href="https://fonts.googleapis.com/css?family=Open+Sans:300italic,400italic,600italic,700italic,400,600,700,300&amp;subset=latin" rel="stylesheet" type="text/css" />

    <link href="/admin/assets/css/bootstrap.min.css" rel="stylesheet" />
    <link href="/vendor/fontawesome/css/all.css" rel="stylesheet" />
    <link href="/admin/assets/css/animate.css" rel="stylesheet" />
    <link href="/assets/css/master.css" rel="stylesheet" />
    <link href="/Web/login.css?v=8" rel="stylesheet" />
    <link href="/admin/assets/css/AdminMaster.css" rel="stylesheet" />
    <link href="/admin/assets/css/plugins/select2/select2.min.css" rel="stylesheet" />
    <link href="/admin/assets/css/plugins/sweetalert/sweetalert.css" rel="stylesheet" />
    <link href="/admin/assets/css/plugins/toastr/toastr.min.css" rel="stylesheet" />
    <link href="/Admin/assets/css/master.css" rel="stylesheet" />
    <link rel="stylesheet" href="/assets/css/mosaic-responsive.css?v=20260923-map-r29" />
    <style>
        .btn-primary, .btn-primary:hover, .btn-primary:focus {{ color: #10170c !important; }}
        .show-password-btn:focus-visible {{ outline: 2px solid #99cc00 !important; outline-offset: 3px; }}
        .form-control::placeholder {{ color: #59675e !important; opacity: 1; }}
        .password-wrapper {{
            position: relative !important;
        }}
        .password-wrapper .form-control {{
            padding-right: 40px !important;
        }}
        .show-password-btn {{
            position: absolute !important;
            top: 50%;
            right: 12px;
            transform: translateY(-50%);
            background: none !important;
            border: none !important;
            padding: 0 !important;
            margin: 0 !important;
            color: #526158 !important;
            cursor: pointer !important;
            z-index: 2;
        }}
        .show-password-btn:hover,
        .show-password-btn:focus {{
            color: #25352a !important;
        }}
    </style>
</head>
<body>
    <form id="form1" method="post" action="/Default.aspx">
        <input type="hidden" name="returnUrl" value="{html.escape(return_url, quote=True)}" />
        <div class="container-fluid">
            <div class="row align-items-center">
                <div class="col-12 col-md-6 text-center">
                    <div class="imagen-logo">
                        <div class="temporary-wordmark" aria-label="MosaicEarth"><span>Mosaic</span><strong>Earth</strong></div>
                        <div class="register-intro">
                            <span class="register-intro-kicker">REAL-WORLD PLACES &middot; DIGITAL ASSETS &middot; ONE GLOBAL NETWORK</span>
                            <h1>Create, place, publish and reuse geospatial assets.</h1>
                            <p>MosaicEarth is a global network for digital geospatial representations of the real world. Process source imagery or import a finished map, layer, 3D model or point cloud created with a compatible workflow. Georeference it, keep it private, share it, or publish it for discovery and reuse.</p>
                            <div class="register-intro-points">
                                <span>Any compatible source</span><span>Real-world georeferencing</span><span>Discoverable and reusable</span>
                            </div>
                            <div class="public-explore-cta" style="margin-top:20px;padding-top:16px;border-top:1px solid rgba(153,204,0,0.25);text-align:left;">
                                <a href="/en/Map" class="btn btn-primary" style="display:inline-block;margin-bottom:10px;font-weight:700;">EXPLORE PUBLIC 3D GLOBE (NO SIGN-IN) &rarr;</a>
                                <nav aria-label="Public geospatial asset directories" style="display:flex;flex-wrap:wrap;gap:10px;font-size:12px;">
                                    <a href="/en/directory" style="color:#99CC00;text-decoration:underline;font-weight:700;">Full Geospatial Directory</a>
                                    <a href="/es/directory" style="color:#99CC00;text-decoration:underline;">Directorio (ES)</a>
                                    <a href="/en/explore/maps" style="color:#99CC00;text-decoration:underline;">Maps &amp; Orthophotos</a>
                                    <a href="/en/explore/3d-models" style="color:#99CC00;text-decoration:underline;">3D Models &amp; Gaussian Splats</a>
                                    <a href="/en/explore/point-clouds" style="color:#99CC00;text-decoration:underline;">LiDAR Point Clouds</a>
                                    <a href="/en/Faqs" style="color:#99CC00;text-decoration:underline;">Help &amp; FAQs</a>
                                </nav>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="col-12 col-md-6 text-center">
                    <div id="PanelLogin" style="display: block;">
                        <div class="col-login">
                            <div class="tituloLogin">
                                WELCOME
                            </div>
                            {error_notice}
                            <div class="tituloForgot">
                                Enter your username and password. Did you forget your password? Click
                                <a href="javascript:void(0);" class="recuperar-password" id="btnResetPass" onclick="showRecovery();return false;">
                                    <b style="color:#99CC00;">here.</b>
                                </a>
                            </div>
                            <div class="divEmail">
                                <input type="text" class="form-control" id="txtEmail" placeholder="Email" name="email" autocomplete="username" required />
                            </div>
                            <div class="divPassword password-wrapper">
                                <input type="password" class="form-control" id="txtPassword" name="password" placeholder="Password" autocomplete="current-password" required />
                                <button type="button" class="show-password-btn" aria-label="Show password">
                                    <i class="fa fa-eye"></i>
                                </button>
                            </div>
                            <div class="divAceptar">
                                <input type="submit" name="btnSave" value="LOG IN" id="btnSave" class="btn btn-primary form-control w-100" />
                            </div>
                            {_social_login_html(return_url)}
                            <div class="tituloForgot">
                                Not a member yet? Sign up
                                <a class="recuperar-password" id="btnSignUp" href="/Register.aspx?returnUrl={html.escape(return_url, quote=True)}">
                                    <b style="color:#99CC00;">here.</b>
                                </a>
                            </div>
                            {lan_quick_bar}
                            <div class="login-legal">
                                <p>By using MosaicEarth you agree to the <a href="/Terms.aspx" target="_blank" rel="noopener">Terms of Service</a>. Public MosaicAssets use the <a href="/License.aspx" target="_blank" rel="noopener">MosaicEarth Standard License</a>.</p>
                                <nav aria-label="Privacy and legal information">
                                    <a href="https://mosaicearth.app/privacy" target="_blank" rel="noopener">Privacy</a>
                                    <a href="https://mosaicearth.app/cookies" target="_blank" rel="noopener">Cookies</a>
                                    <a href="https://mosaicearth.app/data-deletion" target="_blank" rel="noopener">Data deletion</a>
                                </nav>
                            </div>
                            <aside class="login-cookie-note" role="note" aria-label="Essential cookie notice">
                                <strong>Essential cookies</strong>
                                <span>MosaicEarth uses necessary session and security technologies to sign you in and protect the service. We do not use them for advertising.</span>
                                <a href="https://mosaicearth.app/cookies" target="_blank" rel="noopener">Read the Cookie Policy</a>
                            </aside>
                        </div>
                    </div>
                    <div id="PanelForgotPass" style="display: none;">
                        <div class="col-login">
                            <div class="tituloLogin">
                                WELCOME BACK!
                            </div>
                            <div class="tituloForgot">
                                Enter your email or
                                <a href="javascript:void(0);" class="recuperar-password" id="LinkButton1" onclick="showLogin();return false;">
                                    <b>log in.</b>
                                </a>
                            </div>
                            <div class="divEmail">
                                <input class="form-control" id="txtEmailForget" placeholder="Email" />
                            </div>
                            <div class="divAceptar">
                                <input type="button" value="RECOVER PASSWORD" id="btnResetPassword" class="btn btn-primary form-control w-100" onclick="sendPasswordRecovery()" />
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <input type="hidden" id="hdnTranslations" value="" />

        <script src="/admin/assets/js/jquery-3.6.1.min.js"></script>
        <script src="/admin/assets/js/popper.min.js"></script>
        <script src="/admin/assets/js/bootstrap.js"></script>
        <script src="/admin/assets/js/plugins/metisMenu/jquery.metisMenu.js"></script>
        <script src="/admin/assets/js/plugins/slimscroll/jquery.slimscroll.min.js"></script>
        
        <script data-pace-options='{{"ajax": false}}' src="/admin/assets/js/plugins/pace/pace.min.js"></script>
        <script src="/admin/assets/js/plugins/toastr/toastr.min.js"></script>
        <script src="/admin/assets/js/plugins/select2/select2.full.min.js"></script>
        <script src="/admin/assets/js/plugins/sweetalert/sweetalert.min.js"></script>
        <script src="/js/init.js?v=20260916-ux"></script>
        <script src="/js/utils.js?v=20260916-en"></script>
    </form>
    <script>
        function showRecovery() {{
            document.getElementById('PanelLogin').style.display = 'none';
            document.getElementById('PanelForgotPass').style.display = 'block';
        }}
        function showLogin() {{
            document.getElementById('PanelForgotPass').style.display = 'none';
            document.getElementById('PanelLogin').style.display = 'block';
        }}
        function sendPasswordRecovery() {{
            var em = ($('#txtEmailForget').val() || '').trim();
            if (!em) {{
                toastr.error('Please enter your email.');
                return;
            }}
            $.ajax({{
                type: 'POST',
                url: '/Default.aspx/SendPasswordRecovery',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                data: JSON.stringify({{ email: em }}),
                success: function(res) {{
                    var d = typeof res.d === 'string' ? JSON.parse(res.d) : res.d;
                    if (d && d.ok) {{
                        toastr.success(d.message || 'Recovery email sent! Please check your inbox.');
                        showLogin();
                    }} else {{
                        toastr.error((d && d.message) || 'Could not send recovery email.');
                    }}
                }},
                error: function() {{
                    toastr.error('Error sending password recovery request.');
                }}
            }});
        }}
        document.addEventListener('DOMContentLoaded', function () {{
            document.querySelectorAll('.show-password-btn').forEach(function (btn) {{
                var wrapper = btn.closest('.password-wrapper');
                if (!wrapper) return;
                var input = wrapper.querySelector('input');
                var icon = btn.querySelector('i');
                if (!input || !icon) return;
                btn.addEventListener('click', function () {{
                    var isPwd = input.getAttribute('type') === 'password';
                    input.setAttribute('type', isPwd ? 'text' : 'password');
                    btn.setAttribute('aria-label', isPwd ? 'Hide password' : 'Show password');
                    icon.classList.toggle('fa-eye');
                    icon.classList.toggle('fa-eye-slash');
                }});
            }});
        }});
    </script>
</body>
</html>"""


def render_register_page(return_url: str = "/en/Projects") -> str:
    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Create account &middot; MosaicEarth</title>
    <meta name="robots" content="noindex, nofollow" />
    <link rel="icon" type="image/png" href="/Resources/mosaicearth-site-favicon.png" />
    <link href="https://fonts.googleapis.com/css?family=Open+Sans:300italic,400italic,600italic,700italic,400,600,700,300&amp;subset=latin" rel="stylesheet" type="text/css" />

    <link href="/admin/assets/css/bootstrap.min.css" rel="stylesheet" />
    <link href="/vendor/fontawesome/css/all.css" rel="stylesheet" />
    <link href="/admin/assets/css/animate.css" rel="stylesheet" />
    <link href="/assets/css/master.css" rel="stylesheet" />
    <link href="/Web/login.css?v=8" rel="stylesheet" />
    <link href="/admin/assets/css/AdminMaster.css" rel="stylesheet" />
    <link href="/admin/assets/css/plugins/select2/select2.min.css" rel="stylesheet" />
    <link href="/admin/assets/css/plugins/sweetalert/sweetalert.css" rel="stylesheet" />
    <link href="/admin/assets/css/plugins/toastr/toastr.min.css" rel="stylesheet" />
    <link href="/Admin/assets/css/master.css" rel="stylesheet" />
    <link rel="stylesheet" href="/assets/css/mosaic-responsive.css?v=20260923-map-r29" />
    <style>
        .btn-primary, .btn-primary:hover, .btn-primary:focus {{ color: #10170c !important; }}
        .show-password-btn:focus-visible {{ outline: 2px solid #99cc00 !important; outline-offset: 3px; }}
        .form-control::placeholder {{ color: #59675e !important; opacity: 1; }}
        .password-wrapper {{
            position: relative !important;
        }}
        .password-wrapper .form-control {{
            padding-right: 40px !important;
        }}
        .show-password-btn {{
            position: absolute !important;
            top: 50%;
            right: 12px;
            transform: translateY(-50%);
            background: none !important;
            border: none !important;
            padding: 0 !important;
            margin: 0 !important;
            color: #526158 !important;
            cursor: pointer !important;
            z-index: 2;
        }}
        .show-password-btn:hover,
        .show-password-btn:focus {{
            color: #25352a !important;
        }}
    </style>
</head>
<body>
    <form id="form1" onsubmit="registerNewAccount(event)">
        <div class="container-fluid">
            <div class="row align-items-center">
                <div class="col-12 col-md-6 text-center">
                    <div class="imagen-logo">
                        <div class="temporary-wordmark" aria-label="MosaicEarth"><span>Mosaic</span><strong>Earth</strong></div>
                        <div class="register-intro">
                            <span class="register-intro-kicker">REAL-WORLD PLACES &middot; DIGITAL ASSETS &middot; ONE GLOBAL NETWORK</span>
                            <h1>Create, place, publish and reuse geospatial assets.</h1>
                            <p>Process source imagery or import finished orthophotos, 3D models and point clouds from compatible workflows. Keep them private, share them, or publish them on the MosaicEarth world.</p>
                            <div class="register-intro-points">
                                <span>Any compatible source</span><span>Real-world georeferencing</span><span>Discoverable and reusable</span>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="col-12 col-md-6 text-center">
                    <div id="PanelSignUp">
                        <div class="col-login">
                            <div class="tituloLogin">
                                CREATE YOUR MOSAICEARTH ACCOUNT
                            </div>
                            <div class="tituloForgot">
                                Already a member? Log in
                                <a class="recuperar-password" id="LinkButton2" href="/Default.aspx?ReturnUrl={html.escape(return_url, quote=True)}">
                                    <b style="color:#99CC00;">here.</b>
                                </a>
                            </div>
                            <div class="divEmail">
                                <input class="form-control" id="txtRegistroUser" placeholder="Username" autocomplete="nickname" required />
                            </div>
                            <div class="divEmail">
                                <input type="email" class="form-control" id="txtRegistroEmail" placeholder="Email" autocomplete="email" required />
                            </div>
                            <div class="divPassword password-wrapper">
                                <input type="password" class="form-control" id="txtRegistroPass" placeholder="Password" autocomplete="new-password" required />
                                <button type="button" class="show-password-btn" aria-label="Show password">
                                    <i class="fa fa-eye"></i>
                                </button>
                            </div>
                            <div class="divPassword password-wrapper">
                                <input type="password" class="form-control" id="txtRegistroPassConfirm" placeholder="Confirm password" autocomplete="new-password" required />
                                <button type="button" class="show-password-btn" aria-label="Show password">
                                    <i class="fa fa-eye"></i>
                                </button>
                            </div>
                            <div class="divAceptar">
                                <input type="submit" value="SIGN UP" id="btnRegister" class="btn btn-primary form-control w-100" />
                            </div>
                            {_social_login_html(return_url, signup=True)}
                            <div class="login-legal">
                                <p>By creating an account you agree to the <a href="/Terms.aspx" target="_blank" rel="noopener">Terms of Service</a> and acknowledge the <a href="https://mosaicearth.app/privacy" target="_blank" rel="noopener">Privacy Policy</a>.</p>
                                <nav aria-label="Privacy and legal information">
                                    <a href="https://mosaicearth.app/cookies" target="_blank" rel="noopener">Cookies</a>
                                    <a href="/License.aspx" target="_blank" rel="noopener">Standard License</a>
                                    <a href="https://mosaicearth.app/data-deletion" target="_blank" rel="noopener">Data deletion</a>
                                </nav>
                            </div>
                            <aside class="login-cookie-note" role="note" aria-label="Essential cookie notice">
                                <strong>Essential cookies</strong>
                                <span>MosaicEarth uses necessary session and security technologies to create and protect your account. We do not use them for advertising.</span>
                                <a href="https://mosaicearth.app/cookies" target="_blank" rel="noopener">Read the Cookie Policy</a>
                            </aside>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <input type="hidden" id="hdnTranslations" value="" />

        <script src="/admin/assets/js/jquery-3.6.1.min.js"></script>
        <script src="/admin/assets/js/popper.min.js"></script>
        <script src="/admin/assets/js/bootstrap.js"></script>
        <script src="/admin/assets/js/plugins/metisMenu/jquery.metisMenu.js"></script>
        <script src="/admin/assets/js/plugins/slimscroll/jquery.slimscroll.min.js"></script>
        
        <script data-pace-options='{{"ajax": false}}' src="/admin/assets/js/plugins/pace/pace.min.js"></script>
        <script src="/admin/assets/js/plugins/toastr/toastr.min.js"></script>
        <script src="/admin/assets/js/plugins/select2/select2.full.min.js"></script>
        <script src="/admin/assets/js/plugins/sweetalert/sweetalert.min.js"></script>
        <script src="/js/init.js?v=20260916-ux"></script>
        <script src="/js/utils.js?v=20260916-en"></script>
    </form>
    <script>
        function registerNewAccount(ev) {{
            if (ev) ev.preventDefault();
            var u = ($('#txtRegistroUser').val() || '').trim();
            var e = ($('#txtRegistroEmail').val() || '').trim();
            var p = $('#txtRegistroPass').val() || '';
            var p2 = $('#txtRegistroPassConfirm').val() || '';
            if (!u || !e || !p) {{ toastr.error('Please fill in all fields.'); return; }}
            if (p !== p2) {{ toastr.error('Passwords do not match.'); return; }}
            $.ajax({{
                type: 'POST',
                url: '/Default.aspx/RegisterUser',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                data: JSON.stringify({{ username: u, email: e, password: p }}),
                success: function(res) {{
                    var d = typeof res.d === 'string' ? JSON.parse(res.d) : res.d;
                    if (d && d.ok) {{
                        window.location.href = {json.dumps(return_url)};
                    }} else {{
                        toastr.error((d && d.message) || 'Could not register account.');
                    }}
                }},
                error: function() {{
                    toastr.error('Could not register account.');
                }}
            }});
        }}
        document.addEventListener('DOMContentLoaded', function () {{
            document.querySelectorAll('.show-password-btn').forEach(function (btn) {{
                var wrapper = btn.closest('.password-wrapper');
                if (!wrapper) return;
                var input = wrapper.querySelector('input');
                var icon = btn.querySelector('i');
                if (!input || !icon) return;
                btn.addEventListener('click', function () {{
                    var isPwd = input.getAttribute('type') === 'password';
                    input.setAttribute('type', isPwd ? 'text' : 'password');
                    btn.setAttribute('aria-label', isPwd ? 'Hide password' : 'Show password');
                    icon.classList.toggle('fa-eye');
                    icon.classList.toggle('fa-eye-slash');
                }});
            }});
        }});
    </script>
</body>
</html>"""


MYEARTH_ICON_PROJECT = (
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>'
    '<polyline points="2 12 12 17 22 12"></polyline>'
    '<polyline points="2 17 12 22 22 17"></polyline></svg>'
)

MYEARTH_ICON_SURVEY = (
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" '
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="3" width="18" height="18" rx="3"></rect>'
    '<line x1="3" y1="9" x2="21" y2="9"></line>'
    '<line x1="9" y1="21" x2="9" y2="9"></line></svg>'
)

MYEARTH_ICON_CHEVRON = (
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" '
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="6 9 12 15 18 9"></polyline></svg>'
)

MYEARTH_ICON_GLOBE = (
    '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="10"></circle>'
    '<line x1="2" y1="12" x2="22" y2="12"></line>'
    '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>'
)

MYEARTH_ICON_ACQUIRED = (
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" '
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>'
    '<polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline>'
    '<line x1="12" y1="22.08" x2="12" y2="12"></line></svg>'
)

MYEARTH_ICON_TASK = (
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" '
    'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="4" width="18" height="16" rx="2"></rect>'
    '<line x1="3" y1="10" x2="21" y2="10"></line>'
    '<line x1="8" y1="15" x2="16" y2="15"></line></svg>'
)

MYEARTH_ICON_QUOTA = (
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" '
    'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>'
    '<path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>'
    '<path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg>'
)


def render_projects_page(user: Dict[str, Any], lang: str = "en") -> str:
    norm_lang = normalize_lang(lang)
    store = get_store()
    uid = int(user["ID"])
    hierarchy = store.list_myearth_hierarchy(uid, norm_lang)
    i18n = hierarchy["i18n"]
    storage = hierarchy["storage"]
    projects_tree = hierarchy["projects"]
    acquired_assets = hierarchy["acquired_assets"]
    private_quota_assets = hierarchy.get("private_quota_assets") or []

    seo_head = build_head_metadata_html(
        title="MyEarth · Projects, Surveys, Tasks & Assets · MosaicEarth",
        description="Manage your geospatial Projects, Surveys, Tasks and Assets (Orthophotos, DSM, 3D Models, Point Clouds, Gaussian Splats) in MyEarth.",
        canonical_path=f"/{norm_lang}/Projects",
        robots="noindex, nofollow",
    )

    head_extra = """
    <link rel="stylesheet" href="/assets/css/Projects.css?v=20261016-r45" />
    <link rel="stylesheet" href="/assets/css/source-workflow.css?v=20261016-r45" />
    <style>
        #staticBackdrop .modal-content { max-height: calc(100dvh - 32px); }
        #staticBackdrop .modal-body { overflow-y: auto; }
        #staticBackdrop .modal-header, #staticBackdrop .modal-footer { flex-shrink: 0; }
        #staticBackdrop .project-kind-option { min-height: 0; padding: 14px; }
        #staticBackdrop .project-kind-copy { padding-right: 30px; }
        #staticBackdrop .project-kind-copy em { margin-top: 7px; }
        .myearth-quota-bar {
            background: linear-gradient(145deg, #16221b, #101612);
            border: 1px solid #40634f;
            border-radius: 9px;
            padding: 18px 22px;
            margin-bottom: 20px;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
        }
        .myearth-quota-track {
            width: 260px;
            max-width: 100%;
            height: 7px;
            background: #29342e;
            border-radius: 10px;
            overflow: hidden;
        }
        .myearth-quota-fill {
            height: 100%;
            background: linear-gradient(90deg, #99CC00, #7cca51);
            border-radius: 10px;
        }
        .myearth-toolbar {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            margin-bottom: 18px;
            padding-bottom: 14px;
            border-bottom: 1px solid #26312a;
        }
        .myearth-search-wrap {
            position: relative;
            flex: 1;
            min-width: 240px;
            max-width: 420px;
        }
        .myearth-search-wrap input {
            width: 100%;
            height: 38px;
            padding: 6px 14px 6px 36px;
            background: #101713 !important;
            border: 1px solid #3c4b40 !important;
            border-radius: 6px !important;
            color: #e9f0e9 !important;
            font-family: OpenSansRegular, Arial, sans-serif;
            font-size: 13px;
        }
        .myearth-search-wrap input:focus {
            border-color: #99CC00 !important;
            outline: none;
        }
        .myearth-search-wrap .search-icon {
            position: absolute;
            left: 12px;
            top: 50%;
            transform: translateY(-50%);
            color: #8fa099;
            font-size: 12px;
            pointer-events: none;
        }
        .myearth-view-switcher {
            display: inline-flex;
            flex-wrap: wrap;
            gap: 6px;
            background: #101713;
            padding: 4px;
            border: 1px solid #344239;
            border-radius: 7px;
        }
        .myearth-view-btn {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            background: transparent;
            border: 1px solid transparent;
            color: #dce6dc !important;
            padding: 6px 13px;
            border-radius: 5px;
            font-family: OpenSansSemiBold, Arial, sans-serif;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.16s ease;
        }
        .myearth-view-btn svg {
            stroke: currentColor !important;
        }
        .myearth-view-btn:hover {
            color: #ffffff !important;
            background: rgba(153, 204, 0, 0.12);
        }
        .myearth-view-btn.is-active,
        .myearth-view-btn.active {
            background: #99CC00 !important;
            color: #0b110d !important;
            border-color: #99CC00 !important;
            font-family: OpenSansBold, Arial, sans-serif;
        }
        .myearth-view-btn.is-active span,
        .myearth-view-btn.active span,
        .myearth-view-btn.is-active svg,
        .myearth-view-btn.active svg {
            color: #0b110d !important;
            stroke: #0b110d !important;
        }
        .myearth-project-node {
            background: #141a16;
            border: 1px solid #344239;
            border-radius: 9px;
            margin-bottom: 16px;
            overflow: visible;
            transition: border-color 0.18s ease, box-shadow 0.18s ease;
        }
        .myearth-project-node:hover {
            border-color: #5b7c49;
        }
        .myearth-project-header {
            padding: 15px 20px;
            background: linear-gradient(180deg, #18211b 0%, #141b16 100%);
            border-radius: 8px 8px 0 0;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            cursor: pointer;
            border-bottom: 1px solid #26312a;
        }
        .myearth-caret {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 22px;
            height: 22px;
            border-radius: 5px;
            background: #101713;
            border: 1px solid #344239;
            color: #99CC00;
            transition: transform 0.18s ease, border-color 0.18s ease;
            flex-shrink: 0;
        }
        .myearth-caret.is-collapsed {
            transform: rotate(-90deg);
            color: #8fa099;
        }
        .myearth-icon-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            border-radius: 7px;
        }
        .myearth-icon-project {
            width: 36px;
            height: 36px;
            background: rgba(153, 204, 0, 0.12);
            border: 1px solid #99CC00;
            color: #99CC00;
        }
        .myearth-icon-survey {
            width: 30px;
            height: 30px;
            background: #151f18;
            border: 1px solid #4c6b59;
            color: #b5d979;
        }
        .myearth-icon-task {
            width: 26px;
            height: 26px;
            background: #121b15;
            border: 1px solid #3d5647;
            color: #9be2b8;
        }
        .myearth-asset-icon-box {
            width: 34px;
            height: 34px;
            border-radius: 7px;
            background: #EFEFEF;
            border: 1px solid rgba(255, 255, 255, 0.2);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }
        .myearth-asset-icon-box svg {
            width: 16px;
            height: 16px;
            display: block;
        }
        .myearth-level-pill {
            display: inline-flex;
            align-items: center;
            padding: 2px 8px;
            border-radius: 4px;
            font-family: OpenSansBold, Arial, sans-serif;
            font-size: 9.5px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        .myearth-level-pill--project {
            background: rgba(153, 204, 0, 0.14);
            color: #99CC00;
            border: 1px solid rgba(153, 204, 0, 0.38);
        }
        .myearth-level-pill--survey {
            background: rgba(112, 183, 143, 0.14);
            color: #b5d979;
            border: 1px solid rgba(112, 183, 143, 0.35);
        }
        .myearth-level-pill--task {
            background: rgba(155, 226, 184, 0.11);
            color: #9be2b8;
            border: 1px solid rgba(155, 226, 184, 0.32);
        }
        .myearth-survey-node {
            margin: 12px 18px;
            background: #101612;
            border: 1px solid #2e3c33;
            border-radius: 8px;
            overflow: visible;
        }
        .myearth-survey-header {
            padding: 12px 16px;
            background: #141c17;
            border-radius: 7px 7px 0 0;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            cursor: pointer;
            border-bottom: 1px solid #243028;
        }
        .myearth-task-node {
            margin: 10px 14px;
            background: #0d130f;
            border: 1px solid #25332a;
            border-radius: 7px;
            overflow: visible;
        }
        .myearth-task-header {
            padding: 9px 14px;
            background: #111914;
            border-radius: 6px 6px 0 0;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            cursor: pointer;
            border-bottom: 1px solid #1e2922;
        }
        .myearth-asset-row {
            padding: 12px 16px;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            border-bottom: 1px solid #1e2822;
            font-family: OpenSansRegular, Arial, sans-serif;
            font-size: 13px;
            color: #e9f0e9;
            transition: background-color 0.15s ease;
        }
        .myearth-asset-row:last-child {
            border-bottom: none;
        }
        .myearth-asset-row:hover {
            background: #141d17;
        }
        .myearth-meta-chip {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 2px 8px;
            border-radius: 4px;
            background: #16201a;
            border: 1px solid #2f3e34;
            color: #b8c6be;
            font-size: 11px;
            line-height: 1.4;
        }
        .myearth-origin-chip {
            display: inline-flex;
            align-items: center;
            padding: 2px 8px;
            border-radius: 4px;
            background: rgba(153, 204, 0, 0.1);
            border: 1px solid rgba(153, 204, 0, 0.32);
            color: #c4e265;
            font-family: OpenSansSemiBold, Arial, sans-serif;
            font-size: 10.5px;
        }
        .myearth-vis-chip {
            display: inline-flex;
            align-items: center;
            padding: 3px 10px;
            border-radius: 999px;
            font-family: OpenSansBold, Arial, sans-serif;
            font-size: 10px;
            letter-spacing: 0.03em;
        }
        .myearth-vis-chip--private {
            background: #18201b;
            color: #acbbb0;
            border: 1px solid #3c4b40;
        }
        .myearth-vis-chip--public {
            background: rgba(112, 183, 143, 0.15);
            color: #9be2b8;
            border: 1px solid #558b6e;
        }
        .myearth-vis-chip--market {
            background: rgba(153, 204, 0, 0.16);
            color: #99CC00;
            border: 1px solid #99CC00;
        }
        .myearth-action-btn {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 11px;
            border-radius: 5px;
            background: #131b16;
            border: 1px solid #3c4b40;
            color: #dce6dc;
            font-family: OpenSansSemiBold, Arial, sans-serif;
            font-size: 11.5px;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .myearth-action-btn:hover {
            border-color: #99CC00;
            color: #ffffff;
            background: #19241c;
            text-decoration: none;
        }
        .myearth-action-btn--primary {
            background: #99CC00;
            border-color: #99CC00;
            color: #141c11;
            font-family: OpenSansBold, Arial, sans-serif;
        }
        .myearth-action-btn--primary:hover {
            background: #aedb22;
            border-color: #aedb22;
            color: #0f160c;
        }
        .myearth-action-btn--accent {
            background: rgba(153, 204, 0, 0.12);
            border-color: rgba(153, 204, 0, 0.55);
            color: #99CC00;
        }
        .myearth-action-btn--danger:hover {
            border-color: #c86458;
            color: #ffb9b3;
        }
        .myearth-inline-edit {
            display: none;
            width: 100%;
            margin-top: 10px;
            padding: 14px 16px;
            background: #121915;
            border: 1px solid #40634f;
            border-radius: 7px;
        }
        .myearth-inline-edit.is-open {
            display: block;
        }
        .myearth-inline-edit label {
            display: block;
            margin-bottom: 4px;
            color: #b8c6be;
            font-family: OpenSansSemiBold, Arial, sans-serif;
            font-size: 11px;
        }
        .myearth-inline-edit .form-control {
            background: #0d120f !important;
            border: 1px solid #3c4b40 !important;
            border-radius: 5px !important;
            color: #e9f0e9 !important;
            font-size: 12.5px !important;
            min-height: 34px !important;
            margin-bottom: 0 !important;
        }
        .myearth-inline-edit .form-control:focus {
            border-color: #99CC00 !important;
        }
        @media (max-width: 767.98px) {
            .myearth-survey-node { margin: 8px 8px; }
            .myearth-task-node { margin: 6px 6px; }
            .myearth-project-header, .myearth-survey-header, .myearth-task-header, .myearth-asset-row {
                padding: 10px 12px;
                flex-direction: column;
                align-items: flex-start !important;
            }
            .myearth-quota-bar {
                flex-direction: column;
                align-items: flex-start !important;
            }
        }
    </style>"""

    # Build 4-Level Expandable Tree View HTML (Level 1: Project -> Level 2: Survey -> Level 3: Task -> Level 4: Assets)
    tree_nodes_html: List[str] = []

    def _build_category_options(selected_cat_id: int = 0) -> str:
        opts = ['<option value="0">Select category...</option>']
        for cid, cinfo in sorted(DEFAULT_CATEGORIES.items()):
            sel = ' selected="selected"' if int(cid) == int(selected_cat_id or 0) else ""
            opts.append(f'<option value="{cid}"{sel}>{html.escape(cinfo["name"])}</option>')
        return "".join(opts)

    def _render_asset_row(a: Dict[str, Any], dom_prefix: str = "") -> str:
        if a.get("output_available") is False:
            name = html.escape(str(a["name"]))
            detail = html.escape(str(a.get("status_detail") or "Original saved."))
            task_url = html.escape(str(a["task_url"]), quote=True)
            download = html.escape(str(a["download_url"]), quote=True)
            return f"""
            <div class="myearth-asset-row" data-asset-name="{name.lower()}">
                <div><strong>{name}</strong><div>Source file · {detail}</div></div>
                <a href="{task_url}" class="myearth-action-btn">Processing status</a>
                <a href="{download}" class="myearth-action-btn">Download original</a>
            </div>"""
        aid_str = html.escape(f"{dom_prefix}{a['id']}")
        aname = html.escape(str(a["name"]))
        a_icon_svg = str(a.get("icon_svg") or ICON_ORTHOPHOTO)
        atype_lbl = html.escape(str(a.get("type_label") or "Geospatial Asset"))
        a_origin_lbl = html.escape(str(a.get("task_origin_label") or "Direct File Import"))
        a_cdate = html.escape(str(a.get("creation_date") or ""))
        a_method = html.escape(str(a.get("methodology") or ""))
        a_desc = html.escape(str(a.get("description") or ""))
        a_cat_id = int(a.get("category_id") or 0)
        a_cat_name = html.escape(str((DEFAULT_CATEGORIES.get(a_cat_id) or {}).get("name") or ""))
        _raw_a_tags = str(a.get("tags") or "").strip()
        a_tags = "" if _raw_a_tags in ("0", "0.0", "None", "null", "undefined") else html.escape(_raw_a_tags)
        agsd = html.escape(str(a.get("gsd") or ""))
        agps = html.escape(str(a.get("gps_accuracy") or ""))
        alat = float(a.get("lat") or 41.9702)
        alon = float(a.get("lon") or 2.3885)
        aheight = float(a.get("height") or 0.0)
        aft = int(a.get("file_type_id") or 2)
        ext_aid = int(a.get("external_asset_id") or 0)
        task_pid = int(a.get("task_project_id") or 0)
        vis_mode = str(a.get("visibility") or "private")
        price_eur = int(a.get("price_eur") or 0)
        size_mb = float(a.get("size_mb") or 0.0)
        v_url = html.escape(str(a.get("viewer_url") or f"/Map.aspx?id={task_pid}"))
        det_url = html.escape(str(a.get("detail_url") or v_url))
        edit_3d_url = html.escape(str(a.get("edit_3d_url") or ""))
        ann_url = html.escape(
            str(
                a.get("annotations_url")
                or (f"/MapAnnotations.aspx?id={task_pid}&asset_id={ext_aid}" if ext_aid > 0 else f"/MapAnnotations.aspx?id={task_pid}&type={aft}")
            )
        )
        dl_url = html.escape(str(a.get("download_url") or "#"))
        fly_url = html.escape(str(a.get("fly_url") or f"/Fly.aspx?asset_id=processed-{aft}-{task_pid}&lat={alat}&lon={alon}"))

        if os.environ.get('MOSAICEARTH_DESKTOP_SHARED_UI') == '1':
            vis_badge = f'<span class="myearth-vis-chip myearth-vis-chip--private">LOCAL &middot; {size_mb:.1f} MB</span>'
        elif vis_mode == "marketplace":
            vis_badge = f'<span class="myearth-vis-chip myearth-vis-chip--market">MARKETPLACE &middot; {price_eur} &euro; (0 GiB QUOTA)</span>'
        elif vis_mode == "public_free":
            vis_badge = '<span class="myearth-vis-chip myearth-vis-chip--public">PUBLIC FREE &middot; 0 &euro; (0 GiB QUOTA)</span>'
        else:
            vis_badge = f'<span class="myearth-vis-chip myearth-vis-chip--private">PRIVATE &middot; {size_mb:.1f} MB (5 GiB QUOTA)</span>'

        meta_chips = [
            f'<span class="myearth-origin-chip">{a_origin_lbl}</span>',
            f'<span class="myearth-meta-chip"><i class="fas fa-hdd"></i> {size_mb:.1f} MB</span>',
        ]
        if a_cdate:
            meta_chips.append(f'<span class="myearth-meta-chip"><i class="far fa-calendar-alt"></i> {a_cdate}</span>')
        if a_method:
            meta_chips.append(f'<span class="myearth-meta-chip"><i class="fas fa-satellite-dish"></i> {a_method}</span>')
        if agsd and agsd != "—":
            meta_chips.append(f'<span class="myearth-meta-chip">GSD {agsd}</span>')
        if agps and agps != "—":
            meta_chips.append(f'<span class="myearth-meta-chip">GPS {agps}</span>')
        if a_cat_name:
            meta_chips.append(f'<span class="myearth-meta-chip"><i class="fas fa-folder"></i> {a_cat_name}</span>')
        if a_tags:
            meta_chips.append(f'<span class="myearth-meta-chip"><i class="fas fa-tags"></i> {a_tags}</span>')
        meta_chips.append(f'<span class="myearth-meta-chip">{format_coordinates(alat, alon)}</span>')

        adjust_btn_html = (
            f'<a href="{edit_3d_url}" class="myearth-action-btn" title="Adjust 3-Axis Placement (Click on Map + X, Y, Z)"><i class="fas fa-crosshairs"></i> Adjust Placement</a>'
            if edit_3d_url
            else ""
        )
        annotations_btn_html = (
            f'<a href="{ann_url}" class="myearth-action-btn" title="Open Dedicated 3D Annotations Viewer"><i class="fas fa-map-pin"></i> Annotations</a>'
            if ann_url
            else ""
        )
        is_dem_asset = ext_aid > 0 and bool(a.get("can_derive_terrain"))
        terrain_deriv_btn_html = (
            f'<button type="button" class="myearth-action-btn" style="border-color:#99CC00;color:#d9f99d;" data-name="{aname}" onclick="openTerrainDerivativeModal({task_pid}, {ext_aid}, this.getAttribute(\'data-name\'))" title="Generate Hillshade, Slope, Aspect or Contours on demand">'
            f'<i class="fas fa-mountain"></i> Terrain Derivatives</button>'
            if is_dem_asset
            else ""
        )

        return f"""
        <div class="myearth-asset-row" data-asset-name="{aname.lower()}">
            <div style="display:flex;align-items:flex-start;gap:12px;flex:1;min-width:260px;">
                <span class="myearth-asset-icon-box" title="{atype_lbl}">{a_icon_svg}</span>
                <div style="min-width:0;">
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                        <a href="{det_url}" title="Open Asset Detail Page" style="color:#ffffff;font-family:OpenSansSemiBold,Arial,sans-serif;font-size:13.5px;text-decoration:none;">{aname}</a>
                        <span style="font-size:11px;color:#8fa099;">{atype_lbl}</span>
                    </div>
                    {f'<div style="font-size:12px;color:#b8c6be;margin-top:2px;">{a_desc}</div>' if a_desc else ''}
                    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;">
                        {''.join(meta_chips)}
                    </div>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                {vis_badge}
                <a href="{det_url}" class="myearth-action-btn" title="Open Asset Detail Page"><i class="fas fa-external-link-alt"></i> Asset Page</a>
                <a href="{v_url}" class="myearth-action-btn" title="Open 3D Viewer">3D View</a>
                {adjust_btn_html}
                {annotations_btn_html}
                {terrain_deriv_btn_html}
                <a href="{fly_url}" class="myearth-action-btn myearth-action-btn--accent" title="Fly over this asset"><i class="fas fa-plane-departure"></i> Fly</a>
                <a href="{dl_url}" download class="myearth-action-btn" title="Download original file"><i class="fas fa-download"></i></a>
                <button type="button" class="myearth-action-btn" data-name="{aname}" data-url="{v_url}" onclick="shareSingleAsset(this.getAttribute('data-name'), this.getAttribute('data-url'))" title="Share Asset 3D Viewer Link">
                    <i class="fas fa-share-alt"></i> Share
                </button>
                <button type="button" class="myearth-action-btn" onclick="toggleInlineBox('editAsset_{aid_str}')" title="Edit Asset &amp; Publish / Quota">
                    <i class="fas fa-sliders-h"></i> {'Edit' if os.environ.get('MOSAICEARTH_DESKTOP_SHARED_UI') == '1' else 'Edit / Publish'}
                </button>
                <button type="button" class="myearth-action-btn myearth-action-btn--danger" data-name="{aname}" onclick="openDeleteAssetModal({task_pid}, {aft}, {ext_aid}, this.getAttribute('data-name'))" title="Delete Asset">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
            <div id="editAsset_{aid_str}" class="myearth-inline-edit">
                <div class="row g-2 align-items-end">
                    <div class="col-md-3 col-12">
                        <label>Asset Name</label>
                        <input type="text" id="meTitle_{aid_str}" class="form-control" value="{aname}" />
                    </div>
                    <div class="col-md-2 col-6">
                        <label>Capture / Creation Date</label>
                        <input type="date" id="meDate_{aid_str}" class="form-control" value="{a_cdate}" />
                    </div>
                    <div class="col-md-3 col-6">
                        <label>Acquisition Methodology</label>
                        <input type="text" id="meMethod_{aid_str}" class="form-control" value="{a_method}" placeholder="e.g. UAV RTK Photogrammetry / LiDAR / GNSS" />
                    </div>
                    <div class="col-md-2 col-6">
                        <label>Category</label>
                        <select id="meCat_{aid_str}" class="form-control">
                            {_build_category_options(a_cat_id)}
                        </select>
                    </div>
                    <div class="col-md-2 col-6">
                        <label>Tags (comma-separated)</label>
                        <input type="text" id="meTags_{aid_str}" class="form-control" value="{a_tags}" placeholder="drone, dsm, survey..." />
                    </div>
                    <div class="col-md-3 col-12">
                        <label>Specific Asset Description</label>
                        <input type="text" id="meDesc_{aid_str}" class="form-control" value="{a_desc}" placeholder="Specific description of this asset or capture..." />
                    </div>
                    <div class="col-md-2 col-4">
                        <label>Latitude</label>
                        <input type="text" id="meLat_{aid_str}" class="form-control" value="{alat}" />
                    </div>
                    <div class="col-md-2 col-4">
                        <label>Longitude</label>
                        <input type="text" id="meLon_{aid_str}" class="form-control" value="{alon}" />
                    </div>
                    <div class="col-md-1 col-4">
                        <label>Height (m)</label>
                        <input type="number" step="any" id="meHeight_{aid_str}" class="form-control" value="{aheight}" />
                    </div>
                    <div class="col-md-2 col-6">
                        <label>Visibility &amp; Storage Quota</label>
                        <select id="meVis_{aid_str}" class="form-control" onchange="var pw=document.getElementById('mePriceWrap_{aid_str}');if(pw)pw.style.display=(this.value==='marketplace'?'block':'none');">
                            <option value="private" {'selected' if vis_mode == 'private' else ''}>Private (Counts Toward Storage Quota)</option>
                            <option value="public_free" {'selected' if vis_mode == 'public_free' else ''}>Public Free (0 € · 0 GiB Quota)</option>
                            <option value="marketplace" {'selected' if vis_mode == 'marketplace' else ''}>Marketplace € (0 GiB Quota)</option>
                        </select>
                    </div>
                    <div class="col-md-1 col-6" id="mePriceWrap_{aid_str}" style="display:{'block' if vis_mode == 'marketplace' else 'none'};">
                        <label>Price (€)</label>
                        <input type="number" min="0" step="1" id="mePrice_{aid_str}" class="form-control" value="{price_eur if price_eur > 0 else 25}" />
                    </div>
                    <div class="col-md-1 col-12 text-end">
                        <button type="button" class="myearth-action-btn myearth-action-btn--primary w-100 justify-content-center" style="min-height:34px;" onclick="saveMyEarthAsset({task_pid}, {aft}, {ext_aid}, '{aid_str}')">SAVE</button>
                    </div>
                </div>
            </div>
        </div>"""

    for proj in projects_tree:
        pid = int(proj["id"])
        pname = html.escape(str(proj["name"]))
        pdesc = html.escape(str(proj.get("description") or ""))
        surveys = proj.get("surveys") or []
        total_assets = int(proj.get("asset_count") or 0)

        survey_blocks_html: List[str] = []
        for srv in surveys:
            sid = int(srv["id"])
            sname = html.escape(str(srv["name"]))
            sdesc = html.escape(str(srv.get("description") or ""))
            s_tasks = srv.get("tasks") or []
            s_assets = srv.get("assets") or []
            s_task_folders = srv.get("task_folders") or []

            # Active processing tasks bar (only shown while queued/processing)
            active_tasks_html = ""
            for t in s_tasks:
                if t.get("status") in ("queued", "processing"):
                    pct = int(t.get("progress") or 50)
                    t_lbl = html.escape(str(t.get("type_label") or "Photogrammetry Task"))
                    t_meth = html.escape(str(t.get("methodology") or ""))
                    active_tasks_html += f"""
                    <div style="padding:10px 16px;background:rgba(153,204,0,0.08);border-bottom:1px solid #344239;display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:12px;color:#b5d979;">
                        <span><i class="fas fa-sync fa-spin me-2"></i><strong>{html.escape(i18n['processing'])}</strong> &middot; {t_lbl}{f' ({t_meth})' if t_meth else ''}</span>
                        <div style="width:150px;height:6px;background:#101713;border-radius:999px;overflow:hidden;border:1px solid #344239;">
                            <div style="width:{pct}%;height:100%;background:#99CC00;"></div>
                        </div>
                    </div>"""

            task_folder_blocks_html: List[str] = []
            for tf in s_task_folders:
                tid = int(tf["id"])
                tname = html.escape(str(tf.get("name") or f"Task #{tid}"))
                tdesc = html.escape(str(tf.get("description") or ""))
                t_kind = str(tf.get("kind") or "processing")
                t_status = str(tf.get("status") or "completed")
                t_files_cnt = int(tf.get("files_count") or 0)
                t_prog = int(tf.get("processing_progress") or 0)
                t_last_err = html.escape(str(tf.get("last_error") or ""))
                t_origin_lbl = html.escape(str(tf.get("origin_label") or tf.get("type_label") or "Direct File Import"))
                t_cdate = html.escape(str(tf.get("creation_date") or ""))
                t_method = html.escape(str(tf.get("methodology") or ""))
                t_cat_id = int(tf.get("category_id") or 0)
                t_cat_name = html.escape(str((DEFAULT_CATEGORIES.get(t_cat_id) or {}).get("name") or ""))
                _raw_t_tags = str(tf.get("tags") or "").strip()
                t_tags = "" if _raw_t_tags in ("0", "0.0", "None", "null", "undefined") else html.escape(_raw_t_tags)
                t_area = html.escape(str(tf.get("area") or ""))
                t_gsd = html.escape(str(tf.get("gsd") or ""))
                t_gps = html.escape(str(tf.get("gps_accuracy") or ""))
                t_report_url = html.escape(str(tf.get("report_url") or ""))
                t_upload_url = html.escape(str(tf.get("upload_url") or f"/Client/Project.aspx?id={tid}&upload=1"))
                tf_assets = tf.get("assets") or []
                tf_asset_rows = [_render_asset_row(a) for a in tf_assets]

                # Task status pill & primary action button
                task_status_chip_html = ""
                if t_status == "processing":
                    task_status_chip_html = f'<span class="myearth-vis-chip myearth-vis-chip--public" id="taskStatusChip_{tid}" data-processing-task="{tid}"><i class="fas fa-sync fa-spin me-1"></i> PROCESSING ({max(5, t_prog)}% &middot; {t_files_cnt} photos)</span>'
                    task_extra_btn = f'<a href="{t_upload_url}" class="myearth-action-btn myearth-action-btn--primary" title="View Live OpenDroneMap Processing Console"><i class="fas fa-terminal"></i> View Live Processing ({max(5, t_prog)}%)</a>'
                elif t_report_url:
                    task_extra_btn = f'<a href="{t_report_url}" target="_blank" class="myearth-action-btn" title="Download Photogrammetry Quality Report (PDF)"><i class="fas fa-file-pdf" style="color:#99CC00;"></i> Download Report</a>'
                elif t_status == "failed":
                    task_status_chip_html = '<span class="myearth-vis-chip" style="background:rgba(239,68,68,0.16);border:1px solid #ef4444;color:#fca5a5;"><i class="fas fa-exclamation-triangle me-1"></i> NEEDS ATTENTION</span>'
                    task_extra_btn = f'<a href="{t_upload_url}" class="myearth-action-btn myearth-action-btn--primary" title="Open Task &amp; Retry Processing"><i class="fas fa-redo"></i> Open &amp; Process ({t_files_cnt} photos)</a>'
                elif t_kind in ("external", "gaussian") or t_status != "completed":
                    if t_files_cnt > 0 and len(tf_assets) == 0:
                        task_status_chip_html = f'<span class="myearth-meta-chip" style="border-color:#99CC00;color:#b5d979;"><i class="fas fa-images"></i> {t_files_cnt} photos ready</span>'
                        btn_lbl = f"Start Processing ({t_files_cnt} photos)"
                    else:
                        btn_lbl = "Upload Data / Continue" if len(tf_assets) == 0 else "Add / Upload Files"
                    btn_cls = "myearth-action-btn myearth-action-btn--primary" if len(tf_assets) == 0 else "myearth-action-btn"
                    task_extra_btn = f'<a href="{t_upload_url}" class="{btn_cls}" title="Upload files or start processing"><i class="fas fa-cloud-upload-alt"></i> {btn_lbl}</a>'
                else:
                    task_extra_btn = ""

                if tf_asset_rows:
                    task_body_inner_html = "".join(tf_asset_rows)
                elif t_status == "processing":
                    task_body_inner_html = f"""
                    <div style="padding:14px 18px;background:rgba(153,204,0,0.07);border-top:1px solid rgba(153,204,0,0.25);display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;">
                        <div>
                            <div style="font-size:12.5px;color:#99CC00;font-weight:700;"><i class="fas fa-cog fa-spin me-2"></i>OpenDroneMap is currently processing {t_files_cnt} drone photos (<span id="lblTaskProgPct_{tid}">{max(5, t_prog)}%</span>)</div>
                            <div style="font-size:11.5px;color:#b8c6be;margin-top:3px;" id="lblTaskProgStage_{tid}">Generating Orthophoto (GeoTIFF/COG), 3D Textured Mesh (GLB/OBJ/3D Tiles), Dense Point Cloud (LAZ), and Quality Report (PDF)...</div>
                        </div>
                        <div style="display:flex;align-items:center;gap:12px;">
                            <div style="width:180px;height:8px;background:#101713;border-radius:999px;overflow:hidden;border:1px solid #344239;">
                                <div id="barTaskProg_{tid}" style="width:{max(5, t_prog)}%;height:100%;background:#99CC00;transition:width 0.4s ease;"></div>
                            </div>
                            <a href="{t_upload_url}" class="myearth-action-btn myearth-action-btn--primary"><i class="fas fa-terminal"></i> Open Live Monitor</a>
                        </div>
                    </div>"""
                elif t_files_cnt > 0:
                    err_notice = f' &middot; <span style="color:#fca5a5;">Last status: {t_last_err}</span>' if (t_status == "failed" and t_last_err) else ""
                    task_body_inner_html = f'<div style="padding:12px 16px;font-size:12px;color:#dce6dc;"><i class="fas fa-images" style="color:#99CC00;margin-right:6px;"></i><strong>{t_files_cnt} drone photos uploaded</strong> and stored safely in this task{err_notice}. <a href="{t_upload_url}" style="color:#99CC00;font-weight:700;margin-left:6px;">Open Task &amp; Start OpenDroneMap Processing &rarr;</a></div>'
                else:
                    task_body_inner_html = f'<div style="padding:12px 16px;font-size:12px;color:#8fa099;">No assets uploaded to this task yet. <a href="{t_upload_url}" style="color:#99CC00;font-weight:700;">Click here to upload files &rarr;</a></div>'

                task_folder_blocks_html.append(f"""
                <div class="myearth-task-node" id="taskNode_{tid}">
                    <div class="myearth-task-header" onclick="toggleTaskBody({tid})">
                        <div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap;min-width:0;">
                            <span id="taskCaret_{tid}" class="myearth-caret">{MYEARTH_ICON_CHEVRON}</span>
                            <span class="myearth-icon-badge myearth-icon-task">{MYEARTH_ICON_TASK}</span>
                            <div style="min-width:0;">
                                <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap;">
                                    <span class="myearth-level-pill myearth-level-pill--task">TASK</span>
                                    <strong style="color:#e9f0e9;font-family:OpenSansSemiBold,Arial,sans-serif;font-size:13px;" id="lblTaskName_{tid}">{tname}</strong>
                                    <span class="myearth-origin-chip">{t_origin_lbl}</span>
                                    {task_status_chip_html}
                                    {f'<span class="myearth-meta-chip"><i class="far fa-calendar-alt"></i> {t_cdate}</span>' if t_cdate else ''}
                                    {f'<span class="myearth-meta-chip"><i class="fas fa-satellite-dish"></i> {t_method}</span>' if t_method else ''}
                                    {f'<span class="myearth-meta-chip">Area {t_area}</span>' if t_area and t_area != 'Not calculated' else ''}
                                    {f'<span class="myearth-meta-chip">GSD {t_gsd}</span>' if t_gsd else ''}
                                    {f'<span class="myearth-meta-chip">GPS {t_gps}</span>' if t_gps else ''}
                                    {f'<span class="myearth-meta-chip"><i class="fas fa-folder"></i> {t_cat_name}</span>' if t_cat_name else ''}
                                    {f'<span class="myearth-meta-chip"><i class="fas fa-tags"></i> {t_tags}</span>' if t_tags else ''}
                                    <span style="font-size:11px;color:#8fa099;">({len(tf_assets)} {html.escape(i18n['assets'])})</span>
                                </div>
                                <div id="lblTaskDesc_{tid}" style="font-size:11.5px;color:#acbbb0;margin-top:2px;{'display:none;' if not tdesc else ''}">{tdesc}</div>
                            </div>
                        </div>
                        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;" onclick="event.stopPropagation();">
                            {f'<a href="/OpenContainerMosaic.ashx?kind=task&amp;id={tid}" class="myearth-action-btn myearth-action-btn--accent" title="Open all assets of this Task on the 3D Map">{MYEARTH_ICON_GLOBE} Open in 3D Map</a>' if len(tf_assets) > 0 else ''}
                            {task_extra_btn}
                            <button type="button" class="myearth-action-btn" onclick="toggleInlineBox('editTask_{tid}')" title="Edit Task metadata, category &amp; tags">
                                <i class="fas fa-pen"></i> Edit Task
                            </button>
                            <button type="button" class="myearth-action-btn" data-name="{tname}" onclick="shareContainer('task', {tid}, this.getAttribute('data-name'))" title="Share Task 3D Map">
                                <i class="fas fa-share-alt"></i> Share
                            </button>
                            <button type="button" class="myearth-action-btn myearth-action-btn--danger" data-name="{tname}" onclick="openDeleteContainerModal('task', {tid}, this.getAttribute('data-name'))" title="Delete Task and its assets">
                                <i class="fas fa-trash"></i>
                            </button>
                        </div>
                    </div>
                    <div id="editTask_{tid}" class="myearth-inline-edit" style="margin:8px 14px;" onclick="event.stopPropagation();">
                        <div class="row g-2 align-items-end">
                            <div class="col-md-2 col-12">
                                <label>Task Subfolder Name</label>
                                <input type="text" id="taskName_{tid}" class="form-control" value="{tname}" />
                            </div>
                            <div class="col-md-2 col-6">
                                <label>Capture / Task Date</label>
                                <input type="date" id="taskDate_{tid}" class="form-control" value="{t_cdate}" />
                            </div>
                            <div class="col-md-2 col-6">
                                <label>Methodology</label>
                                <input type="text" id="taskMethod_{tid}" class="form-control" value="{t_method}" />
                            </div>
                            <div class="col-md-2 col-6">
                                <label>Category</label>
                                <select id="taskCat_{tid}" class="form-control">
                                    {_build_category_options(t_cat_id)}
                                </select>
                            </div>
                            <div class="col-md-2 col-6">
                                <label>Tags (comma-separated)</label>
                                <input type="text" id="taskTags_{tid}" class="form-control" value="{t_tags}" placeholder="drone, rtk, 3d..." />
                            </div>
                            <div class="col-md-2 col-12">
                                <label>Task Description</label>
                                <input type="text" id="taskDesc_{tid}" class="form-control" value="{tdesc}" />
                            </div>
                            <div class="col-12 text-end mt-2">
                                <button type="button" class="myearth-action-btn myearth-action-btn--primary" style="min-height:34px;padding:6px 18px;" onclick="saveTaskMeta({tid})">SAVE TASK METADATA</button>
                            </div>
                        </div>
                    </div>
                    <div id="taskBody_{tid}">
                        {task_body_inner_html}
                    </div>
                </div>""")

            survey_blocks_html.append(f"""
            <div class="myearth-survey-node" id="surveyNode_{sid}">
                <div class="myearth-survey-header" onclick="toggleSurveyBody({sid})">
                    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-width:0;">
                        <span id="srvCaret_{sid}" class="myearth-caret">{MYEARTH_ICON_CHEVRON}</span>
                        <span class="myearth-icon-badge myearth-icon-survey">{MYEARTH_ICON_SURVEY}</span>
                        <div style="min-width:0;">
                            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                                <span class="myearth-level-pill myearth-level-pill--survey">{html.escape(i18n['survey'])}</span>
                                <strong style="color:#ffffff;font-family:OpenSansSemiBold,Arial,sans-serif;font-size:14px;" id="lblSrvName_{sid}">{sname}</strong>
                                <span style="font-size:11.5px;color:#8fa099;">({len(s_task_folders)} Tasks &middot; {len(s_assets)} {html.escape(i18n['assets'])})</span>
                            </div>
                            <div id="lblSrvDesc_{sid}" style="font-size:12px;color:#acbbb0;margin-top:2px;{'display:none;' if not sdesc else ''}">{sdesc}</div>
                        </div>
                    </div>
                    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;" onclick="event.stopPropagation();">
                        <a href="/OpenContainerMosaic.ashx?kind=survey&amp;id={sid}" class="myearth-action-btn myearth-action-btn--accent" title="Open all assets of this Survey together on the 3D Map">
                            {MYEARTH_ICON_GLOBE} Open in 3D Map
                        </a>
                        <button type="button" class="myearth-action-btn myearth-action-btn--primary" data-name="{sname}" onclick="openAddAssetTaskModal({sid}, this.getAttribute('data-name'))">
                            + {html.escape(i18n['add_data'])}
                        </button>
                        <button type="button" class="myearth-action-btn" onclick="toggleInlineBox('editSurvey_{sid}')">
                            <i class="fas fa-pen"></i> Edit {html.escape(i18n['survey'])}
                        </button>
                        <button type="button" class="myearth-action-btn" data-name="{sname}" onclick="shareContainer('survey', {sid}, this.getAttribute('data-name'))">
                            <i class="fas fa-share-alt"></i> Share
                        </button>
                        <button type="button" class="myearth-action-btn myearth-action-btn--danger" data-name="{sname}" onclick="openDeleteContainerModal('survey', {sid}, this.getAttribute('data-name'))" title="Delete Survey">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </div>
                <div id="editSurvey_{sid}" class="myearth-inline-edit" style="margin:10px 16px;" onclick="event.stopPropagation();">
                    <div class="row g-2 align-items-end">
                        <div class="col-md-4 col-12">
                            <label>{html.escape(i18n['survey'])} Name</label>
                            <input type="text" id="srvName_{sid}" class="form-control" value="{sname}" />
                        </div>
                        <div class="col-md-6 col-12">
                            <label>{html.escape(i18n['survey'])} Description</label>
                            <input type="text" id="srvDesc_{sid}" class="form-control" value="{sdesc}" placeholder="General description of this survey (campaign, extraction session, data group...)" />
                        </div>
                        <div class="col-md-2 col-12 text-end">
                            <button type="button" class="myearth-action-btn myearth-action-btn--primary w-100 justify-content-center" style="min-height:34px;" onclick="saveSurveyMeta({sid})">SAVE</button>
                        </div>
                    </div>
                </div>
                <div id="srvBody_{sid}">
                    {active_tasks_html}
                    <div>
                        {''.join(task_folder_blocks_html) if task_folder_blocks_html else f'<div style="padding:14px 16px;font-size:12px;color:#8fa099;">No tasks or assets in this {html.escape(i18n["survey"].lower())} yet. Click <strong>+ {html.escape(i18n["add_data"])}</strong> to create a Direct Import, Photogrammetry, or Gaussian Splatting task subfolder.</div>'}
                    </div>
                </div>
            </div>""")

        is_desktop_mode = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
        local_badge_html = (
            '<span class="myearth-level-pill" style="background:rgba(153,204,0,0.18);border:1px solid #99CC00;color:#99CC00;">LOCAL PC &middot; 0.00 &euro;</span>'
            if is_desktop_mode
            else ""
        )
        sync_cloud_btn_html = (
            f'<button type="button" class="myearth-action-btn myearth-action-btn--primary" data-name="{pname}" onclick="syncLocalProjectToCloud({pid}, this.getAttribute(\'data-name\'))" title="Upload &amp; sync this Local Project to your MosaicEarth Cloud account"><i class="fas fa-cloud-upload-alt"></i> Pass to Cloud</button>'
            if is_desktop_mode
            else ""
        )

        tree_nodes_html.append(f"""
        <div class="myearth-project-node project-tree-item" id="projectNode_{pid}" data-project-id="{pid}" data-project-name="{pname.lower()}">
            <div class="myearth-project-header" onclick="toggleProjectBody({pid})">
                <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;min-width:0;">
                    <span id="projCaret_{pid}" class="myearth-caret is-collapsed">{MYEARTH_ICON_CHEVRON}</span>
                    <span class="myearth-icon-badge myearth-icon-project">{MYEARTH_ICON_PROJECT}</span>
                    <div style="min-width:0;">
                        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                            <span class="myearth-level-pill myearth-level-pill--project">{html.escape(i18n['project'])}</span>
                            {local_badge_html}
                            <strong style="font-size:15.5px;color:#ffffff;font-family:OpenSansSemiBold,Arial,sans-serif;" id="lblProjName_{pid}">{pname}</strong>
                            <span style="font-size:12px;color:#8fa099;">({len(surveys)} {html.escape(i18n['surveys'])} &middot; {total_assets} {html.escape(i18n['assets'])})</span>
                        </div>
                        <div id="lblProjDesc_{pid}" style="font-size:12px;color:#acbbb0;margin-top:2px;{'display:none;' if not pdesc else ''}">{pdesc}</div>
                    </div>
                </div>
                <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;" onclick="event.stopPropagation();">
                    <a href="/OpenContainerMosaic.ashx?kind=project&amp;id={pid}" class="myearth-action-btn myearth-action-btn--accent" title="Open all Surveys &amp; Assets of this Project together on the 3D Map">
                        {MYEARTH_ICON_GLOBE} Open in 3D Map
                    </a>
                    {sync_cloud_btn_html}
                    <button type="button" class="myearth-action-btn myearth-action-btn--primary" data-name="{pname}" onclick="openNewSurveyModal({pid}, this.getAttribute('data-name'))">
                        {html.escape(i18n['new_survey'])}
                    </button>
                    <button type="button" class="myearth-action-btn" onclick="toggleInlineBox('editProj_{pid}')">
                        <i class="fas fa-pen"></i> Edit {html.escape(i18n['project'])}
                    </button>
                    <button type="button" class="myearth-action-btn" data-name="{pname}" onclick="shareContainer('project', {pid}, this.getAttribute('data-name'))">
                        <i class="fas fa-share-alt"></i> Share
                    </button>
                    <button type="button" class="myearth-action-btn myearth-action-btn--danger" data-name="{pname}" onclick="openDeleteContainerModal('project', {pid}, this.getAttribute('data-name'))" title="Delete Project">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            </div>
            <div id="editProj_{pid}" class="myearth-inline-edit" style="margin:12px 18px;" onclick="event.stopPropagation();">
                <div class="row g-2 align-items-end">
                    <div class="col-md-4 col-12">
                        <label>{html.escape(i18n['project'])} Name</label>
                        <input type="text" id="projNameInput_{pid}" class="form-control" value="{pname}" />
                    </div>
                    <div class="col-md-6 col-12">
                        <label>General Description</label>
                        <input type="text" id="projDescInput_{pid}" class="form-control" value="{pdesc}" placeholder="General description of the project..." />
                    </div>
                    <div class="col-md-2 col-12 text-end">
                        <button type="button" class="myearth-action-btn myearth-action-btn--primary w-100 justify-content-center" style="min-height:34px;" onclick="saveProjectMeta({pid})">SAVE</button>
                    </div>
                </div>
            </div>
            <div id="projBody_{pid}" style="display:none;padding-bottom:6px;">
                {''.join(survey_blocks_html) if survey_blocks_html else f'<div style="padding:16px 20px;font-size:12.5px;color:#8fa099;">This project has no surveys yet. Click <strong>{html.escape(i18n["new_survey"])}</strong> to create your first survey subfolder.</div>'}
            </div>
        </div>""")

    # Build Private Quota Assets rows (sorted from largest MB to smallest)
    quota_rows_html: List[str] = []
    for qa in private_quota_assets:
        qa_id_str = html.escape(f"q_{qa['id']}")
        qa_name = html.escape(str(qa.get("name") or "Private Asset"))
        qa_proj = html.escape(str(qa.get("project_name") or "Project"))
        qa_srv = html.escape(str(qa.get("survey_name") or "Survey"))
        qa_svg = str(qa.get("icon_svg") or ICON_ORTHOPHOTO)
        qa_type_lbl = html.escape(str(qa.get("type_label") or "Asset"))
        qa_size_mb = float(qa.get("size_mb") or 0.0)
        qa_task_pid = int(qa.get("task_project_id") or 0)
        qa_ft = int(qa.get("file_type_id") or 2)
        qa_ext_aid = int(qa.get("external_asset_id") or 0)
        qa_det_url = html.escape(str(qa.get("detail_url") or qa.get("viewer_url") or "#"))
        qa_v_url = html.escape(str(qa.get("viewer_url") or "#"))
        quota_rows_html.append(f"""
        <div class="myearth-asset-row">
            <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:260px;">
                <span class="myearth-asset-icon-box" title="{qa_type_lbl}">{qa_svg}</span>
                <div>
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                        <a href="{qa_det_url}" style="color:#ffffff;font-family:OpenSansSemiBold,Arial,sans-serif;font-size:13.5px;text-decoration:none;">{qa_name}</a>
                        <span class="myearth-vis-chip myearth-vis-chip--private">{qa_size_mb:.1f} MB {'ON THIS COMPUTER' if os.environ.get('MOSAICEARTH_DESKTOP_SHARED_UI') == '1' else 'OF PRIVATE STORAGE'}</span>
                    </div>
                    <div style="font-size:11.5px;color:#8fa099;margin-top:2px;">
                        Location in MyEarth: <strong style="color:#dce6dc;">{qa_proj}</strong> &rarr; <strong style="color:#b5d979;">{qa_srv}</strong> &middot; {qa_type_lbl}
                    </div>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                <button type="button" class="myearth-action-btn myearth-action-btn--primary" onclick="quickPublishFreeQuotaAsset({qa_task_pid}, {qa_ft}, {qa_ext_aid})" title="Publish Free on Global Map to free up {qa_size_mb:.1f} MB of Private Quota">
                    {MYEARTH_ICON_GLOBE} Publish Free (0 € &rarr; 0 GiB Quota)
                </button>
                <a href="{qa_det_url}" class="myearth-action-btn">Asset Page</a>
                <a href="{qa_v_url}" class="myearth-action-btn">3D View</a>
                <button type="button" class="myearth-action-btn myearth-action-btn--danger" data-name="{qa_name}" onclick="openDeleteAssetModal({qa_task_pid}, {qa_ft}, {qa_ext_aid}, this.getAttribute('data-name'))" title="Delete Asset">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        </div>""")

    # Build Acquired Assets rows using corporate SVG icons
    acq_rows_html: List[str] = []
    for acq in acquired_assets:
        aname = html.escape(str(acq.get("name") or "Acquired Asset"))
        acreator = html.escape(str(acq.get("creator") or "Creator"))
        adate = html.escape(str(acq.get("date") or ""))
        a_svg = str(acq.get("icon_svg") or ICON_ORTHOPHOTO)
        av_url = html.escape(str(acq.get("viewer_url") or "/en/Map"))
        adl_url = html.escape(str(acq.get("download_url") or "#"))
        afly_url = html.escape(str(acq.get("fly_url") or "/Fly.aspx"))
        acq_rows_html.append(f"""
        <div class="myearth-asset-row">
            <div style="display:flex;align-items:center;gap:12px;">
                <span class="myearth-asset-icon-box">{a_svg}</span>
                <div>
                    <a href="{av_url}" style="color:#ffffff;font-family:OpenSansSemiBold,Arial,sans-serif;text-decoration:none;">{aname}</a>
                    <div style="font-size:11.5px;color:#8fa099;">Creator: <strong style="color:#dce6dc;">{acreator}</strong> &middot; Acquired: {adate}</div>
                </div>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
                <a href="{av_url}" class="myearth-action-btn">3D View</a>
                <a href="{afly_url}" class="myearth-action-btn myearth-action-btn--accent"><i class="fas fa-plane-departure"></i> Fly</a>
                <a href="{adl_url}" download class="myearth-action-btn myearth-action-btn--primary"><i class="fas fa-download"></i> Download</a>
            </div>
        </div>""")

    is_desktop_local = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
    is_free_plan_myearth = (not is_desktop_local) and (str(storage.get("plan_code") or "free").lower() == "free")
    if is_free_plan_myearth:
        myearth_share_title = "Subscription Required to Share"
        myearth_share_body = """
        <div style="background:#131b16;border:1px solid #99CC00;border-radius:8px;padding:16px;">
            <div style="font-size:13px;font-weight:700;color:#99CC00;margin-bottom:6px;"><i class="fas fa-lock me-1"></i> Subscriber-Only Feature (Pro &amp; Studio Plans)</div>
            <p style="font-size:13px;color:#dce6dc;margin-bottom:10px;line-height:1.55;">
                Sharing <strong id="lblShareKind" style="color:#99CC00;">Project / Survey</strong> via direct 3D client links is only available for <strong>Pro (19 &euro;/mo)</strong> and <strong>Studio (59 &euro;/mo)</strong> subscribers.
            </p>
            <p style="font-size:12px;color:#9ca3af;margin-bottom:0;line-height:1.5;">
                With a subscription, you can share interactive 3D Projects, Surveys, and MyMosaics with clients while keeping your private source files protected in <strong>3D View-Only (non-downloadable)</strong> mode.
            </p>
            <input type="hidden" id="txtShareContainerUrl" value="" />
        </div>"""
        myearth_share_btn = f'<a href="/{norm_lang}/CreditPacks" class="btn btn-primary" style="text-decoration:none;display:inline-block;">VIEW SUBSCRIPTION PLANS</a>'
    elif is_desktop_local:
        myearth_share_title = "Local scene link"
        myearth_share_body = """
        <div style="background:#101713;border:1px solid #99CC00;border-radius:8px;padding:14px;margin-bottom:10px;">
            <div style="font-size:12px;font-weight:700;color:#99CC00;margin-bottom:4px;"><i class="fas fa-network-wired me-1"></i> Opens on this computer only</div>
            <p style="font-size:12.5px;color:#dce6dc;margin-bottom:8px;line-height:1.5;">
                This link opens the <strong id="lblShareKind" style="color:#99CC00;">Project / Survey</strong> in this local Desktop. It is not accessible from other computers.
            </p>
            <p style="font-size:11.5px;color:#9ca3af;margin-bottom:0;line-height:1.45;">
                To share online, use <strong>Pass to Cloud</strong>, wait for the complete copy to finish and open the cloud copy to choose its sharing settings.
            </p>
        </div>
        <input type="text" id="txtShareContainerUrl" class="form-control" style="color:#99CC00;border-color:#99CC00;font-size:12.5px;" readonly />"""
        myearth_share_btn = '<button type="button" class="btn btn-primary" onclick="navigator.clipboard.writeText(document.getElementById(\'txtShareContainerUrl\').value);toastr.success(\'Local link copied — opens on this computer only.\');">COPY LOCAL LINK</button>'
    else:
        myearth_share_title = "Share 3D Scene"
        myearth_share_body = """
        <p style="font-size:13px;color:#dce6dc;">
            Share a direct 3D Globe link for <strong id="lblShareKind" style="color:#99CC00;">Project / Survey</strong>. Public assets are visible to everyone; sharing <strong>Private</strong> assets with clients in View-Only 3D mode (non-downloadable) is included in your subscription.
        </p>
        <input type="text" id="txtShareContainerUrl" class="form-control" style="color:#99CC00;border-color:#99CC00;font-size:12.5px;" readonly />"""
        myearth_share_btn = '<button type="button" class="btn btn-primary" onclick="navigator.clipboard.writeText(document.getElementById(\'txtShareContainerUrl\').value);toastr.success(\'3D Share Link copied!\');">COPY 3D LINK</button>'

    desktop_hybrid_bar_html = ""
    desktop_cloud_projects_section_html = ""
    if is_desktop_local:
        from mosaicearth_platform_api import version as desktop_release
        cloud_acc = store.get_desktop_cloud_account()
        cloud_origin_url = os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", "https://app.mosaicearth.app").rstrip("/")
        if cloud_acc and cloud_acc.get("email"):
            cloud_status_html = f"""
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <span class="myearth-level-pill" style="background:rgba(153,204,0,0.18);border:1px solid #99CC00;color:#99CC00;">CLOUD CONNECTED</span>
                <span style="font-size:12.5px;color:#fff;"><strong>{html.escape(str(cloud_acc.get('email')))}</strong> ({html.escape(str(cloud_acc.get('plan_name') or 'Cloud Account'))})</span>
                <button type="button" class="myearth-action-btn myearth-action-btn--danger" onclick="disconnectDesktopCloudAccount()">Disconnect Cloud</button>
            </div>"""
        else:
            cloud_status_html = """
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <span style="font-size:12px;color:#9ca3af;">Not signed in to MosaicEarth Cloud yet</span>
                <button type="button" class="myearth-action-btn myearth-action-btn--primary" onclick="$('#modalDesktopCloudLogin').modal('show')">
                    <i class="fas fa-cloud"></i> Sign In to Cloud Account (View Cloud Projects &amp; Sync)
                </button>
            </div>"""

        desktop_hybrid_bar_html = f"""
        <div style="background:linear-gradient(90deg, rgba(153,204,0,0.12) 0%, rgba(16,23,19,0.96) 100%);border:1px solid rgba(153,204,0,0.45);border-radius:10px;padding:14px 18px;margin-bottom:16px;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:14px;">
            <div>
                <div style="font-size:10.5px;font-weight:700;color:#99CC00;text-transform:uppercase;letter-spacing:0.06em;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                    <span>MOSAICEARTH DESKTOP WORKSTATION &middot; LOCAL DISK + NODEODX / OPENSPLAT + CLOUD SYNC</span>
                    <span style="background:rgba(153,204,0,0.2);border:1px solid #99CC00;color:#fff;padding:1px 8px;border-radius:999px;font-size:10px;">{html.escape(desktop_release()['version'])}</span>
                </div>
                <div style="font-size:13px;color:#fff;margin-top:4px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                    <span>Local Photogrammetry Engine: <strong id="lblMyEarthNodeOdmStatus" style="color:#99CC00;">Checking NodeODX…</strong></span>
                    <button type="button" class="myearth-action-btn" onclick="activateLocalNodeOdmInMyEarth()" style="padding:3px 10px;font-size:11px;">
                        <i class="fas fa-check-circle"></i> Verify Local Engine
                    </button>
                    <button type="button" class="myearth-action-btn" onclick="openDesktopLocalFolder()" style="padding:3px 10px;font-size:11px;">
                        <i class="fas fa-folder-open"></i> Open Local Disk Folder
                    </button>
                    <button type="button" id="btnForceDesktopUpdate" class="myearth-action-btn myearth-action-btn--accent" onclick="forceUpdateDesktopApp()" style="padding:3px 10px;font-size:11px;" title="Check the published version without replacing this installation">
                        <i class="fas fa-sync-alt"></i> Check for updates
                    </button>
                </div>
            </div>
            {cloud_status_html}
        </div>"""

        desktop_cloud_projects_section_html = f"""
        <div class="myearth-project-node" style="margin-top:22px;border-color:rgba(153,204,0,0.45);">
            <div class="myearth-project-header" style="cursor:default;background:rgba(153,204,0,0.07);">
                <div style="display:flex;align-items:center;gap:12px;">
                    <span class="myearth-icon-badge myearth-icon-project">{MYEARTH_ICON_GLOBE}</span>
                    <div>
                        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                            <span class="myearth-level-pill" style="background:rgba(56,189,248,0.16);border:1px solid #38bdf8;color:#38bdf8;">CLOUD WORKSPACE</span>
                            <strong style="font-size:15.5px;color:#ffffff;font-family:OpenSansSemiBold,Arial,sans-serif;">MyEarth Cloud Projects ({html.escape(cloud_origin_url)})</strong>
                        </div>
                        <div style="font-size:12px;color:#acbbb0;margin-top:2px;">
                            Projects stored in your MosaicEarth Cloud account. You can also click <strong>Pass to Cloud</strong> on any Local PC project above to upload it to your Cloud workspace.
                        </div>
                    </div>
                </div>
            </div>
            <div id="desktopCloudProjectsHost" style="padding:16px 20px;">
                {'<div style="color:#9ca3af;font-size:13px;">Loading your Cloud Projects from MosaicEarth Cloud...</div>' if (cloud_acc and cloud_acc.get("email")) else '<div style="color:#8fa099;font-size:13px;">Sign in with your MosaicEarth Cloud account above to view and manage your Cloud Projects alongside your Local PC Projects.</div>'}
            </div>
        </div>

        <div class="modal fade mosaic-workflow" id="modalDesktopCloudLogin" tabindex="-1" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">Connect Your MosaicEarth Cloud Account</h5>
                    </div>
                    <div class="modal-body">
                        <p class="workflow-help" style="margin-bottom:12px;">
                            Sign in with your <strong>MosaicEarth Cloud</strong> account (<code>{html.escape(cloud_origin_url)}</code>) to view your Cloud Projects &amp; MyMosaics here, sync Local PC projects to the Cloud, or process photographs in the Cloud.
                        </p>
                        <div class="form-group mb-3">
                            <label>Email *</label>
                            <input type="email" class="form-control" id="txtDesktopCloudEmail" placeholder="you@domain.com" />
                        </div>
                        <div class="form-group mb-0">
                            <label>Password *</label>
                            <input type="password" class="form-control" id="txtDesktopCloudPassword" placeholder="••••••••" />
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                        <button type="button" class="btn btn-primary" onclick="connectDesktopCloudAccount()">CONNECT CLOUD ACCOUNT</button>
                    </div>
                </div>
            </div>
        </div>"""

    is_admin_user = (not is_desktop_local) and bool(int(user.get("ROL_ID") or 0) == 1 or user.get("IS_ADMIN") or user.get("IsAdmin"))
    admin_shortcut_bar_html = ""
    if is_admin_user:
        admin_shortcut_bar_html = f"""
                            <div style="background:linear-gradient(135deg,rgba(153,204,0,0.12) 0%,rgba(16,185,129,0.08) 100%);border:1px solid rgba(153,204,0,0.42);border-radius:10px;padding:12px 16px;margin-bottom:14px;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;">
                                <div style="font-size:12.5px;color:#e4efe7;">
                                    <strong style="color:#99CC00;"><i class="fas fa-shield-alt me-1"></i>Platform Administrator ({html.escape(str(user.get("EMAIL") or ""))})</strong> &middot;
                                    <span><strong>MyEarth</strong> displays only projects owned personally by this account. To inspect and manage all platform users, projects, tasks, assets, finances and integrations, open the <strong>Admin Control Center</strong>.</span>
                                </div>
                                <a href="/Admin/ControlCenter.aspx" class="myearth-action-btn myearth-action-btn--primary" style="padding:6px 14px;font-size:12px;text-decoration:none;white-space:nowrap;">
                                    <i class="fas fa-sliders-h me-1"></i>OPEN ADMIN CONTROL CENTER
                                </a>
                            </div>"""

    quick_dest_options: List[str] = [
        f'<option value="auto" selected>&#10024; Auto-create new {html.escape(i18n["project"])} &amp; {html.escape(i18n["survey"])} automatically (rename anytime)</option>'
    ]
    for _p in projects_tree:
        _pid = int(_p["id"])
        _pname = html.escape(str(_p["name"]))
        for _s in (_p.get("surveys") or []):
            _sid = int(_s["id"])
            _sname = html.escape(str(_s["name"]))
            quick_dest_options.append(f'<option value="survey:{_sid}">{_pname} &rsaquo; {_sname}</option>')
        quick_dest_options.append(f'<option value="project:{_pid}">{_pname} &rsaquo; + Auto-create new {html.escape(i18n["survey"])}</option>')
    quick_dest_options_html = "".join(quick_dest_options)

    body_content = f"""
    <section id="projects" class="workspace-section">
        <div class="container">
            <div class="row" style="margin-bottom: 15px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
                                <div>MyEarth &middot; {html.escape(i18n['projects'])}, {html.escape(i18n['surveys'])}, Tasks &amp; {html.escape(i18n['assets'])}</div>
                                <div class="d-flex flex-wrap gap-2">
                                    {('<a href="/Admin/ControlCenter.aspx" class="myearth-action-btn myearth-action-btn--accent" style="padding:7px 14px;font-size:12px;text-decoration:none;"><i class="fas fa-shield-alt me-1"></i>ADMIN PANEL</a>' if is_admin_user else '')}
                                    <button type="button" id="btnQuickNewTask" class="myearth-action-btn myearth-action-btn--primary" style="padding:7px 16px;font-size:12px;background:#99CC00;color:#0b110d;font-weight:800;border-color:#99CC00;" onclick="openQuickNewTaskModal()" title="Jump straight to the Task Selector — Project and Survey are created or selected automatically"><i class="fas fa-bolt me-1"></i>+ NEW TASK</button>
                                    <button type="button" class="myearth-action-btn" style="padding:7px 14px;font-size:12px;border-color:rgba(153,204,0,0.45);color:#dce6dc;" onclick="openNewProjectModal()">+ NEW {html.escape(i18n['project'].upper())}</button>
                                </div>
                            </div>
                        </div>

                        <div class="content-box" style="padding: 20px 24px;">
                            {admin_shortcut_bar_html}
                            {desktop_hybrid_bar_html}
                            <!-- Private Storage Quota Bar -->
                            <div class="myearth-quota-bar">
                                <div>
                                    <div style="font-size:10.5px;font-family:OpenSansBold,Arial,sans-serif;color:#99CC00;text-transform:uppercase;letter-spacing:0.08em;">
                                        PRIVATE CLOUD STORAGE QUOTA ({html.escape(storage['plan_name'])})
                                    </div>
                                    <div style="font-size:14px;color:#ffffff;font-family:OpenSansSemiBold,Arial,sans-serif;margin-top:3px;">
                                        {storage['used_gb']:.2f} GiB <span style="color:#8fa099;font-family:OpenSansRegular,Arial,sans-serif;">used of</span> {storage['quota_gb']:.2f} GiB Private Quota
                                        <span style="font-size:12px;color:#b5d979;margin-left:10px;">&middot; Public Global Map Assets ({storage['public_asset_count']}): <strong>0 GiB Quota Used</strong></span>
                                    </div>
                                    <div style="font-size:12px;color:#acbbb0;margin-top:3px;">
                                        Structured in four levels: <strong>{html.escape(i18n['projects'])} &rarr; {html.escape(i18n['surveys'])} &rarr; Tasks &rarr; {html.escape(i18n['assets'])}</strong>. Assets published to the Global Map (Free 0 &euro; or Marketplace &euro;) consume 0 GiB of private quota and earn 80% creator revenue.
                                    </div>
                                </div>
                                <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;">
                                    <div class="myearth-quota-track">
                                        <div class="myearth-quota-fill" style="width:{max(4.0, storage['used_pct']):.1f}%;"></div>
                                    </div>
                                    <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;">
                                        <button type="button" class="myearth-action-btn myearth-action-btn--accent" onclick="setMyEarthView('quota')" style="padding:3px 10px;font-size:11px;">
                                            {MYEARTH_ICON_QUOTA} Manage Private Quota Assets ({len(private_quota_assets)})
                                        </button>
                                        <button type="button" class="myearth-action-btn" onclick="purchaseExtraStorage(250)" style="padding:3px 10px;font-size:11px;border-color:#99CC00;color:#99CC00;" title="Add +250 GiB Private Cloud Storage (9 €/month)">
                                            <i class="fas fa-hdd"></i> +250 GiB Extra (9 &euro;/mo)
                                        </button>
                                        <button type="button" class="myearth-action-btn" onclick="purchaseExtraStorage(1000)" style="padding:3px 10px;font-size:11px;border-color:#99CC00;color:#99CC00;" title="Add +1,000 GiB Private Cloud Storage (29 €/month)">
                                            <i class="fas fa-hdd"></i> +1,000 GiB Extra (29 &euro;/mo)
                                        </button>
                                        <a href="/{norm_lang}/CreditPacks" style="font-size:11.5px;color:#99CC00;font-family:OpenSansSemiBold,Arial,sans-serif;text-decoration:none;">
                                            Plans &amp; Extra Storage &rarr;
                                        </a>
                                    </div>
                                </div>
                            </div>

"""
    if is_desktop_local:
        from mosaicearth_platform_context import storage_status_html
        storage_bar_html = storage_status_html()

    body_content = f"""
    <section id="projects" class="workspace-section">
        <div class="container">
            <div class="row" style="margin-bottom: 15px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
                                <div>MyEarth &middot; {html.escape(i18n['projects'])}, {html.escape(i18n['surveys'])}, Tasks &amp; {html.escape(i18n['assets'])}</div>
                                <div class="d-flex flex-wrap gap-2">
                                    <button type="button" class="myearth-action-btn myearth-action-btn--primary" style="padding:7px 16px;font-size:12px;" onclick="openNewProjectModal()">+ NEW {html.escape(i18n['project'].upper())}</button>
                                </div>
                            </div>
                        </div>

                        <div class="content-box" style="padding: 20px 24px;">
                            {desktop_hybrid_bar_html}
                            {storage_bar_html}

                            <!-- Toolbar: Search + Expand/Collapse + View Mode Switcher -->
                            <div class="myearth-toolbar">
                                <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;flex:1;min-width:260px;">
                                    <div class="myearth-search-wrap">
                                        <i class="fas fa-search search-icon"></i>
                                        <input type="search" id="myEarthSearch" placeholder="Search {html.escape(i18n['projects'])}, {html.escape(i18n['surveys'])}, Tasks or {html.escape(i18n['assets'])}..." oninput="filterMyEarthTree(this.value)" />
                                    </div>
                                    <button type="button" class="myearth-action-btn" onclick="expandCollapseAllMyEarth(true)" title="Expand all Projects">
                                        <i class="fas fa-angle-double-down"></i> Expand All
                                    </button>
                                    <button type="button" class="myearth-action-btn" onclick="expandCollapseAllMyEarth(false)" title="Collapse all Projects">
                                        <i class="fas fa-angle-double-up"></i> Collapse All
                                    </button>
                                </div>
                                <div class="myearth-view-switcher">
                                    <button type="button" id="btnViewTree" class="myearth-view-btn is-active" onclick="setMyEarthView('tree')">
                                        {MYEARTH_ICON_PROJECT} <span>Tree View ({len(projects_tree)})</span>
                                    </button>
                                    <button type="button" id="btnViewQuota" class="myearth-view-btn" onclick="setMyEarthView('quota')">
                                        {MYEARTH_ICON_QUOTA} <span>Local files &middot; {storage['used_gb']:.2f} GiB ({len(private_quota_assets)})</span>
                                    </button>
                                    <button type="button" id="btnViewAcquired" class="myearth-view-btn" onclick="setMyEarthView('acquired')">
                                        {MYEARTH_ICON_ACQUIRED} <span>{html.escape(i18n['acquired_assets'])} ({len(acquired_assets)})</span>
                                    </button>
                                </div>
                            </div>

                            <!-- 1) Expandable Tree View (Default) -->
                            <div id="myEarthTreeView">
                                {''.join(tree_nodes_html) if tree_nodes_html else '<div style="padding:24px 18px;border:1px dashed rgba(153,204,0,0.38);border-radius:10px;background:rgba(16,23,19,0.65);display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:14px;"><div style="color:#acbbb0;font-size:13.5px;">No projects yet. Jump straight into the <strong>Task Selector</strong> (Project &amp; Survey are created automatically) or create an empty Project container first.</div><div style="display:flex;gap:8px;flex-wrap:wrap;"><button type="button" class="myearth-action-btn myearth-action-btn--primary" style="background:#99CC00;color:#0b110d;font-weight:800;border-color:#99CC00;padding:8px 16px;" onclick="openQuickNewTaskModal()"><i class="fas fa-bolt me-1"></i>+ NEW TASK (QUICK START)</button><button type="button" class="myearth-action-btn" onclick="openNewProjectModal()">+ NEW PROJECT</button></div></div>'}
                                {desktop_cloud_projects_section_html}
                            </div>

                            <!-- 2) Private Storage Quota Manager View -->
                            <div id="myEarthQuotaView" style="display:none;">
                                <div class="myearth-project-node">
                                    <div class="myearth-project-header" style="cursor:default;">
                                        <div style="display:flex;align-items:center;gap:12px;">
                                            <span class="myearth-icon-badge myearth-icon-project">{MYEARTH_ICON_QUOTA}</span>
                                            <div>
                                                <strong style="font-size:15.5px;color:#ffffff;font-family:OpenSansSemiBold,Arial,sans-serif;">Private Storage Quota Manager ({storage['used_gb']:.2f} GiB used of {storage['quota_gb']:.2f} GiB)</strong>
                                                <div style="font-size:12px;color:#acbbb0;">All your Private assets sorted from largest to smallest. Switch any asset to <strong>Public Free (0 €)</strong> or <strong>Marketplace (€)</strong> to immediately reduce its private quota usage to <strong>0 GiB</strong>, or delete unneeded files.</div>
                                            </div>
                                        </div>
                                        <a href="/{norm_lang}/CreditPacks" class="myearth-action-btn myearth-action-btn--accent">Upgrade Storage Plan &rarr;</a>
                                    </div>
                                    <div>
                                        {''.join(quota_rows_html) if quota_rows_html else '<div style="padding:18px;color:#8fa099;font-size:13px;">You currently have 0 Private assets consuming your 5 GiB Private Storage Quota. All your published Global Map assets consume 0 GiB of quota!</div>'}
                                    </div>
                                </div>
                            </div>

                            <!-- 3) Acquired Assets View -->
                            <div id="myEarthAcquiredView" style="display:none;">
                                <div class="myearth-project-node">
                                    <div class="myearth-project-header" style="cursor:default;">
                                        <div style="display:flex;align-items:center;gap:12px;">
                                            <span class="myearth-icon-badge myearth-icon-project">{MYEARTH_ICON_ACQUIRED}</span>
                                            <div>
                                                <strong style="font-size:15.5px;color:#ffffff;font-family:OpenSansSemiBold,Arial,sans-serif;">{html.escape(i18n['acquired_assets'])}</strong>
                                                <div style="font-size:12px;color:#acbbb0;">Assets licensed or acquired from other creators on the Global 3D Map</div>
                                            </div>
                                        </div>
                                        <a href="/{norm_lang}/Map" class="myearth-action-btn myearth-action-btn--accent">{MYEARTH_ICON_GLOBE} Explore Global 3D Map &rarr;</a>
                                    </div>
                                    <div>
                                        {''.join(acq_rows_html) if acq_rows_html else '<div style="padding:18px;color:#8fa099;font-size:13px;">You have not acquired any external Marketplace or Free community assets yet. Explore the Global 3D Map to acquire datasets.</div>'}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </section>

    <!-- Modal 1: Level 1 — Create Project (Project Name + General Description) -->
    <div class="modal fade mosaic-workflow" id="modalCreateProject" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">New {html.escape(i18n['project'])}</h5>
                </div>
                <div class="modal-body">
                    <p class="workflow-help" style="margin-bottom:14px;">Group the surveys and results for one site or job.</p>
                    <div class="form-group mb-3">
                        <label for="txtNewProjectName">{html.escape(i18n['project'])} Name *</label>
                        <input type="text" class="form-control" id="txtNewProjectName" placeholder="e.g. Llera del riu Segre" />
                    </div>
                    <div class="form-group mb-0">
                        <label for="txtNewProjectDesc">General Description</label>
                        <textarea class="form-control" id="txtNewProjectDesc" rows="3" placeholder="General description and scope of this project..."></textarea>
                    </div>
                </div>
                <div class="modal-footer">
                    <div class="row w-100">
                        <div class="col-6">
                            <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                        </div>
                        <div class="col-6 text-end">
                            <button type="button" class="btn btn-primary" onclick="submitCreateProjectContainer()">CREATE {html.escape(i18n['project'].upper())}</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal 2: Level 2 — Create Survey inside a Project (Survey Name + Description) -->
    <div class="modal fade mosaic-workflow" id="modalCreateSurvey" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="lblCreateSurveyModalTitle">New {html.escape(i18n['survey'])}</h5>
                </div>
                <div class="modal-body">
                    <input type="hidden" id="hdnSurveyParentProjectId" value="0" />
                    <div id="surveyParentProjectBanner" style="margin-bottom:14px;padding:9px 12px;border-radius:6px;background:#101713;border:1px solid #40634f;color:#b5d979;font-size:12px;font-family:OpenSansSemiBold,Arial,sans-serif;"></div>
                    <div class="form-group mb-3">
                        <label for="txtNewSurveyName">{html.escape(i18n['survey'])} Name *</label>
                        <input type="text" class="form-control" id="txtNewSurveyName" placeholder="e.g. 01 Reconeixement / Campanya Topogràfica" />
                    </div>
                    <div class="form-group mb-0">
                        <label for="txtNewSurveyDesc">{html.escape(i18n['survey'])} Description</label>
                        <textarea class="form-control" id="txtNewSurveyDesc" rows="3" placeholder="Describe this logical or temporal grouping (field campaign, extraction session, data typology...)"></textarea>
                    </div>
                </div>
                <div class="modal-footer">
                    <div class="row w-100">
                        <div class="col-6">
                            <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                        </div>
                        <div class="col-6 text-end">
                            <button type="button" class="btn btn-primary" onclick="submitCreateSurveyContainer()">CREATE {html.escape(i18n['survey'].upper())}</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal 3: Level 3 — Add Task Subfolder into a Survey (3 Exclusive Entry Tasks + Origin Metadata) -->
    <div class="modal fade mosaic-workflow" id="staticBackdrop" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-labelledby="staticBackdropLabel" aria-hidden="true">
        <div class="modal-dialog modal-lg modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="staticBackdropLabel">Add data to {html.escape(i18n['survey'])}</h5>
                </div>
                <div class="modal-body">
                    <input type="hidden" id="hdnParentProjectId" value="0" />
                    <div id="parentProjectBanner" style="margin-bottom:14px;padding:9px 12px;border-radius:6px;background:#101713;border:1px solid #40634f;color:#b5d979;font-size:12px;font-family:OpenSansSemiBold,Arial,sans-serif;"></div>
                    <div id="quickTaskContainerBox" style="display:none;margin-bottom:14px;padding:11px 14px;border-radius:8px;background:#101713;border:1px solid rgba(153,204,0,0.42);">
                        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px;">
                            <label for="selQuickTargetDestination" style="margin:0;font-size:12px;color:#b5d979;font-family:OpenSansSemiBold,Arial,sans-serif;">
                                <i class="fas fa-folder-tree me-1"></i>Destination {html.escape(i18n['project'])} / {html.escape(i18n['survey'])} (automatic by default)
                            </label>
                            <a href="#" id="lnkToggleCustomContainers" onclick="event.preventDefault(); toggleQuickCustomContainerNames();" style="font-size:11.5px;color:#99CC00;text-decoration:none;">
                                Optional: custom {html.escape(i18n['project'])}/{html.escape(i18n['survey'])} names &#9662;
                            </a>
                        </div>
                        <select id="selQuickTargetDestination" class="form-control" onchange="onQuickDestinationChange()">
                            {quick_dest_options_html}
                        </select>
                        <div id="quickCustomContainerFields" class="row g-2 mt-2" style="display:none;">
                            <div class="col-md-6 col-12" id="wrapQuickProjectName">
                                <label for="txtQuickProjectName" style="font-size:11.5px;color:#acbbb0;">{html.escape(i18n['project'])} name (optional)</label>
                                <input type="text" class="form-control form-control-sm" id="txtQuickProjectName" placeholder="Leave blank to auto-name with today's date" />
                            </div>
                            <div class="col-md-6 col-12" id="wrapQuickSurveyName">
                                <label for="txtQuickSurveyName" style="font-size:11.5px;color:#acbbb0;">{html.escape(i18n['survey'])} name (optional)</label>
                                <input type="text" class="form-control form-control-sm" id="txtQuickSurveyName" placeholder="Leave blank to auto-name (e.g. Survey 1)" />
                            </div>
                        </div>
                    </div>

                    <div class="form-group mb-3">
                        <label style="font-family:OpenSansSemiBold,Arial,sans-serif;color:#dce6dc;">1. What would you like to do?</label>
                        <div class="project-kind-picker" role="radiogroup" aria-label="Entry Task Type" style="margin-top:6px;">
                            <label class="project-kind-option">
                                <input type="radio" name="projectKind" value="external" checked />
                                <span class="project-kind-icon" aria-hidden="true">{ICON_ORTHOPHOTO}</span>
                                <span class="project-kind-copy">
                                    <strong>Upload existing data</strong>
                                    <small>Import a finished map, elevation model, point cloud, 3D model or vector layer. Keep its coordinates or place it on the map.</small>
                                    <em>Direct Import</em>
                                </span>
                            </label>
                            <label class="project-kind-option">
                                <input type="radio" name="projectKind" value="processing" />
                                <span class="project-kind-icon" aria-hidden="true">{ICON_CLOUD}</span>
                                <span class="project-kind-copy">
                                    <strong>Create maps and 3D models from photos</strong>
                                    <small>Upload overlapping aerial photos to create an orthophoto. Choose the Complete preset to also generate elevation models, a point cloud and a textured mesh.</small>
                                    <em>Photogrammetry</em>
                                </span>
                            </label>
                            <label class="project-kind-option">
                                <input type="radio" name="projectKind" value="gaussian" />
                                <span class="project-kind-icon" aria-hidden="true">{ICON_3D}</span>
                                <span class="project-kind-copy">
                                    <strong>Create or import a Gaussian Splat</strong>
                                    <small>Create a photorealistic 3D scene from overlapping photos or video, or import an existing PLY, SPZ, SPLAT or KSPLAT Gaussian file.</small>
                                    <em>3D Gaussian Splatting</em>
                                </span>
                            </label>
                        </div>
                    </div>

                    <div class="row g-3 mt-1">
                        <div class="col-md-6 col-12">
                            <div class="form-group mb-0">
                                <label for="inputName" id="lblTaskNameField">Task name *</label>
                                <input type="text" class="form-control" id="inputName" placeholder="e.g. Vol Fotogramètric Octubre / Importació Punts GPS" />
                            </div>
                        </div>
                        <div class="col-md-6 col-12">
                            <div class="form-group mb-0">
                                <label for="inputTaskCreationDate">Capture / creation date</label>
                                <input type="date" class="form-control" id="inputTaskCreationDate" />
                            </div>
                        </div>
                        <div class="col-md-6 col-12">
                            <div class="form-group mb-0">
                                <label for="inputTaskMethodology">Capture method (optional)</label>
                                <input type="text" class="form-control" id="inputTaskMethodology" list="methodologySuggestions" placeholder="e.g. UAV RTK Photogrammetry / Terrestrial GNSS / Historical Imagery" />
                                <datalist id="methodologySuggestions">
                                    <option value="UAV / Drone RTK Photogrammetry"></option>
                                    <option value="Airborne LiDAR / TLS Point Cloud"></option>
                                    <option value="GNSS / RTK Ground Control Points (GCP)"></option>
                                    <option value="3D Gaussian Splatting Multi-View Capture"></option>
                                    <option value="Historical Aerial Orthophoto Digitization"></option>
                                    <option value="GIS / CAD Vector Cartography"></option>
                                </datalist>
                            </div>
                        </div>
                        <div class="col-md-6 col-12">
                            <div class="form-group mb-0">
                                <label for="inputTaskDescription">Description (optional)</label>
                                <input type="text" class="form-control" id="inputTaskDescription" placeholder="Specific notes about sensor, flight altitude, accuracy or source..." />
                            </div>
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <div class="row w-100">
                        <div class="col-6">
                            <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                        </div>
                        <div class="col-6 text-end">
                            <button id="btnCreate" onclick="submitMyEarthCreate();" type="button" class="btn btn-primary" style="font-size: 12px!important; padding: 10px 34px 10px 34px;">CREATE TASK &amp; CONTINUE</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal: Private Sharing (Pro / Studio Feature + Public Link) -->
    <div class="modal fade mosaic-workflow" id="modalShareContainer" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="lblShareContainerTitle">{myearth_share_title}</h5>
                </div>
                <div class="modal-body">
                    {myearth_share_body}
                </div>
                <div class="modal-footer">
                    <div class="row w-100">
                        <div class="col-6">
                            <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CLOSE</button>
                        </div>
                        <div class="col-6 text-end">
                            {myearth_share_btn}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Unified Delete Modal for Project / Survey / Task / Asset -->
    <div class="modal fade mosaic-workflow" id="modalDeleteMyEarthItem" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="lblDeleteMyEarthTitle">Confirm Delete</h5>
                </div>
                <div class="modal-body texto-delete text-center" id="lblDeleteMyEarthBody">
                    Are you sure you want to delete this item? This action cannot be undone.
                </div>
                <div class="modal-footer">
                    <div class="row w-100">
                        <div class="col-6">
                            <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                        </div>
                        <div class="col-6 text-end">
                            <button id="btnConfirmDeleteMyEarth" onclick="confirmDeleteMyEarthItem()" type="button" class="btn btn-primary" style="font-size: 12px!important; padding: 10px 34px 10px 34px;">DELETE</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <!-- On-Demand Terrain Derivatives Modal (Hillshade, Slope, Aspect, Contours) -->
    <div class="modal fade mosaic-workflow" id="modalTerrainDerivative" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="lblTerrainDerivativeTitle">Generate Terrain Derivative</h5>
                </div>
                <div class="modal-body" style="font-size:12.5px;color:#dce6dc;">
                    <input type="hidden" id="hdnDerivProjectId" value="0" />
                    <input type="hidden" id="hdnDerivAssetId" value="0" />
                    <p style="margin-bottom:10px;color:#b8c6be;">
                        Generate terrain analysis layers directly from <strong id="lblTerrainDerivativeSource" style="color:#99CC00;">DSM/DTM</strong> without re-running photogrammetry:
                    </p>
                    <div class="mb-3">
                        <label style="font-size:11px;font-weight:700;color:#99CC00;text-transform:uppercase;display:block;margin-bottom:4px;">Derivative Product</label>
                        <select id="selTerrainDerivativeKind" class="form-control" style="background:#0d130f;border:1px solid #35443b;color:#fff;" onchange="document.getElementById('boxContourInterval').style.display = (this.value === 'contours' ? 'block' : 'none');">
                            <option value="hillshade">Hillshade Relief Map (Shaded Relief GeoTIFF + Map Tiles)</option>
                            <option value="slope">Slope Map (degrees · GeoTIFF + Map Tiles)</option>
                            <option value="aspect">Aspect / Orientation (azimuth degrees · GeoTIFF + Map Tiles)</option>
                            <option value="contours">Elevation Contours (GeoJSON lines)</option>
                        </select>
                    </div>
                    <div id="boxContourInterval" class="mb-2" style="display:none;">
                        <label style="font-size:11px;font-weight:700;color:#99CC00;text-transform:uppercase;display:block;margin-bottom:4px;">Contour Interval (meters)</label>
                        <input type="number" id="inpContourIntervalM" class="form-control" value="1.0" min="0.1" max="100" step="0.5" style="background:#0d130f;border:1px solid #35443b;color:#fff;" />
                    </div>
                    <div id="terrainDerivativeStatus" role="status" aria-live="polite" style="margin-top:12px;color:#dce6dc;"></div>
                </div>
                <div class="modal-footer">
                    <div class="row w-100">
                        <div class="col-6">
                            <button type="button" id="btnCloseTerrainDerivative" class="btn btn-cancel" data-bs-dismiss="modal">CLOSE</button>
                        </div>
                        <div class="col-6 text-end">
                            <button id="btnGenerateTerrainDerivative" onclick="submitTerrainDerivative()" type="button" class="btn btn-primary" style="font-size:12px!important;padding:10px 22px;">GENERATE LAYER</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <input type="hidden" id="hdnTranslations" value="{{}}" />"""

    scripts_extra = """
    <script src="/js/front/Projects.js?v=20261015-gaussian-r35"></script>
    <script>
        MYLIBRARY.setUrl_page('/Client/Projects.aspx');
        CargarActiveMenu("li_projects");

        var _pendingDeleteAction = null;

        function _getOpenProjectsMap() {
            try {
                return JSON.parse(sessionStorage.getItem('myearth_open_projects') || '{}') || {};
            } catch (e) {
                return {};
            }
        }
        function _saveOpenProjectsMap(mapObj) {
            try {
                sessionStorage.setItem('myearth_open_projects', JSON.stringify(mapObj || {}));
            } catch (e) {}
        }

        function setMyEarthView(mode) {
            document.getElementById('myEarthTreeView').style.display = (mode === 'tree') ? 'block' : 'none';
            document.getElementById('myEarthQuotaView').style.display = (mode === 'quota') ? 'block' : 'none';
            document.getElementById('myEarthAcquiredView').style.display = (mode === 'acquired') ? 'block' : 'none';
            document.getElementById('btnViewTree').classList.toggle('is-active', mode === 'tree');
            document.getElementById('btnViewQuota').classList.toggle('is-active', mode === 'quota');
            document.getElementById('btnViewAcquired').classList.toggle('is-active', mode === 'acquired');
        }
        function setProjectExpanded(pid, open) {
            var body = document.getElementById('projBody_' + pid);
            var caret = document.getElementById('projCaret_' + pid);
            if (!body) return;
            body.style.display = open ? 'block' : 'none';
            if (caret) caret.classList.toggle('is-collapsed', !open);
            var mapObj = _getOpenProjectsMap();
            if (open) mapObj[String(pid)] = 1;
            else delete mapObj[String(pid)];
            _saveOpenProjectsMap(mapObj);
        }
        function toggleProjectBody(pid) {
            var body = document.getElementById('projBody_' + pid);
            if (!body) return;
            var open = body.style.display === 'none';
            setProjectExpanded(pid, open);
        }
        function expandCollapseAllMyEarth(open) {
            var mapObj = {};
            document.querySelectorAll('.project-tree-item').forEach(function(node) {
                var pid = node.getAttribute('data-project-id');
                if (!pid) return;
                var body = document.getElementById('projBody_' + pid);
                var caret = document.getElementById('projCaret_' + pid);
                if (body) body.style.display = open ? 'block' : 'none';
                if (caret) caret.classList.toggle('is-collapsed', !open);
                if (open) mapObj[String(pid)] = 1;
            });
            _saveOpenProjectsMap(mapObj);
        }
        function toggleSurveyBody(sid) {
            var body = document.getElementById('srvBody_' + sid);
            var caret = document.getElementById('srvCaret_' + sid);
            if (!body) return;
            var open = body.style.display === 'none';
            body.style.display = open ? 'block' : 'none';
            if (caret) caret.classList.toggle('is-collapsed', !open);
        }
        function toggleTaskBody(tid) {
            var body = document.getElementById('taskBody_' + tid);
            var caret = document.getElementById('taskCaret_' + tid);
            if (!body) return;
            var open = body.style.display === 'none';
            body.style.display = open ? 'block' : 'none';
            if (caret) caret.classList.toggle('is-collapsed', !open);
        }
        function toggleInlineBox(id) {
            var el = document.getElementById(id);
            if (el) el.classList.toggle('is-open');
        }
        function filterMyEarthTree(q) {
            var term = (q || '').trim().toLowerCase();
            document.querySelectorAll('.project-tree-item').forEach(function(node) {
                var txt = (node.textContent || '').toLowerCase();
                var match = !term || txt.indexOf(term) >= 0;
                node.style.display = match ? '' : 'none';
                var pid = node.getAttribute('data-project-id');
                if (term && match && pid) {
                    var body = document.getElementById('projBody_' + pid);
                    var caret = document.getElementById('projCaret_' + pid);
                    if (body) body.style.display = 'block';
                    if (caret) caret.classList.remove('is-collapsed');
                }
            });
        }
        function openNewProjectModal() {
            document.getElementById('txtNewProjectName').value = '';
            document.getElementById('txtNewProjectDesc').value = '';
            $('#modalCreateProject').modal('show');
        }
        function focusMyEarthField(id) {
            var field = document.getElementById(id);
            field.scrollIntoView({ block: 'center', behavior: 'instant' });
            field.focus({ preventScroll: true });
        }
        function submitMyEarthCreation(modalSelector, options) {
            var button = document.querySelector(modalSelector + ' .modal-footer .btn-primary');
            if (button && button.disabled) return;
            if (button) button.disabled = true;
            options.timeout = 30000;
            options.error = function() {
                toastr.error('Could not confirm the save. Your entries are kept. Check MyEarth before trying again.');
            };
            options.complete = function() { if (button) button.disabled = false; };
            $.ajax(options);
        }
        function submitCreateProjectContainer() {
            var nameVal = (document.getElementById('txtNewProjectName').value || '').trim();
            var descVal = (document.getElementById('txtNewProjectDesc').value || '').trim();
            if (!nameVal) { toastr.error('Please enter a project name.'); focusMyEarthField('txtNewProjectName'); return; }
            submitMyEarthCreation('#modalCreateProject', {
                type: 'POST',
                url: '/Client/Project.aspx/CreateProjectContainer',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                data: JSON.stringify({ name: nameVal, description: descVal }),
                success: function(res) {
                    try {
                        var d = typeof res.d === 'string' ? JSON.parse(res.d) : res.d;
                        if (d && d.projectId) {
                            var m = _getOpenProjectsMap();
                            m[String(d.projectId)] = 1;
                            _saveOpenProjectsMap(m);
                        }
                    } catch (e) {}
                    window.location.reload();
                }
            });
        }
        function openNewSurveyModal(parentPid, parentName) {
            document.getElementById('hdnSurveyParentProjectId').value = String(parentPid);
            document.getElementById('surveyParentProjectBanner').textContent = 'Project: ' + (parentName || ('#' + parentPid));
            document.getElementById('txtNewSurveyName').value = '';
            document.getElementById('txtNewSurveyDesc').value = '';
            setProjectExpanded(parentPid, true);
            $('#modalCreateSurvey').modal('show');
        }
        function submitCreateSurveyContainer() {
            var parentPid = parseInt(document.getElementById('hdnSurveyParentProjectId').value || '0', 10);
            var nameVal = (document.getElementById('txtNewSurveyName').value || '').trim();
            var descVal = (document.getElementById('txtNewSurveyDesc').value || '').trim();
            if (!nameVal) { toastr.error('Please enter a survey name.'); focusMyEarthField('txtNewSurveyName'); return; }
            setProjectExpanded(parentPid, true);
            submitMyEarthCreation('#modalCreateSurvey', {
                type: 'POST',
                url: '/Client/Project.aspx/CreateSurveyContainer',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                data: JSON.stringify({ projectId: parentPid, name: nameVal, description: descVal }),
                success: function() {
                    window.location.reload();
                }
            });
        }
        window.__meQuickTaskMode = false;
        function toggleQuickCustomContainerNames() {
            var box = document.getElementById('quickCustomContainerFields');
            if (!box) return;
            box.style.display = (box.style.display === 'none' || !box.style.display) ? 'flex' : 'none';
            onQuickDestinationChange();
        }
        function onQuickDestinationChange() {
            var sel = document.getElementById('selQuickTargetDestination');
            if (!sel) return;
            var val = String(sel.value || 'auto');
            var wrapProj = document.getElementById('wrapQuickProjectName');
            var wrapSrv = document.getElementById('wrapQuickSurveyName');
            var toggleLnk = document.getElementById('lnkToggleCustomContainers');
            if (val.indexOf('survey:') === 0) {
                if (toggleLnk) toggleLnk.style.display = 'none';
                var box = document.getElementById('quickCustomContainerFields');
                if (box) box.style.display = 'none';
            } else {
                if (toggleLnk) toggleLnk.style.display = 'inline';
                if (wrapProj) wrapProj.style.display = (val === 'auto') ? 'block' : 'none';
                if (wrapSrv) wrapSrv.style.display = 'block';
            }
        }
        function _defaultQuickTaskTitle(kindVal, dateStr) {
            var d = dateStr || new Date().toISOString().slice(0, 10);
            if (kindVal === 'processing') return 'Photogrammetry · ' + d;
            if (kindVal === 'gaussian') return 'Gaussian Splat · ' + d;
            if (kindVal === 'media') return '360° & Geo-Media · ' + d;
            return 'GIS & 3D Import · ' + d;
        }
        function openQuickNewTaskModal() {
            window.__meQuickTaskMode = true;
            document.getElementById('hdnParentProjectId').value = '0';
            var titleEl = document.getElementById('staticBackdropLabel');
            if (titleEl) titleEl.textContent = 'New Task · Choose What to Upload or Create';
            var b = document.getElementById('parentProjectBanner');
            if (b) b.style.display = 'none';
            var qBox = document.getElementById('quickTaskContainerBox');
            if (qBox) qBox.style.display = 'block';
            var sel = document.getElementById('selQuickTargetDestination');
            if (sel) sel.value = 'auto';
            var customBox = document.getElementById('quickCustomContainerFields');
            if (customBox) customBox.style.display = 'none';
            var pInp = document.getElementById('txtQuickProjectName');
            if (pInp) pInp.value = '';
            var sInp = document.getElementById('txtQuickSurveyName');
            if (sInp) sInp.value = '';
            onQuickDestinationChange();
            var lblName = document.getElementById('lblTaskNameField');
            if (lblName) lblName.textContent = 'Task name (optional — auto-named if left blank)';
            var nameInp = document.getElementById('inputName');
            if (nameInp) {
                nameInp.value = '';
                nameInp.placeholder = 'Optional — leave blank to auto-name by task type & date';
            }
            var todayStr = new Date().toISOString().slice(0, 10);
            document.getElementById('inputTaskCreationDate').value = todayStr;
            document.getElementById('inputTaskMethodology').value = '';
            document.getElementById('inputTaskDescription').value = '';
            $('#staticBackdrop').modal('show');
        }
        function openAddAssetTaskModal(surveyId, surveyName) {
            window.__meQuickTaskMode = false;
            var titleEl = document.getElementById('staticBackdropLabel');
            if (titleEl) titleEl.textContent = 'Add data to Survey';
            var qBox = document.getElementById('quickTaskContainerBox');
            if (qBox) qBox.style.display = 'none';
            var lblName = document.getElementById('lblTaskNameField');
            if (lblName) lblName.textContent = 'Task name *';
            document.getElementById('hdnParentProjectId').value = String(surveyId);
            var b = document.getElementById('parentProjectBanner');
            b.style.display = 'block';
            b.textContent = 'Target Survey: ' + (surveyName || ('#' + surveyId));
            var nameInp = document.getElementById('inputName');
            nameInp.value = '';
            nameInp.placeholder = 'e.g. Vol Fotogramètric Octubre / Importació Punts GPS';
            document.getElementById('inputTaskCreationDate').value = new Date().toISOString().slice(0, 10);
            document.getElementById('inputTaskMethodology').value = '';
            document.getElementById('inputTaskDescription').value = '';
            $('#staticBackdrop').modal('show');
        }
        function submitMyEarthCreate() {
            var surveyId = parseInt(document.getElementById('hdnParentProjectId').value || '0', 10);
            var targetProjectId = 0;
            var quickProjectName = '';
            var quickSurveyName = '';
            var isQuick = !!window.__meQuickTaskMode;
            if (isQuick) {
                var sel = document.getElementById('selQuickTargetDestination');
                var destVal = sel ? String(sel.value || 'auto') : 'auto';
                if (destVal.indexOf('survey:') === 0) {
                    surveyId = parseInt(destVal.slice(7) || '0', 10);
                } else if (destVal.indexOf('project:') === 0) {
                    surveyId = 0;
                    targetProjectId = parseInt(destVal.slice(8) || '0', 10);
                } else {
                    surveyId = 0;
                    targetProjectId = 0;
                }
                var pInp = document.getElementById('txtQuickProjectName');
                var sInp = document.getElementById('txtQuickSurveyName');
                quickProjectName = pInp ? (pInp.value || '').trim() : '';
                quickSurveyName = sInp ? (sInp.value || '').trim() : '';
            }
            var nameVal = (document.getElementById('inputName').value || '').trim();
            var kindEl = document.querySelector('input[name="projectKind"]:checked');
            var kindVal = kindEl ? kindEl.value : 'external';
            var cDate = (document.getElementById('inputTaskCreationDate').value || '').trim();
            var methodVal = (document.getElementById('inputTaskMethodology').value || '').trim();
            var descVal = (document.getElementById('inputTaskDescription').value || '').trim();
            if (!nameVal) {
                if (isQuick) {
                    nameVal = _defaultQuickTaskTitle(kindVal, cDate);
                } else {
                    toastr.error('Please enter a task or asset name.');
                    focusMyEarthField('inputName');
                    return;
                }
            }
            submitMyEarthCreation('#staticBackdrop', {
                type: 'POST',
                url: '/Client/Project.aspx/CreateSurveyTask',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                data: JSON.stringify({
                    surveyId: surveyId,
                    targetProjectId: targetProjectId,
                    quickCreate: isQuick,
                    projectName: quickProjectName,
                    surveyName: quickSurveyName,
                    name: nameVal,
                    projectKind: kindVal,
                    creationDate: cDate,
                    methodology: methodVal,
                    description: descVal
                }),
                success: function(res) {
                    var d = typeof res.d === 'string' ? JSON.parse(res.d) : res.d;
                    if (d && d.projectId) {
                        try {
                            var m = _getOpenProjectsMap();
                            m[String(d.projectId)] = 1;
                            _saveOpenProjectsMap(m);
                        } catch (e) {}
                    }
                    if (d && (d.redirectUrl || d.url)) window.location.href = d.redirectUrl || d.url;
                    else window.location.reload();
                }
            });
        }
        document.addEventListener('DOMContentLoaded', function() {
            try {
                var params = new URLSearchParams(window.location.search || '');
                if (params.get('new_task') === '1') {
                    params.delete('new_task');
                    var cleanQs = params.toString();
                    var cleanUrl = window.location.pathname + (cleanQs ? '?' + cleanQs : '') + (window.location.hash || '');
                    if (window.history && window.history.replaceState) window.history.replaceState({}, '', cleanUrl);
                    setTimeout(function() { openQuickNewTaskModal(); }, 80);
                }
            } catch (e) {}
        });
        function saveProjectMeta(pid) {
            var newName = (document.getElementById('projNameInput_' + pid).value || '').trim();
            var newDesc = (document.getElementById('projDescInput_' + pid).value || '').trim();
            if (!newName) { toastr.error('Project name is required.'); return; }
            $.ajax({
                type: 'POST',
                url: '/Client/Project.aspx/SaveChanges',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                data: JSON.stringify({ id: pid, level: 'project', name: newName, description: newDesc }),
                success: function() {
                    var lbl = document.getElementById('lblProjName_' + pid);
                    if (lbl) lbl.textContent = newName;
                    var dEl = document.getElementById('lblProjDesc_' + pid);
                    if (dEl) {
                        dEl.textContent = newDesc;
                        dEl.style.display = newDesc ? 'block' : 'none';
                    }
                    toggleInlineBox('editProj_' + pid);
                    toastr.success('Project updated.');
                }
            });
        }
        function saveSurveyMeta(sid) {
            var newName = (document.getElementById('srvName_' + sid).value || '').trim();
            var newDesc = (document.getElementById('srvDesc_' + sid).value || '').trim();
            if (!newName) { toastr.error('Survey name is required.'); return; }
            $.ajax({
                type: 'POST',
                url: '/Client/Project.aspx/SaveChanges',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                data: JSON.stringify({ id: sid, level: 'survey', name: newName, description: newDesc }),
                success: function() {
                    var lbl = document.getElementById('lblSrvName_' + sid);
                    if (lbl) lbl.textContent = newName;
                    var dEl = document.getElementById('lblSrvDesc_' + sid);
                    if (dEl) {
                        dEl.textContent = newDesc;
                        dEl.style.display = newDesc ? 'block' : 'none';
                    }
                    toggleInlineBox('editSurvey_' + sid);
                    toastr.success('Survey updated.');
                }
            });
        }
        function saveTaskMeta(tid) {
            var newName = (document.getElementById('taskName_' + tid).value || '').trim();
            var newDate = (document.getElementById('taskDate_' + tid).value || '').trim();
            var newMethod = (document.getElementById('taskMethod_' + tid).value || '').trim();
            var newDesc = (document.getElementById('taskDesc_' + tid).value || '').trim();
            var catEl = document.getElementById('taskCat_' + tid);
            var tagsEl = document.getElementById('taskTags_' + tid);
            var newCat = catEl ? parseInt(catEl.value || '0', 10) : 0;
            var newTags = tagsEl ? (tagsEl.value || '').trim() : '';
            if (!newName) { toastr.error('Task name is required.'); return; }
            $.ajax({
                type: 'POST',
                url: '/Client/Project.aspx/SaveChanges',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                data: JSON.stringify({
                    id: tid,
                    level: 'task',
                    name: newName,
                    creationDate: newDate,
                    methodology: newMethod,
                    description: newDesc,
                    categoryId: newCat,
                    tags: newTags
                }),
                success: function() {
                    toastr.success('Task updated.');
                    window.location.reload();
                }
            });
        }
        function saveMyEarthAsset(taskPid, fileTypeId, externalAssetId, domKey) {
            var title = (document.getElementById('meTitle_' + domKey).value || '').trim();
            var cDate = (document.getElementById('meDate_' + domKey).value || '').trim();
            var methodVal = (document.getElementById('meMethod_' + domKey).value || '').trim();
            var descVal = (document.getElementById('meDesc_' + domKey).value || '').trim();
            var catEl = document.getElementById('meCat_' + domKey);
            var tagsEl = document.getElementById('meTags_' + domKey);
            var catVal = catEl ? parseInt(catEl.value || '0', 10) : 0;
            var tagsVal = tagsEl ? (tagsEl.value || '').trim() : '';
            var lat = (document.getElementById('meLat_' + domKey).value || '').trim();
            var lon = (document.getElementById('meLon_' + domKey).value || '').trim();
            var h = parseFloat(document.getElementById('meHeight_' + domKey).value || '0');
            var vis = document.getElementById('meVis_' + domKey).value || 'private';
            var price = parseFloat(document.getElementById('mePrice_' + domKey).value || '0');
            $.ajax({
                type: 'POST',
                url: '/Client/Project.aspx/UpdateSubprojectAssetQuick',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                data: JSON.stringify({
                    projectId: taskPid,
                    fileTypeId: fileTypeId,
                    externalAssetId: externalAssetId,
                    title: title,
                    creationDate: cDate,
                    methodology: methodVal,
                    description: descVal,
                    categoryId: catVal,
                    tags: tagsVal,
                    lat: lat,
                    lon: lon,
                    height: h,
                    visibilityMode: vis,
                    priceEur: price
                }),
                success: function() {
                    toastr.success('Asset metadata updated.');
                    window.location.reload();
                }
            });
        }
        function quickPublishFreeQuotaAsset(taskPid, fileTypeId, externalAssetId) {
            $.ajax({
                type: 'POST',
                url: '/Client/Project.aspx/UpdateSubprojectAssetQuick',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                data: JSON.stringify({
                    projectId: taskPid,
                    fileTypeId: fileTypeId,
                    externalAssetId: externalAssetId,
                    visibilityMode: 'public_free',
                    priceEur: 0
                }),
                success: function() {
                    toastr.success('Asset published as Public Free (0 € · 0 GiB Private Quota)!');
                    window.location.reload();
                }
            });
        }
        function openDeleteContainerModal(kind, id, name) {
            _pendingDeleteAction = { type: 'container', kind: kind, id: id };
            var labelKind = kind === 'project' ? 'Project' : (kind === 'survey' ? 'Survey' : 'Task');
            document.getElementById('lblDeleteMyEarthTitle').textContent = 'Delete ' + labelKind;
            document.getElementById('lblDeleteMyEarthBody').innerHTML = 'Are you sure you want to delete <strong>' + (name || labelKind) + '</strong> and all of its contents? This action cannot be undone.';
            $('#modalDeleteMyEarthItem').modal('show');
        }
        function openDeleteAssetModal(taskPid, fileTypeId, externalAssetId, name) {
            _pendingDeleteAction = { type: 'asset', projectId: taskPid, fileTypeId: fileTypeId, externalAssetId: externalAssetId };
            document.getElementById('lblDeleteMyEarthTitle').textContent = 'Delete Asset';
            document.getElementById('lblDeleteMyEarthBody').innerHTML = 'Are you sure you want to delete the asset <strong>' + (name || 'Asset') + '</strong>? This action cannot be undone.';
            $('#modalDeleteMyEarthItem').modal('show');
        }
        function confirmDeleteMyEarthItem() {
            if (!_pendingDeleteAction) return;
            if (_pendingDeleteAction.type === 'container') {
                $.ajax({
                    type: 'POST',
                    url: '/Client/Project.aspx/DeleteMyEarthContainer',
                    contentType: 'application/json; charset=utf-8',
                    dataType: 'json',
                    data: JSON.stringify({ kind: _pendingDeleteAction.kind, id: _pendingDeleteAction.id }),
                    success: function() {
                        $('#modalDeleteMyEarthItem').modal('hide');
                        toastr.success('Deleted successfully.');
                        window.location.reload();
                    }
                });
            } else if (_pendingDeleteAction.type === 'asset') {
                $.ajax({
                    type: 'POST',
                    url: '/Client/Project.aspx/DeleteMyEarthAsset',
                    contentType: 'application/json; charset=utf-8',
                    dataType: 'json',
                    data: JSON.stringify({
                        projectId: _pendingDeleteAction.projectId,
                        fileTypeId: _pendingDeleteAction.fileTypeId,
                        externalAssetId: _pendingDeleteAction.externalAssetId
                    }),
                    success: function() {
                        $('#modalDeleteMyEarthItem').modal('hide');
                        toastr.success('Asset deleted.');
                        window.location.reload();
                    }
                });
            }
        }
        function shareContainer(kind, id, title) {
            var labelKind = kind === 'project' ? 'Project' : (kind === 'survey' ? 'Survey' : 'Task');
            document.getElementById('lblShareContainerTitle').textContent = 'Share ' + labelKind + ': ' + (title || '');
            document.getElementById('lblShareKind').textContent = title || labelKind;
            var url = window.location.origin + '/OpenContainerMosaic.ashx?kind=' + encodeURIComponent(kind) + '&id=' + encodeURIComponent(id);
            document.getElementById('txtShareContainerUrl').value = url;
            fetch('/api/desktop/share-origin').then(function(r) { return r.json(); }).then(function(d) {
                if (d && d.shareOrigin) {
                    document.getElementById('txtShareContainerUrl').value = d.shareOrigin + '/OpenContainerMosaic.ashx?kind=' + encodeURIComponent(kind) + '&id=' + encodeURIComponent(id);
                }
            }).catch(function() {});
            $('#modalShareContainer').modal('show');
        }
        function shareSingleAsset(title, viewerPath) {
            var path = viewerPath || '/Map2.aspx';
            document.getElementById('lblShareContainerTitle').textContent = 'Share Asset: ' + (title || '');
            var kindEl = document.getElementById('lblShareKind');
            if (kindEl) kindEl.textContent = title || 'Asset';
            var fullUrl = path.indexOf('http') === 0 ? path : (window.location.origin + path);
            var urlEl = document.getElementById('txtShareContainerUrl');
            if (urlEl) urlEl.value = fullUrl;
            fetch('/api/desktop/share-origin').then(function(r) { return r.json(); }).then(function(d) {
                if (d && d.shareOrigin && urlEl && path.indexOf('http') !== 0) {
                    urlEl.value = d.shareOrigin + path;
                }
            }).catch(function() {});
            $('#modalShareContainer').modal('show');
        }
        function purchaseExtraStorage(extraGb) {
            fetch('/api/v2/storage/upgrade-extra', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({extra_gb: extraGb})
            }).then(function(r) { return r.json(); }).then(function(data) {
                if (data && data.requiresStripe && data.checkoutUrl) {
                    window.location.href = data.checkoutUrl;
                } else if (data && data.ok) {
                    toastr.success(data.message || ('+' + extraGb + ' GiB Extra Storage activated!'));
                    setTimeout(function() { window.location.reload(); }, 500);
                } else {
                    toastr.error((data && data.message) || 'Could not start Extra Storage checkout.');
                }
            });
        }
        var terrainPollSerial = 0;
        function terrainJobKey(pid, aid) { return 'mosaic-terrain-job-' + pid + '-' + aid; }
        function setTerrainBusy(busy) {
            document.getElementById('btnGenerateTerrainDerivative').disabled = busy;
            document.getElementById('btnGenerateTerrainDerivative').textContent = busy ? 'GENERATING...' : 'GENERATE LAYER';
            document.getElementById('selTerrainDerivativeKind').disabled = busy;
            document.getElementById('inpContourIntervalM').disabled = busy;
        }
        function watchTerrainJob(pid, aid, jid) {
            var serial = ++terrainPollSerial;
            var status = document.getElementById('terrainDerivativeStatus');
            setTerrainBusy(true);
            function poll() {
                if (serial !== terrainPollSerial) return;
                var controller = new AbortController();
                var timer = setTimeout(function() { controller.abort(); }, 12000);
                fetch('/api/v2/jobs/status?job_id=' + jid, {signal:controller.signal}).then(function(r) {
                    if (!r.ok) throw new Error('Status unavailable');
                    return r.json();
                }).then(function(data) {
                    if (serial !== terrainPollSerial) return;
                    var job = data.job || {};
                    status.textContent = 'Job #' + jid + ' · ' + (job.PROGRESS || 0) + '% · ' + (job.STAGE || job.STATUS);
                    if (job.STATUS === 'COMPLETED' || job.STATUS === 'FAILED') {
                        sessionStorage.removeItem(terrainJobKey(pid, aid));
                        setTerrainBusy(false);
                        if (job.STATUS === 'FAILED') {
                            status.textContent = job.ERROR || 'Terrain generation failed. Your original DEM is preserved.';
                            toastr.error(status.textContent);
                        } else {
                            var link = document.createElement('a');
                            link.href = job.RESULT_URL; link.textContent = 'Open terrain result';
                            link.style.cssText = 'display:block;margin-top:10px;color:#b6e153;font-weight:700;';
                            status.appendChild(link);
                            toastr.success('Terrain layer ready. Your original DEM is preserved.');
                        }
                    } else setTimeout(poll, 2000);
                }).catch(function() {
                    if (serial !== terrainPollSerial) return;
                    status.textContent = 'Connection interrupted. Job #' + jid + ' continues in the background; reconnecting... You may close this window.';
                    setTimeout(poll, 5000);
                }).finally(function() { clearTimeout(timer); });
            }
            poll();
        }
        function openTerrainDerivativeModal(projectId, assetId, assetName) {
            terrainPollSerial++;
            document.getElementById('hdnDerivProjectId').value = String(projectId || 0);
            document.getElementById('hdnDerivAssetId').value = String(assetId || 0);
            document.getElementById('lblTerrainDerivativeSource').textContent = assetName || ('Asset #' + assetId);
            document.getElementById('terrainDerivativeStatus').textContent = 'Generation runs in the background. A notification will link to the finished layer.';
            setTerrainBusy(false);
            $('#modalTerrainDerivative').modal('show');
            var jid = parseInt(sessionStorage.getItem(terrainJobKey(projectId, assetId)) || '0', 10);
            if (jid) watchTerrainJob(projectId, assetId, jid);
        }
        function submitTerrainDerivative() {
            var pid = parseInt(document.getElementById('hdnDerivProjectId').value || '0', 10);
            var aid = parseInt(document.getElementById('hdnDerivAssetId').value || '0', 10);
            var kind = document.getElementById('selTerrainDerivativeKind').value || 'hillshade';
            var intervalM = parseFloat(document.getElementById('inpContourIntervalM').value);
            if (!isFinite(intervalM) || intervalM < 0.1 || intervalM > 100) { toastr.error('Use a contour interval from 0.1 to 100 meters.'); return; }
            setTerrainBusy(true);
            fetch('/api/v2/terrain/derivative', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({project_id: pid, source_asset_id: aid, derivative_kind: kind, contour_interval_m: intervalM})
            }).then(function(r) { return r.json(); }).then(function(data) {
                if (data && data.ok && data.job_id) {
                    sessionStorage.setItem(terrainJobKey(pid, aid), String(data.job_id));
                    watchTerrainJob(pid, aid, data.job_id);
                } else {
                    setTerrainBusy(false);
                    toastr.error((data && data.message) || 'Could not generate terrain derivative.');
                }
            }).catch(function() {
                setTerrainBusy(false);
                toastr.error('Error communicating with server.');
            });
        }
        function activateLocalNodeOdmInMyEarth() {
            var el = document.getElementById('lblMyEarthNodeOdmStatus');
            if (el) el.textContent = 'Starting local NodeODX…';
            fetch('/api/desktop/start-nodeodm', { method: 'POST' }).then(function(r) { return r.json(); }).then(function(d) {
                if (d && d.online) {
                    toastr.success(d.message || 'Local NodeODX is ready.');
                } else {
                    toastr.warning((d && d.message) || 'Could not start local NodeODX.');
                }
                checkMyEarthNodeOdmStatus();
            });
        }
        function checkMyEarthNodeOdmStatus() {
            var el = document.getElementById('lblMyEarthNodeOdmStatus');
            if (!el) return;
            fetch('/api/desktop/nodeodm-status').then(function(r) { return r.json(); }).then(function(d) {
                if (d && d.online) {
                    el.textContent = 'ONLINE · ODX ' + (d.engineVersion || '') + ' · Local processing';
                    el.style.color = '#99CC00';
                } else {
                    el.textContent = d.message || 'Not running · Verify Local Engine';
                    el.style.color = '#fcd34d';
                }
            }).catch(function() {});
        }
        function forceUpdateDesktopApp() {
            var btn = document.getElementById('btnForceDesktopUpdate');
            if (btn) btn.disabled = true;
            fetch('/api/desktop/force-update', {method:'POST'}).then(function(r) { return r.json(); }).then(function(d) {
                if (d.ok) toastr.info(d.message); else toastr.error(d.message || 'Could not check for updates.');
            }).catch(function() { toastr.error('Could not check for updates.'); }).finally(function() {
                if (btn) btn.disabled = false;
            });
        }
        (function initMyEarthLiveProcessingMonitor() {
            var nodes = document.querySelectorAll('[data-processing-task]');
            if (!nodes || !nodes.length) return;
            setInterval(function() {
                nodes.forEach(function(chip) {
                    var tid = parseInt(chip.getAttribute('data-processing-task') || '0', 10);
                    if (!tid) return;
                    fetch('/api/v2/tasks/' + tid + '/live-log').then(function(r) { return r.json(); }).then(function(d) {
                        if (!d || !d.ok) return;
                        if (d.state_id === 4) {
                            window.location.reload();
                            return;
                        }
                        var pct = Math.max(5, parseInt(d.progress || 5, 10));
                        var pEl = document.getElementById('lblTaskProgPct_' + tid);
                        if (pEl) pEl.textContent = pct + '%';
                        var barEl = document.getElementById('barTaskProg_' + tid);
                        if (barEl) barEl.style.width = pct + '%';
                        var stEl = document.getElementById('lblTaskProgStage_' + tid);
                        if (stEl && d.stage) stEl.textContent = d.stage + (d.lines && d.lines.length ? ' — ' + d.lines[d.lines.length - 1] : '');
                    }).catch(function() {});
                });
            }, 3000);
        })();
        function connectDesktopCloudAccount() {
            var email = (document.getElementById('txtDesktopCloudEmail').value || '').trim();
            var password = document.getElementById('txtDesktopCloudPassword').value || '';
            if (!email || !password) { toastr.error('Please enter your MosaicEarth Cloud email and password.'); return; }
            fetch('/api/desktop/cloud-login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: email, password: password })
            }).then(function(r) { return r.json(); }).then(function(d) {
                if (d && d.ok) {
                    $('#modalDesktopCloudLogin').modal('hide');
                    toastr.success('Connected to MosaicEarth Cloud!');
                    window.location.reload();
                } else {
                    toastr.error((d && d.message) || 'Invalid Cloud credentials.');
                }
            });
        }
        function disconnectDesktopCloudAccount() {
            fetch('/api/desktop/cloud-logout', { method: 'POST' }).then(function() {
                toastr.info('Disconnected from MosaicEarth Cloud.');
                window.location.reload();
            });
        }
        function syncLocalProjectToCloud(pid, pname) {
            toastr.info('Syncing Local Project "' + (pname || pid) + '" to MosaicEarth Cloud...');
            fetch('/api/desktop/sync-to-cloud', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ kind: 'project', id: pid })
            }).then(function(r) { return r.json(); }).then(function(d) {
                if (d && d.ok) {
                    toastr.success(d.message || 'Project synced to MosaicEarth Cloud!');
                    loadDesktopCloudProjects();
                } else if (d && d.login_required) {
                    $('#modalDesktopCloudLogin').modal('show');
                    toastr.warning('Please sign in to your MosaicEarth Cloud account first.');
                } else {
                    toastr.error((d && d.message) || 'Could not sync project to Cloud.');
                }
            });
        }
        function loadDesktopCloudProjects() {
            var host = document.getElementById('desktopCloudProjectsHost');
            if (!host) return;
            fetch('/api/desktop/cloud-workspace').then(function(r) { return r.json(); }).then(function(d) {
                if (!d || !d.connected) return;
                var projs = d.projects || [];
                if (!projs.length) {
                    host.innerHTML = '<div style="color:#8fa099;font-size:13px;">No projects in your Cloud workspace yet. Click <strong>Pass to Cloud</strong> on any Local PC project above to upload it.</div>';
                    return;
                }
                var htmlOut = '';
                projs.forEach(function(cp) {
                    var cName = String(cp.NAME || 'Cloud Project');
                    var cId = cp.ID || 0;
                    var sCnt = (cp.Surveys || []).length;
                    var aCnt = cp.TOTAL_LAYERS || 0;
                    htmlOut += '<div style="padding:12px 14px;margin-bottom:8px;background:#101713;border:1px solid #2b3c32;border-radius:8px;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:10px;">';
                    htmlOut += '<div><span class="myearth-level-pill" style="background:rgba(56,189,248,0.16);border:1px solid #38bdf8;color:#38bdf8;">CLOUD PROJECT</span> ';
                    htmlOut += '<strong style="color:#fff;font-size:14.5px;margin-left:6px;">' + cName + '</strong> ';
                    htmlOut += '<span style="font-size:12px;color:#8fa099;margin-left:6px;">(' + sCnt + ' Surveys &middot; ' + aCnt + ' Assets)</span></div>';
                    htmlOut += '<div style="display:flex;gap:8px;flex-wrap:wrap;">';
                    htmlOut += '<a href="' + d.cloudOrigin + '/OpenContainerMosaic.ashx?kind=project&id=' + cId + '" target="_blank" class="myearth-action-btn myearth-action-btn--accent">Open in Cloud 3D Map &rarr;</a>';
                    htmlOut += '<a href="' + d.cloudOrigin + '/en/Projects?open_project=' + cId + '" target="_blank" class="myearth-action-btn">Manage in Cloud</a>';
                    htmlOut += '</div></div>';
                });
                host.innerHTML = htmlOut;
            }).catch(function() {});
        }
        checkMyEarthNodeOdmStatus();
        (function initMyEarthTreeState() {
            var mapObj = _getOpenProjectsMap();
            var params = new URLSearchParams(window.location.search || '');
            var openPid = params.get('open_project');
            if (openPid) {
                mapObj[String(openPid)] = 1;
                _saveOpenProjectsMap(mapObj);
            }
            var viewMode = params.get('view');
            if (viewMode === 'quota' || viewMode === 'acquired') {
                setMyEarthView(viewMode);
            }
            Object.keys(mapObj).forEach(function(pid) {
                if (mapObj[pid]) {
                    var body = document.getElementById('projBody_' + pid);
                    var caret = document.getElementById('projCaret_' + pid);
                    if (body) body.style.display = 'block';
                    if (caret) caret.classList.remove('is-collapsed');
                }
            });
            var hashTarget = (window.location.hash || '').replace(/^#/, '');
            var targetEl = hashTarget ? document.getElementById(hashTarget) : null;
            if (targetEl) {
                var cur = targetEl;
                while (cur && cur !== document.body) {
                    if (cur.id && (cur.id.indexOf('projBody_') === 0 || cur.id.indexOf('surveyBody_') === 0 || cur.id.indexOf('taskBody_') === 0)) {
                        cur.style.display = 'block';
                    }
                    cur = cur.parentElement;
                }
                var ownTaskBody = document.getElementById(hashTarget.replace('taskNode_', 'taskBody_'));
                if (ownTaskBody) ownTaskBody.style.display = 'block';
                targetEl.style.boxShadow = '0 0 0 2px #99CC00';
                setTimeout(function() { targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, 140);
            } else if (openPid) {
                var node = document.getElementById('projectNode_' + openPid);
                if (node) setTimeout(function() { node.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 120);
            }
        })();
    </script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_project_detail_page(user: Dict[str, Any], slug_or_id: str, lang: str = "en") -> Optional[str]:
    norm_lang = normalize_lang(lang)
    store = get_store()
    proj = store.get_project_by_slug_or_id(slug_or_id)
    if not proj:
        return None

    pid = int(proj["ID"])
    uid = int(user["ID"])
    # Check if this is an external or gaussian project
    ext_meta = store.get_external_project_meta(pid)
    gauss_meta = store.get_gaussian_project(pid)
    if gauss_meta or (ext_meta and str(ext_meta.get("PROJECT_KIND") or "").lower() == "gaussian"):
        return render_gaussian_project_page(user, pid, lang=norm_lang)
    if ext_meta and str(ext_meta.get("PROJECT_KIND") or "").lower() == "external":
        return render_external_asset_import_page(user, pid, lang=norm_lang)

    import datetime
    import math
    from .db_store import _parse_iso_dt

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    pname = str(proj.get("NAME") or f"Project #{pid}")
    pdesc = str(proj.get("DESCRIPTION") or "")
    cre_dt = _parse_iso_dt(proj.get("CREATE_DATE")) or now_utc
    pdate = cre_dt.strftime("%d/%m/%Y")
    control_name = str((proj.get("PHOTOGRAMMETRY_CONTROL") or {}).get("name") or "")
    pfolder = str(proj.get("FOLDER") or "")
    state_id = int(proj.get("STATE_ID") or 3)
    zip_uploaded = 1 if (bool(proj.get("ZIP_UPLOADED")) or state_id == 4) else 0
    show_processed = state_id == 4 and zip_uploaded == 1
    result_contract = proj.get("CANONICAL_RESULT_CONTRACT")
    available_types = ({ft for ft, field in ((1, "pointcloud_master"), (2, "orthophoto_master"), (3, "mesh_master"))
                        if result_contract.get(field)} if result_contract is not None else {1, 2, 3})

    prem_pids = {int(pp.get("PROJECT_ID") or 0) for pp in store.get_user_premium_projects(uid)}
    is_premium = pid in prem_pids or bool(proj.get("PREMIUM"))
    credits_val = str(int(float(proj.get("CREDITS") or 0)))
    user_avail_credits = int(float(user.get("CREDITS") or user.get("CreditsInt") or 0))
    cat_id = int(proj.get("CATEGORY_ID") or 0)
    if 1 <= cat_id <= 10:
        cat_id += 12
    tags_val = str(proj.get("TAGS") or "")
    seo_slug = str(proj.get("SEO_URL_FRIENDLY") or pid)

    owner_user = store.get_user_by_id(int(proj.get("CREATE_ID") or proj.get("USER_ID") or uid)) or user
    owner_username = str(owner_user.get("USERNAME") or owner_user.get("NAME") or owner_user.get("EMAIL") or "")

    files_list = store.get_project_files(pid)
    num_files = len(files_list) or int(proj.get("NUM_FILES") or 0)

    vals = store.get_project_values(pid) or {}
    area_val = str(vals.get("AREA") or proj.get("AREA") or "Not calculated")
    cap_dt = _parse_iso_dt(vals.get("CAPTURE_DATE"))
    cap_date_str = cap_dt.strftime("%d/%m/%Y") if cap_dt else pdate

    def _fmt_metric_2dec(raw_val: Any, unit: str) -> str:
        s = str(raw_val or "").strip().replace(",", ".")
        if not s:
            return ""
        m_num = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s)
        if not m_num:
            return s
        try:
            fval = float(m_num.group(0))
            return f"{fval:.2f} {unit}"
        except Exception:
            return s

    gps_err_val = _fmt_metric_2dec(vals.get("GPS_AVERAGE") or vals.get("AVERAGE_GPS_ERROR") or "0.42", "m")
    gsd_val = _fmt_metric_2dec(vals.get("GSD_AVERAGE") or vals.get("AVERAGE_GSD") or "1.85", "cm/px")

    cesium_models = store.get_cesium_models_by_project(pid, 3)
    cesium_model = cesium_models[0] if cesium_models else None
    height_3d = float(cesium_model.get("Height") or 0.0) if cesium_model else 0.0
    pc_models = store.get_cesium_models_by_project(pid, 1)
    height_pc = float(pc_models[0].get("Height") or 0.0) if pc_models else 0.0
    loc_row = store.get_project_location(pid) or {}
    proj_lat = str(proj.get("LATITUD") or loc_row.get("LATITUD") or "41.9794")
    proj_lon = str(proj.get("LONGITUD") or loc_row.get("LONGITUD") or "2.8214")
    asset_titles_map = proj.get("ASSET_TITLES") if isinstance(proj.get("ASSET_TITLES"), dict) else {}
    url_cesium_3d = f"/ProcessedAssetFile.ashx/{pfolder}/procesado/3d_tiles/model/tileset.json" if show_processed else ""
    cesium_key = store.get_cesium_browser_token()

    root_pid, subprojects_family = store.get_subprojects_family(pid)
    root_proj = store.get_project(root_pid) or proj
    root_pname = str(root_proj.get("NAME") or pname)
    subproj_tabs = []
    for idx_sp, sp in enumerate(subprojects_family, start=1):
        sp_id = int(sp.get("ID") or 0)
        sp_name = html.escape(str(sp.get("NAME") or f"Subproject #{idx_sp}"))
        sp_active = sp_id == pid
        sp_ep = store.get_external_project_meta(sp_id)
        sp_gp = store.get_gaussian_project(sp_id)
        if sp_gp or (sp_ep and str(sp_ep.get("PROJECT_KIND") or "").lower() == "gaussian"):
            sp_href = f"/Client/GaussianProject.aspx?id={sp_id}"
            sp_badge = "3D Splat"
        elif sp_ep and str(sp_ep.get("PROJECT_KIND") or "").lower() == "external":
            sp_href = f"/Client/ExternalAssetImport.aspx?project_id={sp_id}"
            sp_badge = "Imported"
        else:
            sp_href = f"/Client/Project.aspx?id={sp_id}"
            sp_badge = "Photogrammetry"
        tab_style = (
            "background:#99CC00;color:#070b09;font-weight:700;border:1px solid #99CC00;"
            if sp_active
            else "background:#141414;color:#e5e7eb;border:1px solid rgba(255,255,255,0.18);"
        )
        subproj_tabs.append(
            f'<a href="{sp_href}" class="btn btn-sm" style="{tab_style}border-radius:999px;padding:6px 14px;text-decoration:none;display:inline-flex;align-items:center;gap:6px;">'
            f'<span>#{idx_sp} {sp_name}</span>'
            f'<small style="opacity:0.8;font-size:10px;text-transform:uppercase;">({sp_badge})</small>'
            f'</a>'
        )

    pubs_by_type: Dict[int, Dict[str, Any]] = {}
    for pub in store.tables.get("dbo.WEB_PUBLICATIONS", []):
        if int(pub.get("PROJECT_ID") or 0) == pid:
            ft = int(pub.get("FILE_TYPE_ID") or 0)
            if ft in (1, 2, 3):
                prev = pubs_by_type.get(ft)
                if not prev or int(pub.get("STATUS_ID") or pub.get("STATE_ID") or 0) == 2:
                    pubs_by_type[ft] = pub

    has_any_published = any(int(p.get("STATUS_ID") or p.get("STATE_ID") or 0) == 2 for p in pubs_by_type.values())

    def _result_state(ft: int, default_label: str) -> Dict[str, Any]:
        pub = pubs_by_type.get(ft)
        pub_id = int(pub.get("ID") or 0) if pub else 0
        pub_state = int(pub.get("STATUS_ID") or pub.get("STATE_ID") or 0) if pub else 0
        is_pub = pub_state == 2
        credits_p = int(round(float(pub.get("CREDITS") or 0))) if is_pub else 0
        badge_cls = "result-publication-badge is-published" if is_pub else "result-publication-badge is-private"
        badge_txt = ("Published · Free (0 € · 0 GiB Quota)" if credits_p <= 0 else f"Published · Marketplace ({credits_p} € · 0 GiB Quota)") if is_pub else "Private (Counts Toward Storage Quota)"
        detail_href = (
            f"/{norm_lang}/ProductDetail?id={pub_id}"
            if is_pub and pub_id > 0
            else f"/{norm_lang}/ProductDetail?project_id={pid}&type={ft}"
        )
        vis_mode = ("marketplace" if credits_p > 0 else "public_free") if is_pub else "private"
        custom_title = str(asset_titles_map.get(str(ft)) or f"{pname} · {default_label}")
        return {
            "is_pub": is_pub,
            "pub_id": pub_id,
            "credits_p": credits_p,
            "vis_mode": vis_mode,
            "custom_title": custom_title,
            "badge_cls": badge_cls,
            "badge_txt": badge_txt,
            "detail_href": detail_href,
        }

    ortho_st = _result_state(2, "Orthophoto")
    model_st = _result_state(3, "3D Model")
    cloud_st = _result_state(1, "Point Cloud")

    def _build_inline_asset_editor(ft: int, st_info: Dict[str, Any], cur_h: float) -> str:
        v_mode = st_info["vis_mode"]
        p_eur = st_info["credits_p"]
        c_title = html.escape(st_info["custom_title"], quote=True)
        h_input_html = (
            f"""<div class="col-4">
                    <label style="font-size:10px;color:#9ca3af;display:block;">Height Offset (m)</label>
                    <input type="number" step="0.5" id="assetHeight_{ft}" class="form-control form-control-sm" style="background:#111;color:#fff;border-color:#333;font-size:12px;" value="{cur_h:.1f}" />
                </div>"""
            if ft in (1, 3)
            else f'<input type="hidden" id="assetHeight_{ft}" value="0" />'
        )
        col_coord = "col-4" if ft in (1, 3) else "col-6"
        return f"""
        <div style="margin-top:12px;padding:12px;background:#111111;border:1px solid rgba(153,204,0,0.28);border-radius:10px;text-align:left;">
            <div style="font-size:11px;font-weight:700;color:#99CC00;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;">
                <i class="fas fa-sliders-h me-1"></i> Practical Asset Editor (Name · 3D Location · Visibility)
            </div>
            <div class="mb-2">
                <label style="font-size:10px;color:#9ca3af;display:block;">Asset Name</label>
                <input type="text" id="assetTitle_{ft}" class="form-control form-control-sm" style="background:#181818;color:#fff;border-color:#333;font-size:12.5px;font-weight:600;" value="{c_title}" />
            </div>
            <div class="row g-1 mb-2">
                <div class="{col_coord}">
                    <label style="font-size:10px;color:#9ca3af;display:block;">Latitude (WGS84)</label>
                    <input type="text" id="assetLat_{ft}" class="form-control form-control-sm" style="background:#181818;color:#fff;border-color:#333;font-size:12px;" value="{html.escape(proj_lat, quote=True)}" />
                </div>
                <div class="{col_coord}">
                    <label style="font-size:10px;color:#9ca3af;display:block;">Longitude (WGS84)</label>
                    <input type="text" id="assetLon_{ft}" class="form-control form-control-sm" style="background:#181818;color:#fff;border-color:#333;font-size:12px;" value="{html.escape(proj_lon, quote=True)}" />
                </div>
                {h_input_html}
            </div>
            <div class="row g-1 align-items-end mb-2">
                <div class="col-8">
                    <label style="font-size:10px;color:#9ca3af;display:block;">Publication &amp; Storage Quota Mode</label>
                    <select id="assetVis_{ft}" onchange="toggleAssetPriceInput({ft})" class="form-control form-control-sm" style="background:#181818;color:#fff;border-color:#99CC00;font-size:12px;">
                        <option value="private" {'selected' if v_mode == 'private' else ''}>Private (Owner Only · Uses 5 GiB Quota)</option>
                        <option value="public_free" {'selected' if v_mode == 'public_free' else ''}>Public Free · 0 € (0 GiB Quota · Global Map)</option>
                        <option value="marketplace" {'selected' if v_mode == 'marketplace' else ''}>Marketplace · Paid € (0 GiB Quota + 80% Payout)</option>
                    </select>
                </div>
                <div class="col-4" id="assetPriceWrap_{ft}" style="{'display:block;' if v_mode == 'marketplace' else 'display:none;'}">
                    <label style="font-size:10px;color:#99CC00;display:block;">Price (€)</label>
                    <input type="number" min="1" step="1" id="assetPrice_{ft}" class="form-control form-control-sm" style="background:#181818;color:#99CC00;border-color:#99CC00;font-weight:700;font-size:12px;" value="{p_eur if p_eur > 0 else 25}" />
                </div>
            </div>
            <div class="d-flex justify-content-between align-items-center gap-2 mt-2">
                <button type="button" onclick="saveAssetQuick({ft})" class="btn btn-sm" style="background:#99CC00;color:#070b09;font-weight:700;font-size:11.5px;padding:5px 12px;">
                    SAVE ASSET CHANGES
                </button>
                <a href="/Fly.aspx?asset_id=processed-{ft}-{pid}" class="btn btn-sm" style="background:rgba(153,204,0,0.14);border:1px solid #99CC00;color:#99CC00;font-weight:700;font-size:11px;padding:5px 10px;">
                    <i class="fas fa-plane-departure me-1"></i>FLY 1ST PERSON
                </a>
            </div>
            <small id="assetSaveMsg_{ft}" style="display:block;font-size:11px;margin-top:4px;color:#99CC00;"></small>
        </div>"""

    ortho_editor_html = _build_inline_asset_editor(2, ortho_st, 0.0)
    model_editor_html = _build_inline_asset_editor(3, model_st, height_3d)
    cloud_editor_html = _build_inline_asset_editor(1, cloud_st, height_pc)

    ortho_thumb = f"/ProcessedAssetFile.ashx/{pfolder}/procesado/odm_orthophoto/odm_orthophoto.webp"
    ortho_dl = f"/ProcessedAssetFile.ashx/{pfolder}/procesado/odm_orthophoto/odm_orthophoto.tif"
    model_dl = f"/ProcessedAssetFile.ashx/{pfolder}/3d-model.zip"
    cloud_dl = f"/ProcessedAssetFile.ashx/{pfolder}/pointcloud.zip"
    report_dl = f"/ProcessedAssetFile.ashx/{pfolder}/procesado/odm_report/report.pdf"

    cat_options = ['<option value="-1">Select a category</option>']
    for cid, cinfo in sorted(DEFAULT_CATEGORIES.items()):
        sel = ' selected="selected"' if cid == cat_id else ""
        cat_options.append(f'<option value="{cid}"{sel}>{html.escape(cinfo["name"])}</option>')

    eligible = store.get_eligible_premium_projects(uid)
    elig_opts = ['<option value="-1">Select a project</option>']
    for ep in eligible:
        sel = ' selected="selected"' if int(ep["ID"]) == pid else ""
        elig_opts.append(f'<option value="{ep["ID"]}"{sel}>{html.escape(str(ep.get("NAME") or ""))}</option>')

    file_rows = []
    for f in files_list:
        fid = int(f.get("PROJECT_FILE_ID") or f.get("ID") or 0)
        fname = html.escape(str(f.get("FILENAME") or f.get("FILE_NAME") or f"image_{fid}.jpg"))
        fsize_mb = float(f.get("SIZE") or 0) / (1024.0 * 1024.0)
        fres = html.escape(str(f.get("RESOLUTION") or "—"))
        fdate = html.escape(str(f.get("CAPTURE_DATE") or "")[:10])
        flat = html.escape(str(f.get("LATITUD") or "—"))
        flon = html.escape(str(f.get("LONGITUD") or "—"))
        falt = html.escape(str(f.get("ALTURA") or "—"))
        file_rows.append(
            f"""<div class="row row-table" id="file_{fid}">
                <div class="col-3">{fname}</div>
                <div class="col-1 text-center">{fsize_mb:.2f} MB</div>
                <div class="col-2 text-center">{fres}</div>
                <div class="col-2 text-center">{fdate}</div>
                <div class="col-3 text-center">{flat}, {flon} ({falt})</div>
                <div class="col-1 text-end">
                    <img class="deleteIcon" src="/Resources/papelera.svg" onclick="changeDeleteId({fid})" data-bs-toggle="modal" data-bs-target="#staticBackdrop1" alt="Delete" style="cursor:pointer;" />
                </div>
            </div>"""
        )

    is_processing_now = state_id in (1, 2) and not show_processed
    proc_panel_cls = "processing-stage-panel background-process-panel" + ("" if is_processing_now else " processing-stage-panel--hidden")
    task_error_html = ""
    if state_id == 3 and proj.get("LAST_ERROR"):
        payment_status = str(proj.get("PAYMENT_STATUS") or "No payment recorded").replace("_", " ").lower()
        payment_message = f"Payment: {payment_status}."
        if float(proj.get("UPFRONT_AMOUNT_EUR") or 0) <= 0:
            payment_message = "No additional charge."
            if proj.get("PAYMENT_STATUS") == "REFUNDED_ON_FAILURE" and (proj.get("COVERED_PLAN_IMAGES") or proj.get("COVERED_PLAN_SPLATS")):
                payment_message += " Included quota restored."
        task_error_html = (f'<div role="alert" data-processing-failure style="margin:12px 0;padding:16px;background:#321d1d;color:#ffe1dc;border:1px solid #b9695a;border-radius:8px;">'
                           f'<strong>Processing failed · Task #{pid}</strong><p>{html.escape(str(proj["LAST_ERROR"]))}</p>'
                           f'<p>Your source files are preserved. {html.escape(payment_message)} Select PROCESS to retry.</p></div>')
    initial_progress = max(0, min(100, int(proj.get("PROCESSING_PROGRESS") or 0)))

    if os.environ.get('MOSAICEARTH_DESKTOP_SHARED_UI') == '1':
        ret_eyebrow = "LOCAL STORAGE"
        ret_title = "Originals and results are stored on this computer"
        ret_exp = "Cloud synchronization creates a separate private copy. Publish and share that cloud copy when you want others to access it online."
        upgrade_btn_style = "display:none;"
    elif has_any_published:
        ret_eyebrow = "GLOBAL MAP & STORAGE QUOTA"
        ret_title = "Published Assets consume 0 GiB of your Private Storage Quota · Permanent Hosting"
        ret_exp = "Assets published on the Global Map (Free 0 € or Marketplace €) do not count against your 5 GiB Free Private Quota, act as your global 3D portfolio, and earn 80% on Marketplace sales."
        upgrade_btn_style = "display:none;"
    else:
        ret_eyebrow = "PERMANENT PRIVATE STORAGE (5 GiB FREE QUOTA · NO 15-DAY EXPIRATION)"
        ret_title = "Stored permanently in your 5 GiB Free Private Quota (or 250 GiB Pro / 1,000 GiB Studio)"
        ret_exp = "Why publish an Asset to the Global Map? 1) Published assets consume 0 GiB of your 5 GiB private quota, 2) You can sell licenses in Euros (€) on the Marketplace (80% creator payout), and 3) Your brand appears on the Global 3D Map."
        upgrade_btn_style = "display:none;"

    # Additional results (DSM, DTM, PointCloud2D, etc.)
    additional_assets = [
        a for a in store.get_external_assets_by_project(pid)
        if str(a.get("SOURCE_ORIGIN") or "").lower() == "processed"
    ]
    additional_html = ""
    if additional_assets:
        add_cards = []
        for a in additional_assets:
            aid = int(a.get("ID") or 0)
            atitle = html.escape(str(a.get("TITLE") or a.get("DISPLAY_NAME") or f"Result #{aid}"))
            atype = str(a.get("ASSET_TYPE") or "orthophoto").lower()
            tid = 1 if atype == "pointcloud" else 3 if atype == "model3d" else 2
            kicker = f"{store.family_display(tid)} · {store.subtype_display(atype, a.get('ASSET_SUBTYPE'))}"
            vis = str(a.get("VISIBILITY") or "private").lower()
            price_c = int(a.get("PRICE_CREDITS") or 0)
            p_disp = "Private" if vis != "public" else ("FREE (0 €)" if price_c <= 0 else f"{price_c} €")
            fmt = html.escape(str(a.get("SOURCE_FORMAT") or a.get("FORMAT") or "TIF").upper())
            st = html.escape(str(a.get("STATUS") or "ready"))
            st_det = html.escape(str(a.get("STATUS_DETAIL") or "Ready for inspection."))
            add_cards.append(
                f"""<article class="additional-result-card">
                    <div class="additional-result-head">
                        <span>{html.escape(kicker)}</span>
                        <strong class="additional-result-access is-{html.escape(vis)}">{html.escape(p_disp)}</strong>
                    </div>
                    <h3>{atitle}</h3>
                    <p>{st_det}</p>
                    <dl>
                        <div><dt>Format</dt><dd>{fmt}</dd></div>
                        <div><dt>Status</dt><dd>{st}</dd></div>
                    </dl>
                    <div class="additional-result-actions">
                        <a class="btn" href="/Client/ExternalAssetDetail.aspx?project_id={pid}&amp;asset_id={aid}">MOSAICASSET DETAILS</a>
                        <a class="btn btn-secondary-action" href="/ExternalAssetFile.ashx?project_id={pid}&amp;asset_id={aid}&amp;download=1">DOWNLOAD</a>
                        {f'<a class="btn btn-secondary-action" href="/Client/ExternalAssetImport.aspx?project_id={pid}&amp;asset_id={aid}#publishingPanel">PUBLISH</a>' if vis != "public" else ''}
                    </div>
                </article>"""
            )
        additional_html = f"""
        <section class="container additional-processing-results" aria-label="Additional photogrammetry results">
            <div class="additional-results-header">
                <div>
                    <span class="result-family-kicker">ADDITIONAL PHOTOGRAMMETRY RESULTS</span>
                    <h2>Elevation, vector, mesh and point-cloud deliverables</h2>
                    <p>Generated during the same processing job and stored as individual MosaicAssets under this project.</p>
                </div>
            </div>
            <div class="additional-results-grid">
                {''.join(add_cards)}
            </div>
        </section>"""

    seo_head = build_head_metadata_html(
        title=f"{pname} · Project Workspace · MosaicEarth",
        description=pdesc or f"Photogrammetry project {pname} on MosaicEarth.",
        canonical_path=f"/{norm_lang}/Projects/{seo_slug}",
        robots="noindex, nofollow",
    )

    head_extra = """
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <link href="https://cesium.com/downloads/cesiumjs/releases/1.144/Build/Cesium/Widgets/widgets.css" rel="stylesheet" />
    <script src="https://cesium.com/downloads/cesiumjs/releases/1.144/Build/Cesium/Cesium.js"></script>
    <script src="/js/front/MosaicCesiumRuntime.js?v=__MOSAIC_MAP_RUNTIME_VERSION__"></script>
    <script src="/js/front/MosaicAnnotations.js?v=20261016-r45"></script>
    <link rel="stylesheet" href="/css/MosaicAnnotations.css?v=20261016-r45" />
    <link rel="stylesheet" href="/assets/css/Project.css?v=20261016-results-r42" />
    <link rel="stylesheet" href="/assets/css/source-workflow.css?v=20261016-r45" />
    <link href="/Admin/assets/css/plugins/select2/select2.min.css" rel="stylesheet" />
    <link rel="stylesheet" href="/assets/css/plugins/bootstrap-tagsinput/bootstrap-tagsinput.css" />"""

    ortho_icon_src = "/Resources/public.svg" if ortho_st["is_pub"] else "/Resources/private.svg"
    model_icon_src = "/Resources/public.svg" if model_st["is_pub"] else "/Resources/private.svg"
    cloud_icon_src = "/Resources/public.svg" if cloud_st["is_pub"] else "/Resources/private.svg"
    ortho_pill_cls = "result-publication-pill is-public" if ortho_st["is_pub"] else "result-publication-pill is-private"
    model_pill_cls = "result-publication-pill is-public" if model_st["is_pub"] else "result-publication-pill is-private"
    cloud_pill_cls = "result-publication-pill is-public" if cloud_st["is_pub"] else "result-publication-pill is-private"

    total_files_mb = round(sum(float(f.get("SIZE") or 0) for f in files_list) / (1024.0 * 1024.0), 2)
    upfront_quote = store.calculate_task_processing_quote(
        uid,
        num_images=max(5, num_files),
        task_kind="photogrammetry",
        total_megabytes=total_files_mb,
        target_mode="cloud",
    )
    upfront_eur_val = float(upfront_quote.get("retail_price_eur") or 0.0)
    upfront_summary_lbl = html.escape(str(upfront_quote.get("summary_label") or f"{upfront_eur_val:.2f} €"))
    upfront_engine_lbl = html.escape(str(upfront_quote.get("engine") or "opendronemap/nodeodm:latest (Official OpenDroneMap Engine)"))
    is_desktop_local_mode = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
    desktop_cloud_acc = store.get_desktop_cloud_account() if is_desktop_local_mode else {"logged_in": True}
    desktop_cloud_logged_in = bool(desktop_cloud_acc.get("logged_in"))

    exif_gps_points = []
    for _pf in files_list:
        try:
            _pla = float(_pf.get("LATITUD") or 0.0)
            _plo = float(_pf.get("LONGITUD") or 0.0)
            if (abs(_pla) > 1e-5 or abs(_plo) > 1e-5) and not (
                abs(_pla - 41.8205) < 1e-4 and abs(_plo - 1.8676) < 1e-4 and not _pf.get("HAS_EXIF_GPS")
            ):
                exif_gps_points.append([round(_pla, 6), round(_plo, 6), str(_pf.get("FILE_NAME") or _pf.get("FILENAME") or "")])
        except Exception:
            pass
    gcp_obs_photo = proj.get("GCP_OBSERVATIONS") if isinstance(proj.get("GCP_OBSERVATIONS"), dict) else {}
    gcp_photo_pts = [p for p in (gcp_obs_photo.get("points") or []) if isinstance(p, dict) and p.get("role", "gcp") != "check"]
    has_photo_gcp = bool(control_name or len(gcp_photo_pts) >= 3)
    has_photo_georef = len(exif_gps_points) >= 3 or has_photo_gcp
    _raw_p_lat = proj.get("LATITUD")
    _raw_p_lon = proj.get("LONGITUD")
    has_user_approx_pin = False
    approx_lat_str = ""
    approx_lon_str = ""
    try:
        _plf = float(_raw_p_lat) if _raw_p_lat not in (None, "") else 0.0
        _plnf = float(_raw_p_lon) if _raw_p_lon not in (None, "") else 0.0
        if (abs(_plf) > 1e-5 or abs(_plnf) > 1e-5) and not (
            (abs(_plf - 41.8205) < 1e-4 and abs(_plnf - 1.8676) < 1e-4)
            or (abs(_plf - 41.9794) < 1e-4 and abs(_plnf - 2.8214) < 1e-4)
        ):
            has_user_approx_pin = True
            approx_lat_str = f"{_plf:.6f}"
            approx_lon_str = f"{_plnf:.6f}"
    except Exception:
        pass
    if exif_gps_points and not approx_lat_str:
        approx_lat_str = f"{sum(p[0] for p in exif_gps_points) / len(exif_gps_points):.6f}"
        approx_lon_str = f"{sum(p[1] for p in exif_gps_points) / len(exif_gps_points):.6f}"
    exif_gps_json = json.dumps(exif_gps_points[:600])

    body_content = f"""
    <section id="projects" class="mosaic-workflow">
        <div class="container">
            <div class="row">
                <div class="col-12">
                    <div id="divTimeDownload" class="timeDownloadDiv project-retention-unified" style="{'display:flex;' if show_processed else 'display:none;'}">
                        <div>
                            <small id="storageStatusLabel">{html.escape(ret_eyebrow)}</small>
                            <strong><span id="timeLeft">{html.escape(ret_title)}</span></strong>
                            <span id="storageStatusDescription">{html.escape(ret_exp)}</span>
                        </div>
                        <button type="button" class="btn btn-primary" data-bs-toggle="modal" data-bs-target="#modalNewPremiumProject" id="timerUpgradeProject" style="{upgrade_btn_style}">KEEP PRIVATE WITH PREMIUM</button>
                    </div>
                </div>
            </div>
        </div>

        <div class="container">
            <div class="row" style="margin-bottom: 12px;">
                <div class="col-12">
                    <div class="box-4d" style="padding:14px 20px;background:#111111;border:1px solid rgba(153,204,0,0.35);border-radius:10px;">
                        <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
                            <div>
                                <span style="font-size:10.5px;font-weight:700;color:#99CC00;text-transform:uppercase;letter-spacing:0.06em;">MYEARTH TASK WORKSPACE (PROJECT &rarr; SURVEY &rarr; TASK &rarr; ASSETS)</span>
                                <div style="font-size:16px;font-weight:800;color:#fff;">Project: {html.escape(root_pname)} <span style="font-size:12px;color:#9ca3af;font-weight:400;">&middot; Task: {html.escape(pname)}</span></div>
                            </div>
                            <div class="d-flex flex-wrap align-items-center gap-2">
                                <a href="/{norm_lang}/Projects?open_project={root_pid}" class="btn btn-primary btn-sm" style="font-weight:700;text-decoration:none;">
                                    &larr; BACK TO MYEARTH TREE
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="row" style="margin-bottom: 15px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="row">
                                <div class="col-6 text-start">
                                    <a class="subpage-nav" href="/{norm_lang}/Projects?open_project={root_pid}">MyEarth</a> / <span style="color:#9ca3af;font-size:14px;">{html.escape(root_pname)}</span>
                                    <span class="subpage-current"><label id="nameProject">{html.escape(pname)}</label></span>
                                </div>
                                <div class="col-6 text-end">
                                    <button type="button" id="botonAdd" class="btn btn-secondary" style="{'display:none;' if (show_processed or is_processing_now) else 'display:inline-block;'}" data-bs-toggle="modal" data-bs-target="#staticBackdrop2">+ ADD PHOTOS</button>
                                    <button type="button" id="btnOpenVideoStageModal" class="btn btn-secondary" style="{'display:none;' if (show_processed or is_processing_now) else 'display:inline-block;'}border-color:#99CC00;color:#d9f99d;" data-bs-toggle="modal" data-bs-target="#modalVideoPhotogrammetry">&#127916; + VIDEO FRAMES</button>
                                    <button type="button" id="btnOpenPhotoGcpStudio" class="btn btn-secondary" style="{'display:none;' if (show_processed or is_processing_now) else 'display:inline-block;'}border-color:#38bdf8;color:#bae6fd;" onclick="openPhotogrammetryGcpStudio({pid})">&#8982; GCP STUDIO</button>
                                    <button type="button" class="btn btn-primary" id="botonProcess" style="{'display:none;' if (show_processed or is_processing_now or num_files < 1) else 'display:inline-block;'}" data-bs-toggle="modal" data-bs-target="#staticBackdrop3">PROCESS ({upfront_eur_val:.2f} &euro;)</button>
                                    <div style="margin-bottom: 5px; display: {'inline-block' if show_processed else 'none'};">
                                        <a href="{report_dl}" target="_blank" class="btn-secondary" id="enlaceReport">DOWNLOAD REPORT</a>
                                    </div>
                                    <div style="margin-bottom: 5px; display: {'inline-block' if show_processed else 'none'};">
                                        <button type="button" class="btn-primary" id="btnSaveChanges" onclick="saveChangesProject()">SAVE CHANGES</button>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {task_error_html}
                        <div id="processingStagePanel" class="{proc_panel_cls}" role="status" aria-live="polite">
                            <span class="processing-stage-orbit background-process-orbit" aria-hidden="true"><i></i></span>
                            <div class="processing-stage-copy background-process-copy" style="flex:1;min-width:280px;">
                                <small id="processingStageEyebrow">{'LOCAL PHOTOGRAMMETRY' if is_desktop_local_mode else ('ODX (NODEODX) PHOTOGRAMMETRY' if os.environ.get('MOSAICEARTH_RUNPOD_PHOTOGRAMMETRY') == 'nodeodx' else 'OPENDRONEMAP (NODEODM) PHOTOGRAMMETRY')}</small>
                                <h2 id="processingStageTitle">Processing your images</h2>
                                <p id="processingStageMessage">Your source images are preserved. Follow the current stage below or return to MyEarth at any time. Results appear after the products required by your preset have been verified.</p>
                                <div class="source-transfer-meter">
                                    <div class="source-transfer-meter-head"><strong id="processingStagePercent">{initial_progress}%</strong><span id="processingStageStep">{html.escape(str(proj.get('PROCESSING_STAGE') or 'Preparing processing'))}</span></div>
                                    <div class="progress processing-stage-progress"><div id="processingBar" class="progress-bar progress-bar-striped bg-success progress-bar-animated" role="progressbar" style="width: {initial_progress}%; background-color: #99CC00 !important;"></div></div>
                                </div>
                                <strong id="statusProcessing" class="background-process-status">PROCESSING IMAGES...</strong>
                                <div id="odmLiveConsoleBox" style="margin-top:12px;background:#090d0b;border:1px solid #2b3a31;border-radius:8px;padding:10px 12px;font-family:Consolas,monospace;font-size:11.5px;color:#b5d979;">
                                    <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1e2a23;padding-bottom:6px;margin-bottom:6px;color:#99CC00;font-weight:700;">
                                        <span><i class="fas fa-terminal me-1"></i> LIVE PHOTOGRAMMETRY CONSOLE</span>
                                        <span id="odmLiveElapsed" style="font-size:11px;color:#8fa099;">Active &middot; Real-time telemetry</span>
                                    </div>
                                    <pre id="odmLiveConsolePre" style="margin:0;max-height:150px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;color:#d1fae5;font-size:11px;line-height:1.45;">Connecting to the reconstruction log...</pre>
                                </div>
                            </div>
                            <div class="processing-stage-actions background-process-actions">
                                <a href="/{norm_lang}/Projects?open_project={root_pid}" class="btn btn-primary">BACK TO MYEARTH</a>
                                <button id="btnCancel" type="button" class="btn-cancel" style="{'display:inline-block;' if is_processing_now else 'display:none;'}" onclick="btnCancelTask();">CANCEL PROCESSING</button>
                            </div>
                        </div>

                        <div id="photogrammetryUnifiedHub" style="{'display:none;' if (show_processed or is_processing_now) else 'display:block;'}padding:16px 20px;border-bottom:1px solid #243029;background:#0e1511;">
                            <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px;padding:11px 14px;background:rgba(153,204,0,0.08);border:1px solid rgba(153,204,0,0.34);border-radius:10px;">
                                <div>
                                    <div style="font-size:11px;font-weight:800;color:#99CC00;text-transform:uppercase;letter-spacing:0.05em;">
                                        <i class="fas fa-sparkles me-1"></i> Maximum Quality Always Active &middot; Exact Upfront Per-Photo Quote
                                    </div>
                                    <div style="font-size:12.5px;color:#dce8e0;margin-top:2px;">
                                        Upload overlapping photos (<code>.JPG / .PNG / .WEBP</code>) or extract sharp keyframes from video (<code>.MP4 / .MOV / .WEBM</code> + optional <code>.SRT / .GPX</code>). Video frames are extracted first so you know the <strong>exact image count and Euro price</strong> before launching processing.
                                    </div>
                                </div>
                                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                                    <span id="photoGcpHubBadge" style="padding:5px 11px;border-radius:999px;background:{'rgba(56,189,248,0.16)' if has_photo_gcp else '#141d17'};border:1px solid {'#38bdf8' if has_photo_gcp else '#34463b'};color:{'#bae6fd' if has_photo_gcp else '#9eb2a5'};font-size:11.5px;font-weight:700;">
                                        {f'✓ GCP Control Active ({html.escape(control_name or (str(len(gcp_photo_pts)) + " GCPs"))})' if has_photo_gcp else 'No GCPs attached'}
                                    </span>
                                    <div style="padding:6px 12px;border-radius:8px;background:#121914;border:1px solid #99CC00;color:#99CC00;font-weight:800;font-size:13px;">
                                        {num_files} frames &middot; {'0.00 € (Local PC)' if is_desktop_local_mode else f'{upfront_eur_val:.2f} € Cloud Quote'}
                                    </div>
                                </div>
                            </div>

                            <div style="background:#111814;border:1px solid #2b3c32;border-radius:10px;padding:12px 14px;">
                                <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px;">
                                    <div>
                                        <span style="font-size:12px;font-weight:700;color:#fff;"><i class="fas fa-map-marked-alt me-1" style="color:#99CC00;"></i> Dataset Georeferencing &amp; Map Location</span>
                                        <span id="photoGeorefStatusBadge" style="margin-left:8px;font-size:11px;padding:3px 9px;border-radius:999px;font-weight:700;background:{'rgba(153,204,0,0.18)' if has_photo_georef else ('rgba(245,158,11,0.18)' if num_files > 0 else '#18241d')};border:1px solid {'#99CC00' if has_photo_georef else ('#f59e0b' if num_files > 0 else '#3c5244')};color:{'#d9f99d' if has_photo_georef else ('#fde68a' if num_files > 0 else '#cbd5e1')};">
                                            {f'✓ Georeferenced automatically ({len(exif_gps_points)} EXIF GPS frames)' if len(exif_gps_points) >= 3 else ('✓ Metric GCP Control Attached' if has_photo_gcp else ('📍 Unreferenced (No GPS/GCPs) — Click map for initial spot (3D Mesh + Point Cloud only)' if num_files > 0 else 'Upload photos or video to auto-detect GPS on map'))}
                                        </span>
                                    </div>
                                    <div style="display:flex;gap:6px;align-items:center;min-width:240px;">
                                        <input type="text" id="photoMapSearchInput" class="form-control form-control-sm" style="background:#161c18;border:1px solid #35483c;color:#fff;font-size:12px;" placeholder="Search place on map..." />
                                        <button type="button" onclick="searchPhotogrammetryMapPlace()" class="btn btn-sm btn-secondary" style="font-size:11.5px;">Find</button>
                                    </div>
                                </div>
                                <div id="photoTaskLocationMap" style="width:100%;height:215px;border-radius:8px;border:1px solid #29382f;background:#0a0f0c;margin-bottom:8px;"></div>
                                <div class="row g-2 align-items-center">
                                    <div class="col-md-3 col-6">
                                        <label style="font-size:10.5px;color:#9eb2a5;display:block;">Initial Latitude (WGS84)</label>
                                        <input type="text" id="photoApproxLat" value="{approx_lat_str}" placeholder="Click map or auto-GPS" class="form-control form-control-sm" style="background:#161c18;border:1px solid #334439;color:#fff;font-size:12px;" onchange="savePhotogrammetryManualCoords()" />
                                    </div>
                                    <div class="col-md-3 col-6">
                                        <label style="font-size:10.5px;color:#9eb2a5;display:block;">Initial Longitude (WGS84)</label>
                                        <input type="text" id="photoApproxLon" value="{approx_lon_str}" placeholder="Click map or auto-GPS" class="form-control form-control-sm" style="background:#161c18;border:1px solid #334439;color:#fff;font-size:12px;" onchange="savePhotogrammetryManualCoords()" />
                                    </div>
                                    <div class="col-md-6 col-12">
                                        <small style="color:#b8c6be;font-size:11.5px;display:block;line-height:1.4;">
                                            {'✓ Full metric deliverables enabled: <strong>Orthophoto + DSM + DTM + Dense Point Cloud + 3D Textured Mesh</strong>.' if has_photo_georef else 'Without GPS or GCPs, Photogrammetry reconstructs your <strong>3D Textured Mesh + Dense Point Cloud</strong> anchored at the map pin you click (refine X, Y, Z later in MyEarth). Want metric Orthophoto + DSM + DTM? Mark 3+ points in <strong>GCP Studio</strong>!'}
                                        </small>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div id="cabecera" class="row header-project" style="{'display:none;' if show_processed else 'display:flex;'}">
                            <div class="col-md-3 header-table-project">FILE NAME</div>
                            <div class="col-md-2 header-table-project">SIZE</div>
                            <div class="col-md-2 header-table-project">RESOLUTION</div>
                            <div class="col-md-2 header-table-project">CAPTURE DATE</div>
                            <div class="col-md-2 header-table-project">GEOLOCATION</div>
                            <div class="col-md-1 text-end"></div>
                        </div>

                        <div class="content-box">
                            <div id="nofiles" class="row text-center" style="{'display:none;' if (show_processed or num_files > 0) else 'display:block;'}">
                                <div class="col-12" style="padding: 48px 20px;">
                                    <div class="imagen-anadir">
                                        <img id="imganadir" style="cursor: pointer;" data-bs-toggle="modal" data-bs-target="#staticBackdrop2" src="/Resources/Projects/imagenes-project.svg" />
                                    </div>
                                    <div class="texto-nofiles">
                                        <span class="color-verde-modal">Upload</span> the files for this project
                                    </div>
                                </div>
                            </div>

                            <div id="countfiles" class="row" style="{'display:none;' if (show_processed or num_files == 0) else 'display:flex;'}">
                                <div class="col-12" id="files">{''.join(file_rows)}</div>
                            </div>

                            <div id="processed" class="row" style="{'display:flex;' if show_processed else 'display:none;'}">
                                <div class="col-lg-2 col-md-6 col-sm-12">
                                    <div class="form-group">
                                        <label for="inputCreatedOn">Created on</label>
                                        <input class="form-control input-margin readonly" id="inputCreatedOn" readonly value="{html.escape(pdate, quote=True)}" />
                                    </div>
                                </div>
                                <div class="col-lg-2 col-md-6 col-sm-12">
                                    <div class="form-group">
                                        <label for="inputCaptureDate">Capture date</label>
                                        <input class="form-control input-margin" id="inputCaptureDate" value="{html.escape(cap_date_str, quote=True)}" />
                                    </div>
                                </div>
                                <div class="col-lg-5 col-md-6 col-sm-12">
                                    <div class="form-group">
                                        <label for="inputProjectName">Subproject name (editable)</label>
                                        <input class="form-control input-margin" id="inputProjectName" value="{html.escape(pname, quote=True)}" />
                                    </div>
                                </div>
                                <div class="col-lg-3 col-md-6 col-sm-12">
                                    <div class="form-group">
                                        <label for="inputUser">User</label>
                                        <input class="form-control input-margin readonly" id="inputUser" readonly value="{html.escape(owner_username, quote=True)}" />
                                    </div>
                                </div>
                                <div class="col-lg-6 col-md-12 col-sm-12">
                                    <div class="form-group">
                                        <label for="inputDescription">Description</label>
                                        <textarea class="form-control input-margin" id="inputDescription" style="height: 125px;">{html.escape(pdesc)}</textarea>
                                    </div>
                                </div>
                                <div class="col-lg-6 col-md-12 col-sm-12">
                                    <div class="row">
                                        <div class="col-lg-4 col-md-4 col-sm-12">
                                            <div class="form-group">
                                                <label for="inputFiles">Files</label>
                                                <input class="form-control input-margin readonly" id="inputFiles" readonly value="{num_files}" />
                                            </div>
                                        </div>
                                        <div class="col-lg-4 col-md-4 col-sm-12">
                                            <div class="form-group">
                                                <label for="inputCreditsProcess">Processing Quote (€)</label>
                                                <input class="form-control input-margin readonly" id="inputCreditsProcess" readonly value="{html.escape('0 € (Local / Plan)' if credits_val == '0' else f'{credits_val} €', quote=True)}" />
                                            </div>
                                        </div>
                                        <div class="col-lg-4 col-md-4 col-sm-12">
                                            <div class="form-group">
                                                <label for="inputArea">Area</label>
                                                <input class="form-control input-margin readonly" id="inputArea" readonly value="{html.escape(area_val, quote=True)}" />
                                            </div>
                                        </div>
                                        <div class="col-lg-6 col-md-6 col-sm-12">
                                            <div class="form-group">
                                                <label for="inputCategories">Categories</label>
                                                <select id="inputCategories" class="form-control input-margin">
                                                    {''.join(cat_options)}
                                                </select>
                                            </div>
                                        </div>
                                        <div class="col-lg-6 col-md-6 col-sm-12">
                                            <div class="form-group">
                                                <label for="inputTags">Tags</label>
                                                <input id="inputTags" class="form-control input-margin" type="text" value="{html.escape(tags_val, quote=True)}" />
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div class="col-lg-6 col-md-6 col-sm-12">
                                    <div class="form-group">
                                        <label for="inputGPS">Average GPS accuracy (m)</label>
                                        <input class="form-control input-margin readonly" id="inputGPS" readonly value="{html.escape(gps_err_val, quote=True)}" />
                                    </div>
                                </div>
                                <div class="col-lg-6 col-md-6 col-sm-12">
                                    <div class="form-group">
                                        <label for="inputGSD">Ground resolution · GSD (cm/px)</label>
                                        <input class="form-control input-margin readonly" id="inputGSD" readonly value="{html.escape(gsd_val, quote=True)}" />
                                    </div>
                                </div>
                            </div>

                            <div id="divnumfiles" class="row" style="{'display:none;' if show_processed else 'display:flex;'}">
                                <div class="col-12 text-end" style="color: #BBBBBB; font-size: 14px; padding-right: 30px; padding-bottom: 17px; margin-top: 15px;">
                                    <label id="numberFiles">{num_files}</label> files
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div id="previewImages" class="container" style="{'display:block;' if show_processed else 'display:none;'}">
            <div class="row" style="margin-bottom: 15px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box processed-assets-heading">
                            <div class="processed-assets-heading-copy">
                                <span>MosaicAssets</span>
                                <small class="result-review-subtitle">Review every result before publishing</small>
                            </div>
                            <div class="processed-assets-actions">
                                <a class="btn btn-secondary project-source-action" href="/{norm_lang}/Projects?open_project={root_pid}">&larr; BACK TO MYEARTH TREE</a>
                                <button type="button" class="btn btn-primary project-publish-action" id="btnPublish" data-bs-toggle="modal" data-bs-target="#staticBackdrop7">PUBLISH SELECTED ASSETS</button>
                            </div>
                        </div>
                    </div>
                    <div class="content-box" style="padding-left: 30px!important; padding-right: 30px!important;">
                        <div class="processed-retention-explainer"><strong>Per-Asset Practical Controls &amp; Global Map Benefits (No 15-day deletion).</strong><span>Edit each MosaicAsset's Name, 3D Location (Lat, Lon, Height Offset) and Publication Mode directly on its card below. Private assets use your <strong>5 GiB Free Private Quota</strong>; assets published to the Global Map (Free 0 € or Marketplace €) consume <strong>0 GiB of your Private Quota</strong> and earn <strong>80% creator payout</strong> on Marketplace sales.</span></div>
                        <div class="row processed-assets-grid" style="padding-top: 40px; padding-bottom: 50px; margin-right: 0px; margin-left: 0px;">
                            <div class="col-12 col-md-6 col-xl-4 processed-asset-column" style="{'display:none;' if 2 not in available_types else ''}">
                                <div class="container processed-result-card mosaic-card-v2">
                                    <div class="row" style="margin-bottom: 20px;">
                                        <div class="col-6 proccesedtitleleft" style="padding-left: 0px!important;">
                                            Orthophoto
                                            <img id="iconPublicationOrthophoto" style="margin-left: 10px;" src="{ortho_icon_src}" />
                                        </div>
                                        <div class="col-6 text-end proccesedtitleright">
                                            <a class="menu-processed-drop dropdown-toggle dropdown-menu-dark" href="#" style="display: inline-block;" role="button" data-bs-toggle="dropdown" aria-expanded="false">OPTIONS <i style="margin-left: 10px;" class="fa fa-ellipsis-v"></i></a>
                                            <ul class="dropdown-menu menu-options">
                                                <a href="/Map.aspx?id={pid}" class="enlaces-procesado-mapa"><li class="item-option-menu">View</li></a>
                                                <a href="{ortho_dl}" download class="enlaces-procesado-mapa"><li class="item-option-menu-last">Download</li></a>
                                            </ul>
                                        </div>
                                    </div>
                                    <div class="row mosaic-card-preview" id="orthoPreview" style="display:block;">
                                        <img src="{ortho_thumb}" id="imagenProcesada" class="imgPreview" onerror="this.src='/Resources/mosaicearth-mark-transparent-cropped.png';this.style.objectFit='contain';this.style.padding='40px';" />
                                        <span class="mosaic-card-type-icon" title="Orthophoto" aria-label="Orthophoto">{ICON_ORTHOPHOTO}</span>
                                        <span class="mosaic-card-origin is-processed">Processed</span>
                                        <a class="mosaic-card-details" href="{ortho_st['detail_href']}">View details</a>
                                    </div>
                                    <div class="mosaic-card-body result-card-body">
                                        <h3 class="mosaic-card-title" id="cardTitleDisplay_2">{html.escape(ortho_st['custom_title'])}</h3>
                                        <p class="mosaic-card-creator">{html.escape(owner_username)}</p>
                                        <p class="mosaic-card-date"><span>CAPTURE</span><strong>{html.escape(cap_date_str)}</strong></p>
                                        <div class="mosaic-card-footer"><span class="mosaic-card-download"><span>Download</span><strong>GEOTIFF &middot; TIF</strong></span></div>
                                    </div>
                                    <span id="orthophotoStatusPill" class="{ortho_pill_cls}">{html.escape(ortho_st['badge_txt'])}</span>
                                    <div class="result-primary-actions">
                                        <a class="btn-primary result-review-button" href="/Map.aspx?id={pid}">REVIEW ORTHOPHOTO</a>
                                        <a class="result-secondary-action" href="{ortho_dl}" download>Download GeoTIFF</a>
                                    </div>
                                    {ortho_editor_html}
                                </div>
                            </div>

                            <div class="col-12 col-md-6 col-xl-4 processed-asset-column" style="{'display:none;' if 3 not in available_types else ''}">
                                <div class="container processed-result-card mosaic-card-v2">
                                    <div class="row" style="margin-bottom: 20px;">
                                        <div class="col-6 proccesedtitleleft" style="padding-left: 0px!important;">
                                            3D Model
                                            <img id="iconPublication3d" style="margin-left: 10px;" src="{model_icon_src}" />
                                        </div>
                                        <div class="col-6 text-end proccesedtitleright">
                                            <a class="menu-processed-drop dropdown-toggle dropdown-menu-dark" href="#" style="display: inline-block;" role="button" data-bs-toggle="dropdown" aria-expanded="false">OPTIONS <i style="margin-left: 10px;" class="fa fa-ellipsis-v"></i></a>
                                            <ul class="dropdown-menu menu-options">
                                                <a href="/MapEdit.aspx?id={pid}&amp;type=3" class="enlaces-procesado-mapa"><li class="item-option-menu">Relocate</li></a>
                                                <a href="/MapView.aspx?id={pid}&amp;type=3" class="enlaces-procesado-mapa"><li class="item-option-menu">View</li></a>
                                                <a href="{model_dl}" download class="enlaces-procesado-mapa"><li class="item-option-menu-last">Download</li></a>
                                            </ul>
                                        </div>
                                    </div>
                                    <div class="row" style="padding-top: 120px; padding-bottom: 30px;" id="show3d">
                                        <div class="col-12 text-center">
                                            <button style="width: 120px;" type="button" class="btn-primary" id="btnshow3d" onclick="show3d()">PREVIEW</button>
                                        </div>
                                    </div>
                                    <div class="row result-static-preview asset-model-preview mosaic-card-preview" id="3dpreview" style="--asset-preview:url('{ortho_thumb}')">
                                        <span class="mosaic-card-type-icon" title="3D model" aria-label="3D model">{ICON_3D}</span>
                                        <span class="mosaic-card-origin is-processed">Processed</span>
                                        <a class="mosaic-card-details" href="{model_st['detail_href']}">View details</a>
                                        <div id="cesiumContainer" style="width: 300px; height: 300px;"></div>
                                    </div>
                                    <div class="mosaic-card-body result-card-body">
                                        <h3 class="mosaic-card-title" id="cardTitleDisplay_3">{html.escape(model_st['custom_title'])}</h3>
                                        <p class="mosaic-card-creator">{html.escape(owner_username)}</p>
                                        <p class="mosaic-card-date"><span>CAPTURE</span><strong>{html.escape(cap_date_str)}</strong></p>
                                        <div class="mosaic-card-footer"><span class="mosaic-card-download"><span>Download</span><strong>OBJ &middot; ZIP</strong></span></div>
                                    </div>
                                    <span id="model3dStatusPill" class="{model_pill_cls}">{html.escape(model_st['badge_txt'])}</span>
                                    <div class="result-primary-actions">
                                        <a class="btn-primary result-review-button" href="/MapEdit.aspx?id={pid}&amp;type=3">REVIEW &amp; ADJUST PLACEMENT</a>
                                        <a class="result-secondary-action" href="{model_dl}" download>Download 3D model</a>
                                    </div>
                                    {model_editor_html}
                                </div>
                            </div>

                            <div class="col-12 col-md-6 col-xl-4 processed-asset-column" style="{'display:none;' if 1 not in available_types else ''}">
                                <div class="container processed-result-card mosaic-card-v2 pointcloud-result-card">
                                    <div class="row" style="margin-bottom: 20px;">
                                        <div class="col-6 proccesedtitleleft" style="padding-left: 0px!important;">
                                            Point Cloud
                                            <img id="iconPublicationPointcloud" style="margin-left: 10px;" src="{cloud_icon_src}" />
                                        </div>
                                        <div class="col-6 text-end proccesedtitleright">
                                            <a class="menu-processed-drop dropdown-toggle dropdown-menu-dark" href="#" style="display: inline-block;" role="button" data-bs-toggle="dropdown" aria-expanded="false">OPTIONS <i style="margin-left: 10px;" class="fa fa-ellipsis-v"></i></a>
                                            <ul class="dropdown-menu menu-options">
                                                <a href="/MapEdit.aspx?id={pid}&amp;type=1" class="enlaces-procesado-mapa"><li class="item-option-menu">Relocate</li></a>
                                                <a href="/MapView.aspx?id={pid}&amp;type=1" class="enlaces-procesado-mapa"><li class="item-option-menu">View</li></a>
                                                <a href="/Potree.aspx?id={pid}" target="_blank" class="enlaces-procesado-mapa"><li class="item-option-menu">Open advanced point-cloud tools</li></a>
                                                <a href="{cloud_dl}" download class="enlaces-procesado-mapa"><li class="item-option-menu-last">Download</li></a>
                                            </ul>
                                        </div>
                                    </div>
                                    <div class="row pointcloud-tool-frame result-static-preview asset-pointcloud-preview mosaic-card-preview" id="pointcloudPreview" style="--asset-preview:url('{ortho_thumb}')">
                                        <span class="mosaic-card-type-icon" title="Point cloud" aria-label="Point cloud">{ICON_CLOUD}</span>
                                        <span class="mosaic-card-origin is-processed">Processed</span>
                                        <a class="mosaic-card-details" href="{cloud_st['detail_href']}">View details</a>
                                        <div class="embedded-tool-label"><strong>Point-cloud preview</strong><span>Use Advanced tools for measurements, classifications and display controls.</span></div>
                                        <iframe id="orthophotoPreview" src="about:blank" data-src="/Potree.aspx?id={pid}" style="width: 300px; height: 300px;" title="Interactive point-cloud preview"></iframe>
                                    </div>
                                    <div class="row" style="padding-top: 120px; padding-bottom: 30px;" id="pointcloudShow">
                                        <div class="col-12 text-center">
                                            <button style="width: 120px;" type="button" class="btn-primary" id="btnshowpointcloud" onclick="showPointcloud()">PREVIEW</button>
                                        </div>
                                    </div>
                                    <div class="mosaic-card-body result-card-body">
                                        <h3 class="mosaic-card-title" id="cardTitleDisplay_1">{html.escape(cloud_st['custom_title'])}</h3>
                                        <p class="mosaic-card-creator">{html.escape(owner_username)}</p>
                                        <p class="mosaic-card-date"><span>CAPTURE</span><strong>{html.escape(cap_date_str)}</strong></p>
                                        <div class="mosaic-card-footer"><span class="mosaic-card-download"><span>Download</span><strong>LAS &middot; ZIP</strong></span></div>
                                    </div>
                                    <span id="pointcloudStatusPill" class="{cloud_pill_cls}">{html.escape(cloud_st['badge_txt'])}</span>
                                    <div class="result-primary-actions">
                                        <a class="btn-primary result-review-button" href="/MapEdit.aspx?id={pid}&amp;type=1">REVIEW &amp; ADJUST PLACEMENT</a>
                                        <a class="result-secondary-action" href="/Potree.aspx?id={pid}" target="_blank">Open advanced tools</a>
                                        <a class="result-secondary-action" href="{cloud_dl}" download>Download point cloud</a>
                                    </div>
                                    {cloud_editor_html}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        {additional_html}
    </section>

    <!-- Upload, Upfront Quote & Publish Modals expected by Project.js -->
    <div class="modal fade mosaic-workflow" id="staticBackdrop3" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" style="max-width:540px!important;">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Select Photogrammetry Processing Engine</h5>
                </div>
                <div class="modal-body">
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;">
                        <button type="button" id="btnSelectCloudEngine" onclick="selectTaskEngineMode('cloud')" style="{'background:#111713;border:1.5px solid #35443b;color:#d1d5db;' if is_desktop_local_mode else 'background:rgba(153,204,0,0.16);border:1.5px solid #99CC00;color:#fff;'}border-radius:8px;padding:10px;text-align:left;cursor:pointer;">
                            <div style="font-size:10px;color:#99CC00;font-weight:700;text-transform:uppercase;">Option 1 &middot; Cloud</div>
                            <div style="font-size:12.5px;font-weight:700;margin-top:2px;">MosaicEarth Cloud</div>
                            <div style="font-size:11px;color:#d9f99d;margin-top:2px;">{upfront_eur_val:.2f} &euro; (100% Failure Refund)</div>
                        </button>
                        <button type="button" id="btnSelectLocalEngine" onclick="selectTaskEngineMode('local')" style="{'background:rgba(153,204,0,0.16);border:1.5px solid #99CC00;color:#fff;' if is_desktop_local_mode else 'background:#111713;border:1.5px solid #35443b;color:#d1d5db;'}border-radius:8px;padding:10px;text-align:left;cursor:pointer;">
                            <div style="font-size:10px;color:#99CC00;font-weight:700;text-transform:uppercase;">Option 2 &middot; Your PC</div>
                            <div style="font-size:12.5px;font-weight:700;margin-top:2px;">Local PC (Desktop)</div>
                            <div style="font-size:11px;color:#99CC00;margin-top:2px;">0.00 &euro; &middot; Local NodeODX / ODX</div>
                        </button>
                    </div>
                    <input type="hidden" id="photogrammetryPreset" value="complete" />
                    <div class="mb-3" style="padding:10px 13px;background:#101914;border:1px solid rgba(153,204,0,0.4);border-radius:8px;">
                        <div style="font-size:11px;font-weight:800;color:#99CC00;text-transform:uppercase;letter-spacing:0.05em;">
                            <i class="fas fa-check-circle me-1"></i> Maximum Quality Always Active
                        </div>
                        <div style="font-size:12px;color:#dce8e0;margin-top:2px;">
                            {'Ultra feature extraction &middot; High-density 3D point cloud &middot; 400k-face 3D textured mesh &middot; 1.0 cm/px Orthophoto + DSM + DTM.' if has_photo_georef else 'Unreferenced 3D Reconstruction Mode: Ultra feature extraction &middot; High-density 3D point cloud + 3D textured mesh anchored at your selected map pin.'}
                        </div>
                    </div>
                    <div id="cloudEnginePanel" style="{'display:none;' if is_desktop_local_mode else 'display:block;'}">
                        {f'''<div style="background:#17212b;border:1px solid #38bdf8;border-radius:8px;padding:12px;margin-bottom:12px;font-size:12px;color:#e0f2fe;">
                            <div style="font-weight:700;color:#38bdf8;margin-bottom:4px;"><i class="fas fa-cloud me-1"></i> MosaicEarth Cloud Account Required for Cloud Processing</div>
                            <div style="margin-bottom:8px;">You are currently in Local PC mode. Sign in with your MosaicEarth Cloud account to process on Cloud GPU nodes (or switch to <strong>Option 2 · Local PC</strong> to process at 0.00 &euro; on your computer).</div>
                            <a href="/Default.aspx?switch_account=1&amp;ReturnUrl=%2FClient%2FProject.aspx%3Fid%3D{pid}" class="btn btn-sm" style="background:#38bdf8;color:#071018;font-weight:700;font-size:11.5px;padding:5px 12px;border-radius:6px;text-decoration:none;">Sign In to MosaicEarth Cloud Account &rarr;</a>
                        </div>''' if (is_desktop_local_mode and not desktop_cloud_logged_in) else ''}
                        <div style="background:#101713;border:1px solid #99CC00;border-radius:8px;padding:14px;margin-bottom:12px;">
                            <div style="font-size:11px;font-weight:700;color:#99CC00;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">
                                <i class="fas fa-microchip me-1"></i> {'ODX Engine (Cloud NodeODX)' if os.environ.get('MOSAICEARTH_RUNPOD_PHOTOGRAMMETRY') == 'nodeodx' else 'OpenDroneMap Engine (Cloud NodeODM)'}
                            </div>
                            <div style="font-size:13px;color:#fff;margin-bottom:6px;">
                                Dataset: <strong id="numberFilestoProcess">{num_files}</strong> images (<strong id="numMegabytes">{total_files_mb:.2f}</strong> MB)
                            </div>
                            <div style="font-size:13px;color:#d9f99d;font-weight:700;">
                                Upfront Quote: <span id="creditsCost">{upfront_eur_val:.2f} &euro;</span>
                                <div style="font-size:11.5px;color:#b8c6be;font-weight:400;margin-top:4px;" id="textcreditsCost">{upfront_summary_lbl}</div>
                            </div>
                        </div>
                        <div style="font-size:12px;color:#cbd5e1;line-height:1.55;background:#121814;border:1px solid #2e3d33;border-radius:8px;padding:12px;">
                            <strong style="color:#99CC00;"><i class="fas fa-shield-alt me-1"></i> Upfront Pre-Authorization &amp; 100% Failure Refund Guarantee:</strong><br/>
                            To guarantee compute resources, the exact task price (or monthly plan quota) is authorized <strong>before</strong> processing begins. If the photogrammetry job fails or is cancelled before producing your deliverables, <strong>100% of the payment or quota is refunded automatically (`0.00 &euro;` charged)</strong>.
                            <span id="availableCredits" style="display:none;">999999</span>
                            <label id="textNoAvailable" style="display:none;"></label>
                            <label id="textAvailable" style="display:none;"></label>
                        </div>
                    </div>
                    <div id="localEnginePanel" style="{'display:block;' if is_desktop_local_mode else 'display:none;'}">
                        <div style="background:#101713;border:1px solid #99CC00;border-radius:8px;padding:14px;margin-bottom:12px;">
                            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px;">
                                <span style="font-size:11px;font-weight:700;color:#99CC00;text-transform:uppercase;"><i class="fas fa-desktop me-1"></i> Official OpenDroneMap (Local PC &middot; 0.00 &euro;)</span>
                                <span id="localEngineStatusBadge" style="font-size:10.5px;padding:2px 8px;border-radius:99px;background:rgba(153,204,0,0.22);color:#99CC00;">ONLINE &middot; Built-in Local ODM Engine (0.00 &euro;)</span>
                            </div>
                            <p style="font-size:12px;color:#dce6dc;margin:0 0 8px;line-height:1.5;">
                                Processes your drone images at <strong>0.00 &euro;</strong> using your computer's CPU or compatible GPU via MosaicEarth Desktop's built-in <strong>NodeODX / ODX</strong> engine — completely integrated inside the application without external windows.
                            </p>
                            <div id="localEngineHowTo" style="font-size:11.5px;color:#b8c6be;background:#0b100d;border:1px solid #28362e;border-radius:6px;padding:10px;line-height:1.5;">
                                <div style="font-size:11px;color:#99CC00;font-weight:700;">
                                    &check; Ready to process {num_files} images locally on your PC disk (0.00 &euro;)
                                </div>
                                <div style="margin-top:4px;font-size:11px;color:#9ca3af;">
                                    Produces georeferenced <strong>Orthophotos (GeoTIFF)</strong>, <strong>3D Textured Models</strong>, <strong>Dense Point Clouds (LAZ)</strong>, <strong>DSM (Digital Surface Model)</strong>, and <strong>DTM (Digital Terrain Model)</strong> directly on your PC hard drive.
                                </div>
                            </div>
                        </div>
                    </div>
                    <div style="background:#0f1612;border:1px solid #2b3c32;border-radius:8px;padding:12px;margin-bottom:10px;font-size:12px;color:#dce6dc;">
                        <div style="font-size:11px;font-weight:700;color:#99CC00;text-transform:uppercase;margin-bottom:6px;">
                            <i class="fas fa-layer-group me-1"></i> Maximum Quality Deliverables
                        </div>
                        <div style="font-size:11.5px;color:#b8c6be;line-height:1.55;">
                            {'''Five georeferenced products, verified before completion:<br/>
                            &bull; <strong>Orthophoto</strong> (<code>GeoTIFF</code> + Map Tiles) &middot; <strong>Dense Point Cloud</strong> (<code>LAZ</code> + <code>3D Tiles</code>) &middot; <strong>3D Textured Mesh</strong> (<code>OBJ/ZIP</code> + <code>3D Tiles</code>)<br/>
                            &bull; <strong>DSM — Digital Surface Model</strong> (<code>dsm.tif</code>) &middot; <strong>DTM — Digital Terrain Model / Bare Earth</strong> (<code>dtm.tif</code>)<br/>
                            <span style="font-size:11px;color:#9ca3af;"><em>Terrain Derivatives (Hillshade, Slope, Aspect &amp; Contours) can be generated on demand from any DSM or DTM asset in MyEarth.</em></span>''' if has_photo_georef else '''Unreferenced 3D Reconstruction (no EXIF GPS or GCPs attached):<br/>
                            &bull; <strong>3D Textured Mesh</strong> (<code>OBJ/ZIP</code> + <code>3D Tiles</code>) &middot; <strong>Dense Point Cloud</strong> (<code>LAZ</code> + <code>3D Tiles</code>)<br/>
                            <span style="font-size:11px;color:#fde68a;"><em>Note: Metric Orthophoto, DSM, and DTM require EXIF GPS or 3+ Ground Control Points (click <strong>GCP STUDIO</strong> before processing to enable them).</em></span>'''}
                        </div>
                        <input type="hidden" id="chkEnableDtm" value="1" checked="checked" />
                    </div>
                    <script>
                        window.__meIsDesktopLocal = {'true' if os.environ.get('MOSAICEARTH_DESKTOP_LOCAL') == '1' else 'false'};
                        window.__meTaskEngineMode = window.__meIsDesktopLocal ? 'local' : 'cloud';
                        window.__meLocalEngineReady = false;
                        window.__mePhotoHasGeoref = {'true' if has_photo_georef else 'false'};
                        window.__mePhotoHasApproxPin = {'true' if has_user_approx_pin else 'false'};
                        function probeLocalNodeOdmEngine() {{
                            var badge = document.getElementById('localEngineStatusBadge');
                            fetch('/api/desktop/nodeodm-status').then(function(r) {{ return r.json(); }}).then(function(info) {{
                                if (info && info.online) {{
                                    window.__meLocalEngineReady = true;
                                    if (badge) {{
                                        badge.textContent = 'ONLINE · NodeODX / ODX ' + (info.engineVersion || info.version || 'unknown') + ' · 0.00 €)';
                                        badge.style.background = 'rgba(153,204,0,0.22)';
                                        badge.style.color = '#99CC00';
                                    }}
                                }} else {{
                                    window.__meLocalEngineReady = false;
                                    if (badge) {{
                                        badge.textContent = 'STANDBY · Click Activate Local ODM';
                                        badge.style.background = 'rgba(245,158,11,0.20)';
                                        badge.style.color = '#fcd34d';
                                    }}
                                }}
                            }}).catch(function() {{
                                window.__meLocalEngineReady = false;
                            }});
                        }}
                        function startLocalNodeOdmFromModal() {{
                            var badge = document.getElementById('localEngineStatusBadge');
                            if (badge) badge.textContent = 'Starting local NodeODX…';
                            fetch('/api/desktop/start-nodeodm', {{ method: 'POST' }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
                                if (d && d.online) {{
                                    toastr.success(d.message || 'Local NodeODM is ONLINE!');
                                }} else {{
                                    toastr.warning((d && d.message) || 'Could not start local NodeODX.');
                                }}
                                probeLocalNodeOdmEngine();
                            }});
                        }}
                        function selectTaskEngineMode(mode) {{
                            window.__meTaskEngineMode = mode;
                            var bCloud = document.getElementById('btnSelectCloudEngine');
                            var bLocal = document.getElementById('btnSelectLocalEngine');
                            var pCloud = document.getElementById('cloudEnginePanel');
                            var pLocal = document.getElementById('localEnginePanel');
                            var btn = document.getElementById('processTask');
                            if (mode === 'local') {{
                                bLocal.style.background = 'rgba(153,204,0,0.16)'; bLocal.style.borderColor = '#99CC00'; bLocal.style.color = '#fff';
                                bCloud.style.background = '#111713'; bCloud.style.borderColor = '#35443b'; bCloud.style.color = '#d1d5db';
                                pCloud.style.display = 'none'; pLocal.style.display = 'block';
                                probeLocalNodeOdmEngine();
                                if (btn) btn.textContent = 'START LOCAL NODEODX PROCESSING (0.00 €)';
                            }} else {{
                                bCloud.style.background = 'rgba(153,204,0,0.16)'; bCloud.style.borderColor = '#99CC00'; bCloud.style.color = '#fff';
                                bLocal.style.background = '#111713'; bLocal.style.borderColor = '#35443b'; bLocal.style.color = '#d1d5db';
                                pCloud.style.display = 'block'; pLocal.style.display = 'none';
                                if (btn) btn.textContent = 'AUTHORIZE ({upfront_eur_val:.2f} €) & START CLOUD PROCESSING';
                            }}
                        }}
                        function submitTaskToSelectedEngine() {{
                            var params = new URLSearchParams(window.location.search);
                            var pid = parseInt(params.get('id') || '{pid}', 10);
                            var latInp = document.getElementById('photoApproxLat');
                            var lonInp = document.getElementById('photoApproxLon');
                            var latVal = latInp ? latInp.value.trim() : '';
                            var lonVal = lonInp ? lonInp.value.trim() : '';
                            if (!window.__mePhotoHasGeoref && (!latVal || !lonVal)) {{
                                $('#staticBackdrop3').modal('hide');
                                toastr.warning('Unreferenced dataset: click on the map to set an approximate initial location (or mark 3+ GCPs in GCP Studio) before starting processing.');
                                var mapEl = document.getElementById('photoTaskLocationMap');
                                if (mapEl) mapEl.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                                return;
                            }}
                            var btn = document.getElementById('processTask');
                            var preset = 'complete';
                            var origTxt = btn ? btn.textContent : '';
                            if (btn) {{ btn.disabled = true; btn.textContent = 'Submitting photogrammetry…'; }}
                            $.ajax({{
                                type: 'POST',
                                url: '/Client/Project.aspx/btnprocess',
                                contentType: 'application/json; charset=utf-8',
                                dataType: 'json',
                                timeout: 300000,
                                data: JSON.stringify({{ project_id: pid, target_mode: window.__meTaskEngineMode || 'auto', processing_preset: preset, lat: latVal, lon: lonVal }}),
                                success: function(res) {{
                                    if (btn) {{ btn.disabled = false; btn.textContent = origTxt; }}
                                    var d = res && res.d ? res.d : '';
                                    if (typeof d === 'string' && d.indexOf('STRIPE_URL:') === 0) {{
                                        window.location.href = d.substring(11);
                                        return;
                                    }}
                                    if (d === 'OK') {{
                                        $('#staticBackdrop3').modal('hide');
                                        toastr.success('Photogrammetry task submitted.');
                                        setTimeout(function() {{ window.location.reload(); }}, 500);
                                    }} else if (d === 'nofiles') {{
                                        toastr.error('Please upload drone images first (+ ADD).');
                                    }} else if (d === 'nocreditsuser') {{
                                        toastr.error('Insufficient authorization or cloud plan quota.');
                                    }} else if (typeof d === 'string' && d.indexOf('ERROR:') === 0) {{
                                        toastr.error(d.substring(6));
                                    }} else {{
                                        toastr.error(String(d || 'Could not start OpenDroneMap processing.'));
                                    }}
                                }},
                                error: function(xhr) {{
                                    if (btn) {{ btn.disabled = false; btn.textContent = origTxt; }}
                                    var msg = (xhr && xhr.responseText) ? xhr.responseText.substring(0, 180) : 'Error communicating with server.';
                                    toastr.error(msg);
                                }}
                            }});
                        }}
                        document.addEventListener('DOMContentLoaded', function() {{
                            if (window.__meIsDesktopLocal) {{
                                selectTaskEngineMode('local');
                            }}
                            var m3 = document.getElementById('staticBackdrop3');
                            if (m3) m3.addEventListener('shown.bs.modal', function() {{
                                if (window.__meIsDesktopLocal) selectTaskEngineMode('local');
                                probeLocalNodeOdmEngine();
                            }});
                        }});
                    </script>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                    <button id="processTask" type="button" onclick="submitTaskToSelectedEngine()" class="btn btn-primary" style="font-size: 12px!important; font-weight:700; padding: 10px 24px;">
                        {'START LOCAL NODEODX PROCESSING (0.00 €)' if os.environ.get('MOSAICEARTH_DESKTOP_LOCAL') == '1' else f'AUTHORIZE ({upfront_eur_val:.2f} &euro;) &amp; START CLOUD PROCESSING'}
                    </button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade mosaic-workflow" id="staticBackdrop4" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Cancel Processing &amp; Refund Upfront Authorization</h5>
                </div>
                <div class="modal-body">
                    <p style="font-size:13px;color:#d1d5db;">Cancel this active processing task? Any upfront Euro (&euro;) charge or monthly plan image quota reserved for this task will be <strong>100% refunded automatically</strong>.</p>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">KEEP PROCESSING</button>
                    <button type="button" onclick="cancelTask()" class="btn btn-primary" style="font-size: 12px!important; padding: 10px 28px;">CONFIRM CANCEL &amp; REFUND</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade mosaic-workflow" id="staticBackdrop2" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-labelledby="staticBackdropLabel" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="staticBackdropLabel">Add Drone / Aerial Photos <button type="button" class="upload-help-button" data-bs-toggle="modal" data-bs-target="#staticBackdropRecom" aria-label="Upload recommendations">?</button></h5>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="col-12 text-center">
                            <div class="dropzone" style="border:2px dashed rgba(153,204,0,0.55);border-radius:12px;padding:28px 18px;background:#101713;cursor:pointer;">
                                <input type="file" id="myFile1" class="dropzone-file-input" aria-label="Select source images" accept=".jpg,.jpeg,.png,.webp" onchange="getDocument1(this)" multiple="multiple" style="display:none;" />
                                <div class="divaddfiles">
                                    <span class="upload-cloud-icon" aria-hidden="true" style="display:inline-flex;width:44px;height:44px;color:#99CC00;margin-bottom:8px;"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="44" height="44"><path d="M7 18h10a4 4 0 0 0 .4-8A6 6 0 0 0 6 8.6 4.7 4.7 0 0 0 7 18Zm5-8v6m-3-3 3-3 3 3"/></svg></span>
                                    <br />
                                    <span id="titleaddfiles" style="font-size:15px;font-weight:700;color:#fff;">Click to select drone / aerial / terrestrial photos</span>
                                    <br />
                                    <span id="subtitleaddfiles" style="font-size:12.5px;color:#99CC00;">or drag and drop JPEG, PNG, or WEBP files here</span>
                                    <p class="profile-field-help" style="font-size:11.5px;color:#9ca3af;margin-top:10px;margin-bottom:0;">EXIF GPS coordinates and dimensions are read automatically. Have a video instead? Close and click <strong>+ VIDEO FRAMES</strong>.</p>
                                </div>
                                <div class="porcentajeaddfiles" style="display: none;">
                                    <div class="source-transfer-card source-transfer-card--embedded">
                                        <span class="source-transfer-orbit" aria-hidden="true"><i></i></span>
                                        <div class="source-transfer-copy">
                                            <small style="color:#99CC00;font-weight:700;">SOURCE UPLOAD IN PROGRESS</small>
                                            <h3 style="color:#fff;font-size:16px;margin:6px 0;">Uploading source imagery</h3>
                                            <p style="color:#b8c6be;font-size:12px;">The selected photos are being stored in this Photogrammetry task.</p>
                                            <div class="source-transfer-meter">
                                                <div class="source-transfer-meter-head"><strong id="porcentajefiles" style="color:#99CC00;">0%</strong> <span id="uploadBatchStatus" style="color:#d1d5db;font-size:12px;">Preparing files…</span></div>
                                                <div class="source-transfer-bar" style="height:8px;background:#1c2620;border-radius:99px;overflow:hidden;margin:8px 0;"><i id="uploadLinearBar" style="display:block;height:100%;width:0%;background:#99CC00;"></i></div>
                                            </div>
                                            <strong id="uploadStatusText" class="source-transfer-status" style="color:#fff;font-size:12px;display:block;">PREPARING FILES…</strong>
                                            <span class="source-transfer-warning" style="color:#9ca3af;font-size:11px;display:block;margin-top:4px;">Keep this window open until the transfer completes.</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="col-12" style="margin-top: 22px;">
                            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
                                <span id="title-gcp" style="font-size:12px;font-weight:700;color:#dce6dc;">Optional Ground Control Points (GCP)</span>
                                <button type="button" class="btn btn-sm" style="background:rgba(56,189,248,0.16);border:1px solid #38bdf8;color:#bae6fd;font-size:11px;font-weight:700;padding:3px 10px;" onclick="$('#staticBackdrop2').modal('hide');openPhotogrammetryGcpStudio({pid});">
                                    &#8982; Open MosaicEarth GCP Studio
                                </button>
                            </div>
                            <div class="gcp-upload" style="background:#101713;border:1px solid #2b3930;border-radius:8px;padding:10px 14px;margin-top:6px;">
                                <div class="row align-items-center">
                                    <div class="col-8">
                                        <img id="checkGCP" src="/Resources/checkGCP.svg" style="margin-right: 10px; display: {'inline' if control_name else 'none'};" /><span id="placeholdergcp" style="font-size:12px;color:#9ca3af;">{html.escape(control_name) if control_name else 'Or upload an existing gcp_list.txt / geo.txt'}</span>
                                    </div>
                                    <div class="col-4 text-end">
                                        <span class="uploadbuttongcp" style="display:{'none' if control_name else 'inline'};color:#99CC00;font-size:11.5px;font-weight:700;cursor:pointer;">UPLOAD .TXT</span>
                                        <span class="deletebuttongcp" onclick="deleteGCP({pid});" style="display:{'inline' if control_name else 'none'};color:#ef4444;font-size:11.5px;font-weight:700;cursor:pointer;">DELETE</span>
                                    </div>
                                </div>
                            </div>
                            <input type="file" id="myFileGCP" accept=".txt" onchange="getDocumentGCP(this)" style="display: none" />
                            <input type="hidden" id="NombreDocumento1" value="" />
                            <input type="hidden" id="Base641" value="" />
                            <input type="hidden" id="FileName1" value="" />
                            <input type="hidden" id="FileType1" value="" />
                            <input type="hidden" id="FileSize1" value="" />
                            <input type="hidden" id="docId1" value="0" />
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <div class="row w-100">
                        <div class="col-6">
                            <button id="cancelUploadFiles" type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                        </div>
                        <div class="col-6 text-end">
                            <button type="button" class="btn btn-primary" onclick="subirfichero1()">SELECT PHOTOS</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade mosaic-workflow" id="modalVideoPhotogrammetry" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" style="max-width:560px!important;">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">&#127916; Extract Sharp Photogrammetry Frames from Video</h5>
                </div>
                <div class="modal-body" style="font-size:12.5px;color:#d1d5db;">
                    <p style="margin-bottom:12px;color:#cbd5e1;line-height:1.55;">
                        Upload a drone or handheld capture video (<code>.MP4 / .MOV / .WEBM / .M4V</code>) plus an optional telemetry track (<code>.SRT / .GPX / .CSV</code>). MosaicEarth extracts sharp overlapping keyframes, injects GPS EXIF when available, and calculates your <strong>exact per-photo Euro quote</strong> before you launch processing.
                    </p>
                    <div class="mb-3">
                        <label style="font-size:11.5px;font-weight:700;color:#fff;margin-bottom:4px;display:block;">Video File (.MP4, .MOV, .WEBM, .M4V) *</label>
                        <input type="file" id="inpPhotoVideoFile" accept=".mp4,.mov,.webm,.m4v" class="form-control" style="background:#141c17;border:1px solid #99CC00;color:#fff;font-size:13px;" />
                    </div>
                    <div class="mb-3">
                        <label style="font-size:11.5px;font-weight:700;color:#cbd5e1;margin-bottom:4px;display:block;">Companion GPS Telemetry (.SRT, .GPX, .CSV) &mdash; optional</label>
                        <input type="file" id="inpPhotoVideoTelem" accept=".srt,.gpx,.csv" class="form-control" style="background:#141c17;border:1px solid #334439;color:#fff;font-size:13px;" />
                    </div>
                    <div id="videoStageProgressWrap" style="display:none;background:#0e1511;border:1px solid #2b3c32;border-radius:8px;padding:12px;">
                        <div style="display:flex;justify-content:space-between;font-size:12px;font-weight:700;color:#99CC00;margin-bottom:6px;">
                            <span id="videoStageStatusText">Uploading video...</span>
                            <span id="videoStagePctText">0%</span>
                        </div>
                        <div style="height:8px;background:#1a241e;border-radius:999px;overflow:hidden;">
                            <div id="videoStageBar" style="width:0%;height:100%;background:#99CC00;transition:width 0.2s;"></div>
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" id="btnCancelVideoStage" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                    <button type="button" id="btnStartVideoStage" onclick="stageVideoForPhotogrammetry({pid})" class="btn btn-primary" style="font-weight:700;">EXTRACT FRAMES &amp; QUOTE</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade mosaic-workflow" id="staticBackdropRecom" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" style="max-width: 560px!important;">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Photogrammetry Capture Recommendations</h5>
                </div>
                <div class="modal-body" style="font-size:12.5px;color:#d1d5db;line-height:1.6;">
                    <p>Upload overlapping JPEG or PNG aerial/drone or terrestrial photos with embedded EXIF GPS coordinates when available:</p>
                    <ul style="padding-left:18px;">
                        <li><strong>Overlap:</strong> Capture photos with at least 70% frontal and 65% side overlap.</li>
                        <li><strong>Outputs generated automatically:</strong> Georeferenced Orthophoto (GeoTIFF &amp; Web Tiles), 3D Textured Mesh (3D Tiles &amp; OBJ/GLB), and Dense Point Cloud (LAZ &amp; Potree/3D Tiles).</li>
                    </ul>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CLOSE</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade mosaic-workflow" id="staticBackdrop7" data-bs-keyboard="true" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Publish MosaicAssets</h5>
                </div>
                <div class="modal-body">
                    <p style="font-size:13px;color:#ccc;">Publish one or more results from this project on MosaicEarth. Published MosaicAssets appear immediately on the global map and remain downloadable.</p>
                    <div class="row mt-3 mb-2">
                        <div class="col-6">
                            <label><input id="checkOrthophoto" type="checkbox" {'checked disabled' if ortho_st['is_pub'] else ''} /> Orthophoto</label>
                        </div>
                        <div class="col-6">
                            <label><input id="check3DModel" type="checkbox" {'checked disabled' if model_st['is_pub'] else ''} /> 3D Model</label>
                        </div>
                        <div class="col-6 mt-2">
                            <label><input id="checkPointCloud" type="checkbox" {'checked disabled' if cloud_st['is_pub'] else ''} /> Point Cloud</label>
                        </div>
                    </div>
                    <div class="form-group mt-3">
                        <label for="inputCredits">Marketplace License Price in Euros (€) — (0 € = Free Public Asset; &gt; 0 € = Paid Commercial License, 85% Creator Payout)</label>
                        <input id="inputCredits" type="number" min="0" step="1" class="form-control" value="0" />
                    </div>
                    <div class="row mt-3">
                        <div class="col-6">
                            <label for="inputCaptureFrom">Capture start date</label>
                            <input id="inputCaptureFrom" type="date" class="form-control" value="{cap_dt.strftime('%Y-%m-%d') if cap_dt else ''}" />
                        </div>
                        <div class="col-6">
                            <label for="inputCaptureTo">Capture end date</label>
                            <input id="inputCaptureTo" type="date" class="form-control" value="{cap_dt.strftime('%Y-%m-%d') if cap_dt else ''}" />
                        </div>
                    </div>
                    <input type="hidden" id="selectCapturePrecision" value="day" />
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                    <button type="button" class="btn btn-primary" onclick="requestPublish()">PUBLISH NOW</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade mosaic-workflow" id="modalNewPremiumProject" data-bs-keyboard="true" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Activate Private Premium Storage</h5>
                </div>
                <div class="modal-body">
                    <p style="font-size:13px;color:#ccc;">Keep a project and all its private files stored after the 15-day free window for 20 credits per month.</p>
                    <select id="selectProjectList" class="form-control mt-3 mb-3">
                        {''.join(elig_opts)}
                    </select>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                    <button type="button" class="btn btn-primary" onclick="upgradeProject()">ACTIVATE FOR 20 CREDITS</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade mosaic-workflow" id="modalAddSubproject" data-bs-keyboard="true" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Add Subproject to "{html.escape(root_pname)}"</h5>
                </div>
                <div class="modal-body">
                    <p style="font-size:13px;color:#ccc;">Add a new drone capture date, imported 3D/GIS file, or Gaussian Splat as a Subproject inside this Project.</p>
                    <div class="form-group mb-3">
                        <label for="subprojName">Subproject Name</label>
                        <input type="text" id="subprojName" class="form-control" placeholder="e.g. Flight 2 - November 2026 / Imported Laser Scan" />
                    </div>
                    <div class="form-group mb-3">
                        <label for="subprojKind">Subproject Workflow Type</label>
                        <select id="subprojKind" class="form-control">
                            <option value="processing">Photogrammetry Processing (Images &rarr; Orthophoto, 3D Model, Point Cloud, DSM/DTM)</option>
                            <option value="external">Import Finished Asset (LAS/LAZ, OBJ/FBX/GLB, GeoTIFF, PLY &rarr; Web-Friendly 3D Tiles/COG)</option>
                            <option value="gaussian">Gaussian Splat Generation (Photos or Video &rarr; 3D Splat)</option>
                        </select>
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                    <button type="button" class="btn btn-primary" onclick="createSubprojectNow({root_pid})">CREATE SUBPROJECT</button>
                </div>
            </div>
        </div>
    </div>

    <input type="hidden" id="hdnid" value="{pid}" />
    <input type="hidden" id="hdnEstado" value="{state_id}" />
    <input type="hidden" id="hdnZip" value="{zip_uploaded}" />
    <input type="hidden" id="hdnAvailableCredits" value="{user_avail_credits}" />
    <input type="hidden" id="hdnHandler" value="/ProjectImageUpload.ashx" />
    <input type="hidden" id="hdnFiles" value="{num_files}" />
    <input type="hidden" id="hdnTranslations" value="{{}}" />"""

    scripts_extra = f"""
    <script>
        if (typeof MYLIBRARY !== 'undefined' && MYLIBRARY && typeof MYLIBRARY.setUrl_page === 'function') {{
            MYLIBRARY.setUrl_page('/Client/Project.aspx');
        }}
    </script>
    <script src="/Admin/assets/js/plugins/select2/select2.full.min.js"></script>
    <script src="/assets/js/plugins/bootstrap-tagsinput/bootstrap-tagsinput.js"></script>
    <script src="/js/front/ProjectUploadQueue.js?v=1"></script>
    <script src="/js/front/Project.js?v=20261016-r46"></script>
    <script>
        const URL_CESIUM = {json.dumps(url_cesium_3d)};
        const COMPLETED = {json.dumps('true' if show_processed else 'false')};
        const HEIGHT = {json.dumps(height_3d)};
        const key = {json.dumps(cesium_key)};
        var __user_id = {uid};
        var __project_id = {pid};
        var __can_reprocess_sources = false;
        window.__meTaskCompletedRedirectUrl = "/{norm_lang}/Projects?open_project={root_pid}#taskNode_{pid}";
        window.__meExifGpsPoints = {exif_gps_json};
        MYLIBRARY.setUrl_page('/Client/Project.aspx');
        CargarActiveMenu("li_projects");

        var __photoLocMap = null;
        var __photoApproxMarker = null;
        function setPhotogrammetryPin(lat, lon, fly, persist) {{
            if (!isFinite(lat) || !isFinite(lon)) return;
            var laInp = document.getElementById('photoApproxLat');
            var loInp = document.getElementById('photoApproxLon');
            if (laInp) laInp.value = Number(lat).toFixed(6);
            if (loInp) loInp.value = Number(lon).toFixed(6);
            window.__mePhotoHasApproxPin = true;
            if (__photoLocMap && window.L) {{
                if (!__photoApproxMarker) {{
                    __photoApproxMarker = window.L.marker([lat, lon], {{ draggable: true }}).addTo(__photoLocMap);
                    __photoApproxMarker.on('dragend', function() {{
                        var p = __photoApproxMarker.getLatLng();
                        setPhotogrammetryPin(p.lat, p.lng, false, true);
                    }});
                }} else {{
                    __photoApproxMarker.setLatLng([lat, lon]);
                }}
                if (fly) __photoLocMap.setView([lat, lon], Math.max(__photoLocMap.getZoom(), 16));
            }}
            if (persist) {{
                fetch('/api/v2/tasks/' + __project_id + '/location', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ lat: Number(lat), lon: Number(lon) }})
                }}).then(function(r) {{
                    if (r.ok) {{
                        var b = document.getElementById('photoGeorefStatusBadge');
                        if (b && !window.__mePhotoHasGeoref) {{
                            b.textContent = '✓ Initial approximate pin set (' + Number(lat).toFixed(4) + ', ' + Number(lon).toFixed(4) + ') — 3D Mesh + Point Cloud ready';
                            b.style.background = 'rgba(153,204,0,0.18)';
                            b.style.borderColor = '#99CC00';
                            b.style.color = '#d9f99d';
                        }}
                    }}
                }}).catch(function() {{}});
            }}
        }}
        function savePhotogrammetryManualCoords() {{
            var la = parseFloat((document.getElementById('photoApproxLat').value || '').replace(',', '.'));
            var lo = parseFloat((document.getElementById('photoApproxLon').value || '').replace(',', '.'));
            if (isFinite(la) && isFinite(lo) && Math.abs(la) <= 90 && Math.abs(lo) <= 180) {{
                setPhotogrammetryPin(la, lo, true, true);
            }}
        }}
        function searchPhotogrammetryMapPlace() {{
            var q = (document.getElementById('photoMapSearchInput').value || '').trim();
            if (!q || !__photoLocMap) return;
            fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(q))
                .then(function(r) {{ return r.json(); }})
                .then(function(arr) {{
                    if (Array.isArray(arr) && arr[0]) {{
                        var la = parseFloat(arr[0].lat), lo = parseFloat(arr[0].lon);
                        __photoLocMap.setView([la, lo], 17);
                        if (!window.__mePhotoHasGeoref) setPhotogrammetryPin(la, lo, false, true);
                    }}
                }}).catch(function() {{}});
        }}
        function initPhotogrammetryLocationMap() {{
            var host = document.getElementById('photoTaskLocationMap');
            if (!host || !window.L || __photoLocMap) return;
            var la = parseFloat((document.getElementById('photoApproxLat')?.value || '').replace(',', '.'));
            var lo = parseFloat((document.getElementById('photoApproxLon')?.value || '').replace(',', '.'));
            var hasInit = isFinite(la) && isFinite(lo) && (Math.abs(la) > 1e-5 || Math.abs(lo) > 1e-5);
            __photoLocMap = window.L.map(host, {{
                center: hasInit ? [la, lo] : [30.0, 5.0],
                zoom: hasInit ? 16 : 2
            }});
            var sat = window.L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{ maxZoom: 20, attribution: 'Tiles &copy; Esri' }}).addTo(__photoLocMap);
            var osm = window.L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 20, attribution: '&copy; OSM' }});
            window.L.control.layers({{ 'Satellite': sat, 'Streets': osm }}, null, {{ position: 'bottomleft' }}).addTo(__photoLocMap);
            var bounds = [];
            (window.__meExifGpsPoints || []).forEach(function(pt) {{
                bounds.push([pt[0], pt[1]]);
                window.L.circleMarker([pt[0], pt[1]], {{ radius: 4, color: '#38bdf8', fillColor: '#0284c7', fillOpacity: 0.9, weight: 1 }})
                    .bindTooltip('📷 ' + (pt[2] || 'Frame')).addTo(__photoLocMap);
            }});
            if (bounds.length > 1) {{
                __photoLocMap.fitBounds(bounds, {{ padding: [24, 24], maxZoom: 18 }});
            }} else if (hasInit) {{
                setPhotogrammetryPin(la, lo, false, false);
            }}
            __photoLocMap.on('click', function(ev) {{
                setPhotogrammetryPin(ev.latlng.lat, ev.latlng.lng, false, true);
            }});
        }}
        document.addEventListener('DOMContentLoaded', initPhotogrammetryLocationMap);

        function openPhotogrammetryGcpStudio(pid) {{
            window.open('/GCPStudio.aspx?task_id=' + encodeURIComponent(pid) + '&mode=photogrammetry', 'mosaicearth_gcp_studio', 'width=1420,height=900,resizable=yes,scrollbars=yes');
        }}
        window.addEventListener('message', function(ev) {{
            if (ev.origin !== window.location.origin || !ev.data) return;
            if (ev.data.type === 'mosaicearth:gcp-attached' && String(ev.data.taskId) === String(__project_id)) {{
                toastr.success('Attached ' + ev.data.gcpCount + ' Ground Control Points! Reloading task georeferencing...');
                setTimeout(function() {{ window.location.reload(); }}, 500);
            }}
        }});

        function stageVideoForPhotogrammetry(pid) {{
            var vInp = document.getElementById('inpPhotoVideoFile');
            var tInp = document.getElementById('inpPhotoVideoTelem');
            var btn = document.getElementById('btnStartVideoStage');
            var cancelBtn = document.getElementById('btnCancelVideoStage');
            var wrap = document.getElementById('videoStageProgressWrap');
            var stTxt = document.getElementById('videoStageStatusText');
            var pctTxt = document.getElementById('videoStagePctText');
            var bar = document.getElementById('videoStageBar');
            if (!vInp || !vInp.files || !vInp.files.length) {{
                toastr.warning('Select a video file (.MP4, .MOV, .WEBM, or .M4V) first.');
                return;
            }}
            var fd = new FormData();
            fd.append('video', vInp.files[0]);
            if (tInp && tInp.files && tInp.files.length) {{
                fd.append('telemetry', tInp.files[0]);
            }}
            btn.disabled = true;
            cancelBtn.disabled = true;
            vInp.disabled = true;
            if (tInp) tInp.disabled = true;
            wrap.style.display = 'block';
            stTxt.textContent = 'Uploading video...';
            pctTxt.textContent = '0%';
            bar.style.width = '0%';

            var xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/v2/tasks/' + encodeURIComponent(pid) + '/photogrammetry-video-stage');
            xhr.upload.onprogress = function(ev) {{
                if (!ev.lengthComputable) return;
                var pct = Math.min(95, Math.round((ev.loaded / ev.total) * 85));
                pctTxt.textContent = pct + '%';
                bar.style.width = pct + '%';
                if (ev.loaded >= ev.total) {{
                    stTxt.textContent = 'Extracting sharp overlapping keyframes & injecting EXIF GPS...';
                    pctTxt.textContent = '92%';
                    bar.style.width = '92%';
                }}
            }};
            xhr.onload = function() {{
                var data = {{}};
                try {{ data = JSON.parse(xhr.responseText || '{{}}'); }} catch (_) {{}}
                if (xhr.status >= 200 && xhr.status < 300 && data.ok) {{
                    bar.style.width = '100%';
                    pctTxt.textContent = '100%';
                    stTxt.textContent = 'Extracted ' + data.extracted_frames + ' sharp frames! Updating quote...';
                    toastr.success('Extracted ' + data.extracted_frames + ' sharp frames from video!');
                    setTimeout(function() {{ window.location.reload(); }}, 600);
                }} else {{
                    btn.disabled = false;
                    cancelBtn.disabled = false;
                    vInp.disabled = false;
                    if (tInp) tInp.disabled = false;
                    toastr.error(data.detail || data.error || 'Could not extract frames from video.');
                }}
            }};
            xhr.onerror = function() {{
                btn.disabled = false;
                cancelBtn.disabled = false;
                vInp.disabled = false;
                if (tInp) tInp.disabled = false;
                toastr.error('Network error while uploading video.');
            }};
            xhr.send(fd);
        }}
        function pollOdmLiveLog() {{
            fetch("/api/v2/tasks/" + __project_id + "/live-log")
                .then(function(r) {{ return r.json(); }})
                .then(function(d) {{
                    if (!d || !d.ok) return;
                    var stepEl = document.getElementById("processingStageStep");
                    if (stepEl && d.stage) stepEl.textContent = d.stage;
                    var pctEl = document.getElementById("processingStagePercent");
                    var barEl = document.getElementById("processingBar");
                    if (d.progress && d.progress > 0) {{
                        if (pctEl) pctEl.textContent = d.progress + "%";
                        if (barEl) barEl.style.width = d.progress + "%";
                    }}
                    var elapEl = document.getElementById("odmLiveElapsed");
                    if (elapEl && d.processingTimeMs > 0) {{
                        var totalSec = Math.floor(d.processingTimeMs / 1000);
                        var mins = Math.floor(totalSec / 60);
                        var secs = totalSec % 60;
                        elapEl.textContent = "Active · Elapsed: " + mins + "m " + (secs < 10 ? "0" : "") + secs + "s";
                    }}
                    var preEl = document.getElementById("odmLiveConsolePre");
                    if (preEl && d.lines && d.lines.length) {{
                        preEl.textContent = d.lines.join("\\n");
                        preEl.scrollTop = preEl.scrollHeight;
                    }}
                }})
                .catch(function() {{}});
        }}
        function obtenerPorcentaje() {{
            var data = {{ project_id: __project_id }};
            pollOdmLiveLog();
            $.ajax({{
                type: "POST",
                dataType: "json",
                contentType: "application/json; charset=utf-8",
                url: "/Client/Project.aspx/obtenerProceso",
                data: JSON.stringify(data),
                success: function (response) {{
                    var x = String((response && response.d) || "0/3/0").split("/");
                    var activeProcessing = x[1] == "1" || x[1] == "2" || (x[1] == "4" && x[2] == "0");
                    if (typeof setProcessingWorkspaceVisible === "function") setProcessingWorkspaceVisible(activeProcessing);
                    if (typeof setProcessingStatus === "function") {{
                        if (x[0] == "100") setProcessingStatus("FINALIZING MOSAICASSETS…");
                        else if (x[0] == "0" && x[1] == "1") setProcessingStatus("QUEUED FOR PHOTOGRAMMETRY…");
                        else if (x[0] == "0") setProcessingStatus("PREPARING PROCESSING…");
                        else setProcessingStatus(x[0] + "% PROCESSED");
                    }}
                    if (x[1] == "4" && x[2] == "1") {{
                        clearInterval(intervalID);
                        window.location.href = window.__meTaskCompletedRedirectUrl || "/{norm_lang}/Projects?open_project={root_pid}#taskNode_{pid}";
                        return;
                    }}
                    if (x[1] == "3") {{
                        if (typeof mostrarAlertaError === "function") mostrarAlertaError("ERROR", "Photogrammetry processing needs attention", "BACK");
                        $("#botonRetry").css("display", "inline-block");
                        $("#botonAdd").css("display", "inline-block");
                        $("#botonProcess").css("display", "inline-block");
                        $("#btnCancel").css("display", "none");
                        clearInterval(intervalID);
                        if (typeof setProcessingStatus === "function") setProcessingStatus("PROCESSING NEEDS ATTENTION");
                        $("#imagen-spin").css("display", "none");
                    }}
                }}
            }});
        }}
        function ponerIntervalo() {{
            obtenerPorcentaje();
            clearInterval(intervalID);
            intervalID = setInterval(obtenerPorcentaje, 2000);
        }}
        if ($("#hdnEstado").val() == "1" || $("#hdnEstado").val() == "2") {{
            ponerIntervalo();
        }}
        function showPointcloud() {{
            var ifr = document.getElementById('orthophotoPreview');
            if (ifr && ifr.dataset.src && ifr.src === 'about:blank') {{
                ifr.src = ifr.dataset.src;
            }}
            $('#pointcloudShow').hide();
            $('#pointcloudPreview').show();
        }}
        function toggleAssetPriceInput(ft) {{
            var sel = document.getElementById('assetVis_' + ft);
            var wrap = document.getElementById('assetPriceWrap_' + ft);
            if (sel && wrap) {{
                wrap.style.display = (sel.value === 'marketplace') ? 'block' : 'none';
            }}
        }}
        function saveAssetQuick(ft) {{
            var titleEl = document.getElementById('assetTitle_' + ft);
            var latEl = document.getElementById('assetLat_' + ft);
            var lonEl = document.getElementById('assetLon_' + ft);
            var hEl = document.getElementById('assetHeight_' + ft);
            var visEl = document.getElementById('assetVis_' + ft);
            var priceEl = document.getElementById('assetPrice_' + ft);
            var msgEl = document.getElementById('assetSaveMsg_' + ft);
            if (msgEl) msgEl.textContent = 'Saving...';
            $.ajax({{
                type: 'POST',
                url: '/Client/Project.aspx/saveassetquick',
                data: JSON.stringify({{
                    project_id: __project_id,
                    file_type_id: ft,
                    title: titleEl ? titleEl.value : '',
                    lat: latEl ? latEl.value : '',
                    lon: lonEl ? lonEl.value : '',
                    height: hEl ? hEl.value : '0',
                    visibility_mode: visEl ? visEl.value : 'private',
                    price_eur: priceEl ? priceEl.value : 0
                }}),
                contentType: 'application/json; charset=utf-8',
                dataType: 'json'
            }}).done(function(res) {{
                var d = (typeof res.d === 'string') ? JSON.parse(res.d) : res.d;
                if (d && d.ok) {{
                    if (msgEl) msgEl.textContent = 'Saved! ' + (d.badge || '');
                    var cardTitle = document.getElementById('cardTitleDisplay_' + ft);
                    if (cardTitle && titleEl && titleEl.value) cardTitle.textContent = titleEl.value;
                    var pillId = ft === 2 ? 'orthophotoStatusPill' : (ft === 3 ? 'model3dStatusPill' : 'pointcloudStatusPill');
                    var pill = document.getElementById(pillId);
                    if (pill && d.badge) {{
                        pill.textContent = d.badge;
                        pill.className = 'result-publication-pill ' + (d.isPublic ? 'is-public' : 'is-private');
                    }}
                }} else {{
                    if (msgEl) msgEl.textContent = (d && d.message) ? d.message : 'Error saving changes';
                }}
            }});
        }}
        function createSubprojectNow(parentPid) {{
            var nameEl = document.getElementById('subprojName');
            var kindEl = document.getElementById('subprojKind');
            var sName = nameEl ? nameEl.value.trim() : '';
            if (!sName) {{ alert('Please enter a subproject name.'); return; }}
            $.ajax({{
                type: 'POST',
                url: '/Client/Project.aspx/createsubproject',
                data: JSON.stringify({{
                    parent_project_id: parentPid,
                    name: sName,
                    kind: kindEl ? kindEl.value : 'processing'
                }}),
                contentType: 'application/json; charset=utf-8',
                dataType: 'json'
            }}).done(function(res) {{
                var d = (typeof res.d === 'string') ? JSON.parse(res.d) : res.d;
                if (d && d.ok && d.url) {{
                    window.location.href = d.url;
                }} else {{
                    alert((d && d.message) || 'Could not create subproject.');
                }}
            }});
        }}
        if (COMPLETED === 'true' && URL_CESIUM && typeof MosaicCesiumRuntime !== 'undefined' && document.getElementById('cesiumContainer')) {{
            try {{
                Cesium.Ion.defaultAccessToken = key || '';
                const viewer = MosaicCesiumRuntime.createViewer("cesiumContainer", {{
                    imageryProvider: MosaicCesiumRuntime.createBaseProvider('satellite'),
                    timeline: false,
                    animation: false,
                    fullscreenButton: false,
                    homeButton: false,
                    sceneModePicker: false,
                    baseLayerPicker: false,
                    projectionPicker: false,
                    scene3DOnly: true,
                    geocoder: false,
                    antialias: false
                }}, Cesium.Ion.defaultAccessToken, {{requestVertexNormals:true,requestWaterMask:true}});
                MosaicCesiumRuntime.loadTileset(viewer, URL_CESIUM, {{maximumScreenSpaceError:30,skipLevelOfDetail:true,enableCollision:false}}).then(function(tile){{
                    MosaicCesiumRuntime.applyProcessedTilesetHeight(tile, Number(HEIGHT) || 0);
                    var locateTaskAsset = function() {{ MosaicCesiumRuntime.flyToTopDown(viewer, tile, Math.max(tile.boundingSphere.radius * 2.6, 120), 0.8); }};
                    locateTaskAsset();
                    var cHost = document.getElementById('cesiumContainer');
                    if (cHost && !cHost.querySelector('.mosaic-map-locate')) {{
                        cHost.style.position = 'relative';
                        var lBtn = document.createElement('button');
                        lBtn.type = 'button';
                        lBtn.className = 'processed-locate mosaic-map-locate';
                        lBtn.title = 'Center on Asset';
                        lBtn.style.cssText = 'position:absolute;right:14px;bottom:14px;z-index:20;width:42px;height:42px;border:1px solid #72bd8a;border-radius:50%;background:#183524;color:#dff7e7;display:grid;place-items:center;box-shadow:0 6px 18px rgba(0,0,0,.35);cursor:pointer;';
                        lBtn.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v3M12 19v3M2 12h3M19 12h3"></path></svg>';
                        lBtn.addEventListener('click', locateTaskAsset);
                        cHost.appendChild(lBtn);
                    }}
                }});
            }} catch (e) {{ console.warn(e); }}
        }}
    </script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_collection_page(user: Dict[str, Any], lang: str = "en") -> str:
    norm_lang = normalize_lang(lang)
    store = get_store()
    uid = int(user["ID"])
    col_data = store.get_user_collection_data(uid, lang=norm_lang)
    if isinstance(col_data, tuple):
        imported_assets, collection_items = col_data
    else:
        imported_assets = col_data.get("imported", [])
        collection_items = col_data.get("collection", [])

    seo_head = build_head_metadata_html(
        title="MyAssets · MosaicEarth",
        description="Manage all your imported, generated, published and acquired MosaicAssets.",
        canonical_path=f"/{norm_lang}/Collection",
        robots="noindex, nofollow",
    )

    head_extra = """
    <link rel="stylesheet" href="/assets/css/marketplace.css?v=3" />
    <link rel="stylesheet" href="/assets/css/external-asset-import.css?v=7" />
    <link rel="stylesheet" href="/assets/css/collection-refinements.css?v=3" />"""

    cards_html: List[str] = []

    for item in imported_assets:
        fam_raw = str(item.get("AssetFamilyKey") or item.get("AssetType") or "model3d")
        fam_key = html.escape(fam_raw)
        orig_filter = html.escape(str(item.get("OriginFilter") or ("created" if item.get("IsOwnedByCurrentUser") else "acquired")))
        if orig_filter == "owned":
            orig_filter = "created"
        orig_ext = html.escape(str(item.get("OriginalExtension") or item.get("Extensions") or ""))
        proc_ext = html.escape(str(item.get("ProcessedExtensions") or item.get("Extensions") or ""))
        c_iso = html.escape(str(item.get("CreatedIso") or item.get("FilterDate") or ""))
        cs_iso = html.escape(str(item.get("CaptureStartIso") or item.get("FilterDate") or ""))
        ce_iso = html.escape(str(item.get("CaptureEndIso") or item.get("FilterDate") or ""))
        f_date = html.escape(str(item.get("FilterDate") or cs_iso or c_iso))
        dname = html.escape(str(item.get("DisplayName") or "MosaicAsset"))
        pname = html.escape(str(item.get("CreatorDisplay") or item.get("ProjectName") or ""))
        desc = html.escape(str(item.get("Description") or ""))
        tags = html.escape(str(item.get("Tags") or ""))
        loc_name = html.escape(str(item.get("LocationName") or ""))
        thumb = html.escape(str(item.get("ThumbnailUrl") or "/Resources/mosaicearth-mark-transparent-cropped.png"))
        type_disp = html.escape(str(item.get("TypeDisplay") or "3D Model"))
        fmt = html.escape(str(item.get("Format") or "GLB").upper())
        cap_disp = html.escape(str(item.get("DateDisplay") or item.get("CaptureDisplay") or ""))
        detail_href = html.escape(str(item.get("DetailHref") or f"/Client/ExternalAssetDetail.aspx?project_id={int(item.get('ProjectId') or 0)}&asset_id={int(item.get('Id') or 0)}"))
        type_icon = ICON_CLOUD if fam_raw == "pointcloud" else (ICON_3D if fam_raw in ("model3d", "gaussian") else ICON_ORTHOPHOTO)
        src_orig = str(item.get("SourceOrigin") or "imported").lower()
        orig_cls = "is-processed" if src_orig == "processed" else "is-imported"
        orig_lbl = "Processed" if src_orig == "processed" else "Imported"

        cards_html.append(
            f"""
            <div class="col-xl-3 col-lg-4 col-md-6 col-12 mb-4 d-flex asset-filter-item"
                 data-ownership="{orig_filter}"
                 data-source="{html.escape(src_orig)}"
                 data-type="{fam_key}"
                 data-date="{f_date}"
                 data-created="{c_iso}"
                 data-capture-start="{cs_iso}"
                 data-capture-end="{ce_iso}"
                 data-original-extension="{orig_ext}"
                 data-processed-extensions="{proc_ext}"
                 data-extensions="{orig_ext} {proc_ext}"
                 data-search="{dname} {pname} {type_disp} {fmt} {orig_ext} {proc_ext} {desc} {tags} {loc_name}">
                <a class="mosaic-asset-card mosaic-card-v2" href="{detail_href}">
                    <div class="mosaic-card-preview">
                        <span class="mosaic-preview-fallback" aria-hidden="true">Preview unavailable</span>
                        <img class="mosaic-preview-image" src="{thumb}" alt="" loading="lazy" decoding="async" onerror="this.hidden=true" />
                        <span class="mosaic-card-type-icon" title="{type_disp}" aria-label="{type_disp}">{type_icon}</span>
                        <span class="mosaic-card-origin {orig_cls}">{orig_lbl}</span>
                        <span class="preview-provenance">Location preview</span>
                        <span class="mosaic-card-details">View details</span>
                    </div>
                    <div class="mosaic-card-body">
                        <h3 class="mosaic-card-title" title="{dname}">{dname}</h3>
                        <p class="mosaic-card-creator">{pname}</p>
                        <p class="mosaic-card-date"><span>CAPTURE</span><strong>{cap_disp}</strong></p>
                        <div class="mosaic-card-footer">
                            <span class="mosaic-card-download"><span>Download</span><strong>{fmt}</strong></span>
                            <span class="asset-ownership">{orig_filter}</span>
                        </div>
                    </div>
                </a>
            </div>"""
        )

    for item in collection_items:
        ft = int(item.get("FILE_TYPE_ID") or 2)
        fam_default = "pointcloud" if ft == 1 else "model3d" if ft == 3 else "orthophoto"
        fam_raw = str(item.get("FAMILY_KEY") or item.get("FamilyKey") or fam_default)
        fam_key = html.escape(fam_raw)
        orig_key = html.escape(str(item.get("OwnershipKind") or item.get("ORIGIN_KEY") or "created"))
        if orig_key in ("processed", "owned"):
            orig_key = "created"
        src_key = html.escape(str(item.get("SourceKind") or "processed"))
        orig_ext = html.escape(str(item.get("ORIGINAL_EXTENSION") or item.get("OriginalExtension") or item.get("Extensions") or ""))
        proc_ext = html.escape(str(item.get("PROCESSED_EXTENSIONS") or item.get("ProcessedExtensions") or item.get("Extensions") or ""))
        c_iso = html.escape(str(item.get("CREATED_ISO") or item.get("CreatedIso") or item.get("FilterDate") or ""))
        cs_iso = html.escape(str(item.get("CAPTURE_START_ISO") or item.get("CaptureStartIso") or item.get("FilterDate") or ""))
        ce_iso = html.escape(str(item.get("CAPTURE_END_ISO") or item.get("CaptureEndIso") or item.get("FilterDate") or ""))
        f_date = html.escape(str(item.get("FilterDate") or cs_iso or c_iso))
        name = html.escape(str(item.get("NAME") or "MosaicAsset"))
        desc = html.escape(str(item.get("DESCRIPTION") or ""))
        creator_disp = html.escape(str(item.get("CreatorDisplay") or item.get("CATEGORY_NAME") or "MosaicEarth"))
        tags = html.escape(str(item.get("TAGS") or ""))
        img_url = html.escape(str(item.get("CardPreviewUrl") or item.get("IMAGE_URL") or "/Resources/mosaicearth-mark-transparent-cropped.png"))
        type_disp = html.escape(str(item.get("FamilyDisplay") or item.get("TYPE_DISPLAY") or "Orthophoto"))
        dl_fmt = html.escape(str(item.get("DownloadFormat") or item.get("DOWNLOAD_FORMAT") or "TIF"))
        cap_disp = html.escape(str(item.get("DateDisplay") or item.get("CAPTURE_DISPLAY") or ""))
        access_label = html.escape(str(item.get("AccessBadge") or item.get("ACCESS_LABEL") or "Private"))
        detail_url = html.escape(str(item.get("DetailHref") or item.get("DETAIL_URL") or "#"))
        type_icon = ICON_CLOUD if ft == 1 else (ICON_3D if ft == 3 else ICON_ORTHOPHOTO)

        cards_html.append(
            f"""
            <div class="col-xl-3 col-lg-4 col-md-6 col-12 mb-4 d-flex asset-filter-item"
                 data-ownership="{orig_key}"
                 data-source="{src_key}"
                 data-type="{fam_key}"
                 data-date="{f_date}"
                 data-created="{c_iso}"
                 data-capture-start="{cs_iso}"
                 data-capture-end="{ce_iso}"
                 data-original-extension="{orig_ext}"
                 data-processed-extensions="{proc_ext}"
                 data-extensions="{orig_ext} {proc_ext}"
                 data-search="{name} {creator_disp} {type_disp} {dl_fmt} {orig_ext} {proc_ext} {desc} {tags}">
                <a class="mosaic-asset-card mosaic-card-v2" href="{detail_url}">
                    <div class="mosaic-card-preview">
                        <span class="mosaic-preview-fallback" aria-hidden="true">Preview unavailable</span>
                        <img class="mosaic-preview-image" src="{img_url}" alt="" loading="lazy" decoding="async" onerror="this.hidden=true" />
                        <span class="mosaic-card-type-icon" title="{type_disp}" aria-label="{type_disp}">{type_icon}</span>
                        <span class="mosaic-card-origin is-processed">Processed &middot; {access_label}</span>
                        <span class="mosaic-card-details">View details</span>
                    </div>
                    <div class="mosaic-card-body">
                        <h3 class="mosaic-card-title" title="{name}">{name}</h3>
                        <p class="mosaic-card-creator">{creator_disp}</p>
                        <p class="mosaic-card-date"><span>CAPTURE</span><strong>{cap_disp}</strong></p>
                        <div class="mosaic-card-footer">
                            <span class="mosaic-card-download"><span>Download</span><strong>{dl_fmt}</strong></span>
                            <span class="asset-ownership">{orig_key}</span>
                        </div>
                    </div>
                </a>
            </div>"""
        )

    body_content = f"""
    <section id="assets" class="workspace-section">
        <div class="container">
            <div class="row" style="margin-bottom: 15px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="row">
                                <div class="col-md-8">
                                    MyAssets
                                </div>
                                <div class="col-md-4">
                                    <div class="input-group mb-3 input-group-4d">
                                        <input id="txtSearch" type="text" class="form-control" placeholder="Search" />
                                        <div class="input-group-append">
                                            <span class="input-group-text"><i class="fas fa-search"></i></span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <p class="page-purpose"><strong>Your MosaicAsset library.</strong> Find every map or layer, 3D model and point cloud you created, imported, acquired for free or purchased.</p>
                        <div class="asset-filter-bar" role="group" aria-label="Filter MyAssets">
                            <button type="button" class="active filter-all" data-filter-action="all" aria-pressed="true">All</button>
                            <span class="filter-group-label">Ownership</span><button type="button" data-filter-group="ownership" data-filter-value="created" aria-pressed="false">Created</button><button type="button" data-filter-group="ownership" data-filter-value="acquired" aria-pressed="false">Acquired</button><button type="button" data-filter-group="ownership" data-filter-value="purchased" aria-pressed="false">Purchased</button>
                            <span class="filter-divider" aria-hidden="true"></span><span class="filter-group-label">Source</span><button type="button" data-filter-group="source" data-filter-value="imported" aria-pressed="false">Imported</button><button type="button" data-filter-group="source" data-filter-value="processed" aria-pressed="false">Processed</button>
                            <span class="filter-divider" aria-hidden="true"></span><span class="filter-group-label">Family</span><button type="button" data-filter-group="type" data-filter-value="orthophoto" aria-pressed="false">Maps &amp; Layers</button><button type="button" data-filter-group="type" data-filter-value="model3d" aria-pressed="false">3D Models</button><button type="button" data-filter-group="type" data-filter-value="pointcloud" aria-pressed="false">Point Clouds</button>
                            <div class="mosaic-extension-filter"><span>File extensions</span><select multiple aria-label="File extensions" id="assetExtension"><option value="">All extensions</option></select></div>
                            <span class="filter-date-range"><label>From <input type="date" id="assetDateFrom" /></label><label>To <input type="date" id="assetDateTo" /></label></span><span class="filter-result-count" id="assetFilterCount" aria-live="polite"></span><button type="button" class="filter-reset" id="assetFilterReset">Reset</button>
                        </div>
                        <div class="content-box-equal-padding content-collection">
                            <div id="assetEmptyState" class="workspace-empty-state" role="status" hidden>
                                <strong id="assetEmptyTitle">No assets match your filters</strong>
                                <p id="assetEmptyMessage">Try another name or clear the filters.</p>
                                <button type="button" class="btn btn-secondary" id="assetEmptyReset">Clear filters</button>
                                <a class="btn btn-secondary" href="/{norm_lang}/Map">Explore the map</a>
                                <a class="btn btn-primary" href="/{norm_lang}/Projects">Create or import an asset</a>
                            </div>
                            <div class="row">
                                {''.join(cards_html)}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </section>"""

    scripts_extra = """
    <script src="/js/front/Collection.js?v=20260923-map-r28"></script>
    <script>
        CargarActiveMenu("li_collection");
    </script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_mymaps_page(user: Dict[str, Any], lang: str = "en") -> str:
    norm_lang = normalize_lang(lang)
    store = get_store()
    uid = int(user["ID"])
    is_desktop_local = os.environ.get("MOSAICEARTH_DESKTOP_MODE", "") == "1" or os.environ.get("MOSAICEARTH_DESKTOP_LOCAL", "") == "1"
    cloud_acc = store.get_desktop_cloud_account() if is_desktop_local else {}
    cloud_acc_logged = bool(cloud_acc.get("logged_in"))
    cloud_acc_email = str(cloud_acc.get("email") or "")
    cloud_origin_url = str(cloud_acc.get("cloud_url") or os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", "https://app.mosaicearth.app")).rstrip("/")
    mymaps = store.list_user_mymaps(uid)
    storage = store.get_user_storage_summary(uid)
    plan_code = str(storage.get("plan_code") or "free").lower()
    is_subscriber = plan_code in ("pro", "studio", "enterprise")

    seo_head = build_head_metadata_html(
        title="MyMosaics · MosaicEarth",
        description="Combine multiple MosaicAssets from your projects or collection into one shared map scene.",
        canonical_path=f"/{norm_lang}/MyMaps",
        robots="noindex, nofollow",
    )

    head_extra = """
    <link rel="stylesheet" href="/assets/css/Projects.css?v=7" />
    <link rel="stylesheet" href="/assets/css/MyMaps.css?v=4" />
    <style>
        .mymosaic-row-node {
            background: rgba(15, 19, 17, 0.94);
            border: 1px solid rgba(255, 255, 255, 0.10);
            border-radius: 12px;
            padding: 15px 18px;
            margin-bottom: 12px;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            transition: border-color 0.16s ease, background 0.16s ease;
        }
        .mymosaic-row-node:hover {
            border-color: rgba(153, 204, 0, 0.45);
            background: rgba(20, 26, 22, 0.98);
        }
        .mymosaic-icon-box {
            width: 38px;
            height: 38px;
            border-radius: 9px;
            background: rgba(153, 204, 0, 0.12);
            border: 1px solid rgba(153, 204, 0, 0.35);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: #99CC00;
            flex-shrink: 0;
        }
        .mymosaic-badge-kind {
            font-size: 9.5px;
            font-family: OpenSansBold, Arial, sans-serif;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            padding: 2px 7px;
            border-radius: 5px;
            background: rgba(153, 204, 0, 0.15);
            color: #99CC00;
            border: 1px solid rgba(153, 204, 0, 0.35);
        }
        .mymosaic-title-link {
            font-size: 15.5px;
            font-family: OpenSansBold, Arial, sans-serif;
            color: #ffffff;
            text-decoration: none;
            word-break: break-word;
        }
        .mymosaic-title-link:hover {
            color: #99CC00;
        }
        .mymosaic-meta-chip {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            font-size: 11px;
            color: #b7c4bc;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.09);
            border-radius: 6px;
            padding: 2px 8px;
        }
        .mymosaic-btn {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 11.5px;
            font-family: OpenSansSemiBold, Arial, sans-serif;
            padding: 6px 12px;
            border-radius: 7px;
            border: 1px solid rgba(255, 255, 255, 0.14);
            background: rgba(255, 255, 255, 0.05);
            color: #e4ece7;
            cursor: pointer;
            text-decoration: none !important;
            transition: all 0.15s ease;
        }
        .mymosaic-btn svg {
            width: 14px;
            height: 14px;
            flex-shrink: 0;
        }
        .mymosaic-btn:hover {
            background: rgba(153, 204, 0, 0.14);
            border-color: rgba(153, 204, 0, 0.55);
            color: #ffffff;
        }
        .mymosaic-btn--primary {
            background: #99CC00;
            border-color: #99CC00;
            color: #070b09 !important;
            font-family: OpenSansBold, Arial, sans-serif;
        }
        .mymosaic-btn--primary:hover {
            background: #a8df00;
            border-color: #a8df00;
            color: #070b09 !important;
        }
        .mymosaic-btn--accent {
            background: rgba(153, 204, 0, 0.13);
            border-color: rgba(153, 204, 0, 0.45);
            color: #c8f064;
        }
        .mymosaic-btn--danger:hover {
            background: rgba(239, 68, 68, 0.16);
            border-color: rgba(239, 68, 68, 0.55);
            color: #fca5a5;
        }
        .mymosaic-toolbar {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 18px;
            padding: 12px 16px;
            background: rgba(14, 18, 16, 0.85);
            border: 1px solid rgba(255, 255, 255, 0.09);
            border-radius: 10px;
        }
    </style>"""

    is_desktop_mymaps = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
    rows_html = []
    for m in mymaps:
        mid = int(m.get("ID") or 0)
        mname_raw = str(m.get("NAME") or f"MyMosaic #{mid}")
        mname = html.escape(mname_raw)
        c_iso = html.escape(str(m.get("CreateDateIso") or m.get("DATE") or ""))
        c_disp = html.escape(str(m.get("CreateDateDisplay") or m.get("DATE") or ""))
        pub_cnt = int(m.get("PublicationCount") if m.get("PublicationCount") is not None else (m.get("NUM_FILES") or 0))
        exts = html.escape(str(m.get("Extensions") or ""))
        vis_val = str(m.get("VISIBILITY") or m.get("Visibility") or "public").strip().lower()
        if vis_val not in ("private", "unlisted", "public"):
            vis_val = "public"
        if vis_val == "private":
            vis_chip_html = f'<span id="visBadge_{mid}" class="mymosaic-meta-chip" style="border-color:#45564b;color:#acbbb0;"><i class="fas fa-lock" style="color:#9ca3af;"></i> PRIVATE &middot; OWNER ONLY</span>'
        elif vis_val == "unlisted":
            vis_chip_html = f'<span id="visBadge_{mid}" class="mymosaic-meta-chip" style="border-color:#5a7a22;color:#d9f99d;"><i class="fas fa-link" style="color:#99CC00;"></i> UNLISTED &middot; SECRET CLIENT LINK</span>'
        else:
            vis_chip_html = f'<span id="visBadge_{mid}" class="mymosaic-meta-chip" style="border-color:#99CC00;color:#99CC00;"><i class="fas fa-globe-europe" style="color:#99CC00;"></i> PUBLIC &middot; IN PUBLIC MOSAICS</span>'

        exts_chip = (
            f'<span class="mymosaic-meta-chip"><i class="fas fa-file-code" style="color:#99CC00;"></i> {exts.upper()}</span>'
            if exts
            else ""
        )
        local_pc_badge = (
            '<span class="mymosaic-badge-kind" style="background:rgba(153,204,0,0.22);border-color:#99CC00;color:#99CC00;">LOCAL PC</span>'
            if is_desktop_mymaps
            else ""
        )
        sync_mosaic_btn = (
            f'<button type="button" class="mymosaic-btn mymosaic-btn--primary" data-name="{html.escape(mname_raw, quote=True)}" onclick="syncLocalMyMosaicToCloud({mid}, this.dataset.name)" title="Upload &amp; sync this Local MyMosaic to your MosaicEarth Cloud account"><i class="fas fa-cloud-upload-alt" style="font-size:11px;"></i> Pass to Cloud</button>'
            if is_desktop_mymaps
            else ""
        )

        rows_html.append(
            f"""
            <div class="mymosaic-row-node mosaic-list-item" data-name="{html.escape(mname_raw.lower(), quote=True)}" data-extensions="{exts}" data-date="{c_iso}">
                <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:260px;">
                    <span class="mymosaic-icon-box" title="MyMosaic">{MYEARTH_ICON_GLOBE}</span>
                    <div style="min-width:0;flex:1;">
                        <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;">
                            <span class="mymosaic-badge-kind">MYMOSAIC</span>
                            {local_pc_badge}
                            <a href="/MyMap.aspx?id={mid}" class="mymosaic-title-link projectTitle">{mname}</a>
                            {vis_chip_html}
                        </div>
                        <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:6px;">
                            <span class="mymosaic-meta-chip"><i class="far fa-calendar-alt" style="color:#99CC00;"></i> Created: <strong style="color:#fff;">{c_disp}</strong></span>
                            <span class="mymosaic-meta-chip">{MYEARTH_ICON_PROJECT} <strong style="color:#99CC00;">{pub_cnt}</strong> Assets / Layers</span>
                            {exts_chip}
                        </div>
                    </div>
                </div>
                <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;">
                    <select aria-label="MyMosaic Visibility" onchange="updateMyMosaicVisibility({mid}, this.value)" style="background:#121915;border:1px solid #45564b;color:#dce6dc;border-radius:5px;padding:5px 8px;font-size:11.5px;font-family:OpenSansSemiBold,Arial,sans-serif;cursor:pointer;">
                        <option value="private"{' selected="selected"' if vis_val == 'private' else ''}>Private (Only Me)</option>
                        <option value="unlisted"{' selected="selected"' if vis_val == 'unlisted' else ''}>Unlisted (Secret Link)</option>
                        <option value="public"{' selected="selected"' if vis_val == 'public' else ''}>Public (In Public Mosaics)</option>
                    </select>
                    <a href="/MyMap.aspx?id={mid}" class="mymosaic-btn mymosaic-btn--accent" title="Open this MyMosaic on the 3D Map">
                        {MYEARTH_ICON_GLOBE} Open in 3D Map
                    </a>
                    {sync_mosaic_btn}
                    <a href="/Client/MyMapDetail.aspx?id={mid}" class="mymosaic-btn" title="Edit layers and title of this MyMosaic">
                        <i class="fas fa-pen" style="font-size:11px;"></i> Edit Mosaic
                    </a>
                    <button type="button" class="mymosaic-btn" data-name="{html.escape(mname_raw, quote=True)}" onclick="copyMyMosaicShareLink({mid}, this.dataset.name)" title="Copy a link that opens this MyMosaic on this computer only">
                        <i class="fas fa-link" style="font-size:11px;"></i> Local link
                    </button>
                    <button type="button" class="mymosaic-btn mymosaic-btn--danger" title="Delete this MyMosaic" onclick="changeDeleteBtn({mid})" data-bs-toggle="modal" data-bs-target="#staticBackdrop1">
                        <i class="fas fa-trash" style="font-size:11px;"></i>
                    </button>
                </div>
            </div>"""
        )

    empty_mosaics_html = ""
    if not rows_html:
        empty_mosaics_html = """
        <div style="padding:36px 20px;text-align:center;color:#9ca3af;border:1px dashed rgba(255,255,255,0.14);border-radius:12px;">
            <div style="font-size:15px;color:#fff;font-family:OpenSansBold,Arial,sans-serif;margin-bottom:6px;">No MyMosaics created yet</div>
            <p style="font-size:13px;margin-bottom:14px;">Combine multiple assets from your MyEarth Projects &amp; Surveys or from the public globe into a curated 3D presentation.</p>
            <a href="/Client/MyMapDetail.aspx" class="mymosaic-btn mymosaic-btn--primary">+ NEW MYMOSAIC</a>
        </div>"""

    body_content = f"""
    <section id="MyMaps" class="workspace-section">
        <div class="container">
            <div class="row" style="margin-bottom: 15px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
                                <div>MyMosaics &middot; Curated 3D Map Compositions</div>
                                <div class="d-flex flex-wrap gap-2">
                                    <a href="/Client/MyMapDetail.aspx" class="mymosaic-btn mymosaic-btn--primary" style="padding:7px 16px;font-size:12px;">+ NEW MYMOSAIC</a>
                                </div>
                            </div>
                        </div>
                        <div class="content-box" style="padding: 20px 24px;">
                            <div style="background:rgba(153,204,0,0.06);border:1px solid rgba(153,204,0,0.24);border-radius:10px;padding:12px 16px;margin-bottom:16px;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;">
                                <div style="font-size:12.5px;color:#d3ded7;">
                                    <strong style="color:#99CC00;">Your local geographic mosaics.</strong> Combine layers from MyEarth and the public map into 3D scenes. Local layers and compositions stay on this computer. Synchronize a complete private copy to your cloud account to share or publish it online. Public layers require an Internet connection.
                                </div>
                                <span id="mosaicListCount" style="font-size:12px;font-family:OpenSansBold,Arial,sans-serif;color:#99CC00;">{len(mymaps)} MyMosaics</span>
                            </div>

                            <div class="mymosaic-toolbar">
                                <div style="position:relative;flex:1;min-width:240px;max-width:420px;">
                                    <i class="fas fa-search" style="position:absolute;left:12px;top:50%;transform:translateY(-50%);color:#8fa099;font-size:12px;"></i>
                                    <input type="search" id="mosaicListSearch" class="form-control" style="padding-left:34px;background:#121714;border:1px solid rgba(255,255,255,0.14);color:#fff;font-size:13px;height:36px;border-radius:8px;" placeholder="Search MyMosaics by name..." oninput="filterMyMosaicsRows()" />
                                </div>
                                <div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px;">
                                    <label style="display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#b7c4bc;margin:0;">
                                        Sort:
                                        <select id="mosaicListSort" class="form-control" style="width:auto;background:#121714;border:1px solid rgba(255,255,255,0.14);color:#fff;font-size:12px;height:34px;padding:4px 10px;border-radius:7px;" onchange="filterMyMosaicsRows()">
                                            <option value="newest">Newest first</option>
                                            <option value="oldest">Oldest first</option>
                                            <option value="name">Name A–Z</option>
                                        </select>
                                    </label>
                                </div>
                            </div>

                            <p id="mosaicListEmpty" class="workspace-empty-state" role="status" hidden>No MyMosaics match your search filter.</p>

                            <div id="myMosaicsListContainer">
                                {''.join(rows_html) if rows_html else empty_mosaics_html}
                            </div>
                            {f'''
                            <div style="margin-top:24px;padding-top:18px;border-top:1px solid rgba(255,255,255,0.10);">
                                <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:12px;">
                                    <div>
                                        <span style="display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:999px;background:rgba(56,189,248,0.14);border:1px solid rgba(56,189,248,0.45);color:#38bdf8;font-size:10.5px;font-weight:700;margin-right:8px;"><i class="fas fa-cloud"></i> CLOUD ACCOUNT &middot; MyMosaics</span>
                                        <strong style="color:#fff;font-size:14px;">Cloud MyMosaics ({html.escape(cloud_acc_email) if cloud_acc_logged else "Not Signed In"})</strong>
                                    </div>
                                    <a href="/{norm_lang}/Projects" class="mymosaic-btn" style="font-size:11px;padding:4px 12px;"><i class="fas fa-user-check"></i> Manage Cloud Sign-In in MyEarth</a>
                                </div>
                                <div id="cloudMyMosaicsList" style="font-size:12.5px;color:#b8c6be;">
                                    {("Loading your Cloud MyMosaics from app.mosaicearth.app..." if cloud_acc_logged else "Sign in to your MosaicEarth Cloud Account in MyEarth to view and manage your Cloud MyMosaics alongside your Local PC MyMosaics.")}
                                </div>
                            </div>
                            ''' if is_desktop_local else ''}
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="modal fade" id="staticBackdrop1" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-labelledby="staticBackdropLabel1" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="staticBackdropLabel1">Delete MyMosaic</h5>
                    </div>
                    <div class="modal-body texto-delete text-center">
                        Are you sure you want to delete this MyMosaic composition? Your underlying assets in MyEarth will remain untouched. This action cannot be undone.
                    </div>
                    <div class="modal-footer">
                        <div class="row w-100">
                            <div class="col-6">
                                <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                            </div>
                            <div class="col-6 text-end">
                                <button id="btnDeleteProject" onclick="" type="button" class="btn btn-primary" style="font-size: 12px!important; padding: 10px 34px 10px 34px;">CONFIRM</button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="modal fade" id="modalSubRequiredMyMosaics" tabindex="-1" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content" style="background:#17211b;border:1px solid #3c4b40;color:#fff;">
                    <div class="modal-header" style="border-bottom:1px solid rgba(255,255,255,0.1);">
                        <h5 class="modal-title" style="color:#99CC00;font-family:OpenSansBold,Arial,sans-serif;"><i class="fas fa-crown me-2"></i>Subscriber-Only Feature</h5>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body" style="padding:20px;">
                        <div style="background:rgba(153,204,0,0.08);border:1px solid rgba(153,204,0,0.35);border-radius:8px;padding:16px;text-align:center;">
                            <div style="font-size:15px;font-family:OpenSansBold,Arial,sans-serif;color:#99CC00;margin-bottom:8px;">
                                This feature is exclusively available for subscribers
                            </div>
                            <p style="font-size:12.5px;color:#dce6dc;line-height:1.55;margin-bottom:14px;">
                                Sharing Cloud MyMosaics via secret client links (<strong>Unlisted</strong>) or publishing them to the <strong>Public Mosaics</strong> directory requires an active <strong>Pro (19 &euro;/month)</strong> or <strong>Studio (59 &euro;/month)</strong> subscription.
                            </p>
                            <a href="/{norm_lang}/CreditPacks" class="btn btn-primary" style="background:#99CC00;color:#0b110d;border:none;font-family:OpenSansBold,Arial,sans-serif;font-size:12px;padding:9px 22px;border-radius:6px;text-decoration:none;">
                                VIEW SUBSCRIPTION PLANS &rarr;
                            </a>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </section>
    <input type="hidden" id="hdnTranslations" value="{{}}" />"""

    scripts_extra = f"""
    <script src="/js/front/MyMaps.js?v=20260923-map-r28"></script>
    <script>
        var IS_MYMOSAIC_SUBSCRIBER = {'true' if (is_subscriber or is_desktop_local) else 'false'};
        var IS_DESKTOP_LOCAL_MYMAPS = {'true' if is_desktop_local else 'false'};
        var CLOUD_ACC_LOGGED_MYMAPS = {'true' if cloud_acc_logged else 'false'};
        var CLOUD_ORIGIN_URL_MYMAPS = '{cloud_origin_url}';
        MYLIBRARY.setUrl_page('/Client/MyMaps.aspx');
        CargarActiveMenu("li_mymaps");
        function showMyMosaicSubModal() {{
            var mEl = document.getElementById('modalSubRequiredMyMosaics');
            if (mEl && window.bootstrap && bootstrap.Modal) {{
                bootstrap.Modal.getOrCreateInstance(mEl).show();
            }} else {{
                alert('This feature is only available for subscribers (Pro or Studio Plan).');
            }}
        }}
        function copyMyMosaicShareLink(mid, mname) {{
            if (!IS_MYMOSAIC_SUBSCRIBER && !IS_DESKTOP_LOCAL_MYMAPS) {{
                showMyMosaicSubModal();
                return;
            }}
            if (IS_DESKTOP_LOCAL_MYMAPS) {{
                fetch('/api/desktop/share-origin').then(function(r){{ return r.json(); }}).then(function(d){{
                    var baseOrig = (d && d.shareOrigin) ? d.shareOrigin : window.location.origin;
                    var shareUrl = baseOrig + '/MyMap.aspx?id=' + mid;
                    window.prompt('Local link — opens on this computer only. Synchronize to Cloud to share with other people:', shareUrl);
                }}).catch(function(){{
                    var shareUrl = window.location.origin + '/MyMap.aspx?id=' + mid;
                    window.prompt('Local PC Share Link:', shareUrl);
                }});
                return;
            }}
            var shareUrl = window.location.origin + '/MyMap.aspx?id=' + mid;
            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(shareUrl).then(function() {{
                    if (typeof toastr !== 'undefined') toastr.success('Share link copied for "' + mname + '"!');
                }});
            }} else {{
                window.prompt('Copy shareable MyMosaic link:', shareUrl);
            }}
        }}
        function syncLocalMyMosaicToCloud(mid, mname) {{
            if (!CLOUD_ACC_LOGGED_MYMAPS) {{
                alert('Please sign in to your MosaicEarth Cloud Account in MyEarth first so we know which account to push "' + mname + '" to.');
                window.location.href = '/{norm_lang}/Projects';
                return;
            }}
            if (!confirm('Pass local MyMosaic "' + mname + '" to your MosaicEarth Cloud account?')) return;
            fetch('/api/desktop/sync-to-cloud', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{type: 'mymosaic', id: mid}})
            }}).then(function(r){{ return r.json(); }}).then(function(d){{
                if (d && d.ok) {{
                    alert('MyMosaic "' + mname + '" has been pushed to your MosaicEarth Cloud account!');
                    loadCloudMyMosaicsList();
                }} else {{
                    alert((d && d.message) || 'Could not sync MyMosaic to Cloud.');
                }}
            }});
        }}
        function loadCloudMyMosaicsList() {{
            if (!IS_DESKTOP_LOCAL_MYMAPS || !CLOUD_ACC_LOGGED_MYMAPS) return;
            var box = document.getElementById('cloudMyMosaicsList');
            if (!box) return;
            fetch('/api/desktop/cloud-workspace').then(function(r){{ return r.json(); }}).then(function(d){{
                if (!d || !d.ok) {{
                    box.innerHTML = '<div style="color:#fca5a5;">' + ((d && d.message) || 'Could not load Cloud MyMosaics.') + '</div>';
                    return;
                }}
                var mlist = d.mymosaics || [];
                var isSub = !!(d.account && (d.account.plan === 'pro' || d.account.plan === 'studio' || d.account.plan === 'enterprise'));
                if (!mlist.length) {{
                    box.innerHTML = '<div style="padding:14px;background:#101712;border:1px dashed rgba(255,255,255,0.12);border-radius:8px;color:#9ca3af;">No Cloud MyMosaics found in your account yet. Use <strong>Pass to Cloud</strong> on any Local MyMosaic above to upload it.</div>';
                    return;
                }}
                var html = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;">';
                mlist.forEach(function(m){{
                    var shareUrlStr = CLOUD_ORIGIN_URL_MYMAPS + '/MyMap.aspx?id=' + m.id;
                    html += '<div style="background:#101712;border:1px solid rgba(56,189,248,0.28);border-radius:10px;padding:14px;">'
                        + '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px;">'
                        + '<strong style="color:#fff;font-size:14px;">' + (m.name || 'Cloud MyMosaic') + '</strong>'
                        + '<span style="font-size:10px;padding:2px 7px;border-radius:999px;background:rgba(56,189,248,0.14);color:#38bdf8;border:1px solid rgba(56,189,248,0.4);font-weight:700;">CLOUD</span>'
                        + '</div>'
                        + '<div style="font-size:11.5px;color:#9ca3af;margin-bottom:10px;">Assets: ' + (m.asset_count || 0) + ' &middot; Visibility: ' + (m.visibility || 'public').toUpperCase() + '</div>'
                        + '<div style="display:flex;gap:8px;flex-wrap:wrap;">'
                        + '<a href="' + shareUrlStr + '" target="_blank" class="mymosaic-btn mymosaic-btn--primary" style="font-size:11px;padding:4px 10px;">Open Cloud 3D View &nearr;</a>'
                        + '<button type="button" data-url="' + shareUrlStr + '" onclick="shareCloudMyMosaicLink(this, ' + (isSub ? 'true' : 'false') + ')" class="mymosaic-btn" style="font-size:11px;padding:4px 10px;"><i class="fas fa-share-alt"></i> Share Cloud Link</button>'
                        + '</div></div>';
                }});
                html += '</div>';
                box.innerHTML = html;
            }}).catch(function(){{}});
        }}
        function shareCloudMyMosaicLink(btnEl, isSub) {{
            if (!isSub) {{
                showMyMosaicSubModal();
                return;
            }}
            var url = btnEl ? btnEl.getAttribute('data-url') : '';
            window.prompt('Cloud MyMosaic Share Link:', url);
        }}
        if (IS_DESKTOP_LOCAL_MYMAPS && CLOUD_ACC_LOGGED_MYMAPS) {{
            // The shared Desktop sync controller loads this list safely.
        }}
        function updateMyMosaicVisibility(mid, visibility) {{
            if (!IS_MYMOSAIC_SUBSCRIBER && visibility !== 'private') {{
                showMyMosaicSubModal();
                return;
            }}
            fetch('/api/v2/mymosaics/visibility', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{mapId: mid, visibility: visibility}})
            }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
                if (data && data.ok) {{
                    if (typeof toastr !== 'undefined') toastr.success(data.message || 'Visibility updated.');
                    var badge = document.getElementById('visBadge_' + mid);
                    if (badge) {{
                        if (visibility === 'private') {{
                            badge.style.borderColor = '#45564b'; badge.style.color = '#acbbb0';
                            badge.innerHTML = '<i class="fas fa-lock" style="color:#9ca3af;"></i> PRIVATE &middot; OWNER ONLY';
                        }} else if (visibility === 'unlisted') {{
                            badge.style.borderColor = '#5a7a22'; badge.style.color = '#d9f99d';
                            badge.innerHTML = '<i class="fas fa-link" style="color:#99CC00;"></i> UNLISTED &middot; SECRET CLIENT LINK';
                        }} else {{
                            badge.style.borderColor = '#99CC00'; badge.style.color = '#99CC00';
                            badge.innerHTML = '<i class="fas fa-globe-europe" style="color:#99CC00;"></i> PUBLIC &middot; IN PUBLIC MOSAICS';
                        }}
                    }}
                }} else if (data && data.subscription_required) {{
                    showMyMosaicSubModal();
                }} else {{
                    alert((data && data.message) || 'Could not update MyMosaic visibility.');
                }}
            }});
        }}
        function filterMyMosaicsRows() {{
            var q = (document.getElementById('mosaicListSearch').value || '').toLowerCase().trim();
            var sortMode = document.getElementById('mosaicListSort').value || 'newest';
            var container = document.getElementById('myMosaicsListContainer');
            if (!container) return;
            var rows = Array.prototype.slice.call(container.querySelectorAll('.mymosaic-row-node'));
            var visibleCount = 0;
            rows.forEach(function(r) {{
                var name = (r.getAttribute('data-name') || '').toLowerCase();
                var show = !q || name.indexOf(q) !== -1;
                r.style.display = show ? 'flex' : 'none';
                if (show) visibleCount++;
            }});
            rows.sort(function(a, b) {{
                if (sortMode === 'name') {{
                    return (a.getAttribute('data-name') || '').localeCompare(b.getAttribute('data-name') || '');
                }}
                var da = a.getAttribute('data-date') || '';
                var db = b.getAttribute('data-date') || '';
                return sortMode === 'oldest' ? da.localeCompare(db) : db.localeCompare(da);
            }});
            rows.forEach(function(r) {{ container.appendChild(r); }});
            var emptyEl = document.getElementById('mosaicListEmpty');
            if (emptyEl) emptyEl.hidden = (visibleCount > 0 || rows.length === 0);
            var cntEl = document.getElementById('mosaicListCount');
            if (cntEl) cntEl.textContent = visibleCount + ' MyMosaics';
        }}
    </script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_mymap_detail_page(user: Dict[str, Any], map_id: int = 0, lang: str = "en") -> str:
    norm_lang = normalize_lang(lang)
    store = get_store()
    uid = int(user["ID"])
    mname = ""
    mvis = "private"
    if map_id > 0:
        mymaps = store.list_user_mymaps(uid)
        for m in mymaps:
            if int(m.get("ID") or 0) == map_id:
                mname = str(m.get("NAME") or "")
                mvis = str(m.get("VISIBILITY") or "public").strip().lower()
                break

    hierarchy = store.list_myearth_hierarchy(uid, norm_lang)
    folder_pills = []
    for p in hierarchy.get("projects", []):
        pid = int(p.get("ID") or 0)
        pname = html.escape(str(p.get("NAME") or f"Project #{pid}"))
        pcnt = int(p.get("TOTAL_LAYERS") or 0)
        if pcnt > 0:
            folder_pills.append(
                f'<a href="/OpenContainerMosaic.ashx?kind=project&id={pid}" class="btn btn-sm" style="background:#1c2510;border:1px solid #99CC00;color:#99CC00;border-radius:999px;font-size:11px;font-weight:700;padding:4px 12px;text-decoration:none;display:inline-flex;align-items:center;gap:6px;"><span style="display:inline-flex;width:14px;height:14px;">{MYEARTH_ICON_PROJECT}</span> {pname} ({pcnt} layers)</a>'
            )
        for s in p.get("Surveys", []):
            sid = int(s.get("ID") or 0)
            sname = html.escape(str(s.get("NAME") or f"Survey #{sid}"))
            scnt = int(s.get("LAYER_COUNT") or 0)
            if scnt > 0 and sid != pid:
                folder_pills.append(
                    f'<a href="/OpenContainerMosaic.ashx?kind=survey&id={sid}" class="btn btn-sm" style="background:#171717;border:1px solid #333;color:#e5e7eb;border-radius:999px;font-size:11px;padding:4px 12px;text-decoration:none;display:inline-flex;align-items:center;gap:6px;"><span style="display:inline-flex;width:14px;height:14px;color:#99CC00;">{MYEARTH_ICON_SURVEY}</span> {pname} / {sname} ({scnt})</a>'
                )
    folder_bar_html = ""
    if folder_pills:
        folder_bar_html = f"""
        <div style="background:#151812;border:1px solid #2b3818;border-radius:12px;padding:12px 16px;margin-top:14px;">
            <div style="font-size:12px;color:#d9f99d;font-weight:700;margin-bottom:8px;">
                <i class="fas fa-folder-tree me-1" style="color:#99CC00;"></i> Quick Create / Open MyMosaic from a MyEarth Folder (Project or Survey)
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:8px;">
                {''.join(folder_pills[:14])}
            </div>
        </div>"""

    title_str = mname if mname else "New MyMosaic"
    seo_head = build_head_metadata_html(
        title=f"{title_str} · MyMosaics · MosaicEarth",
        description="Compose a multi-asset map scene with Maps & Layers, 3D Models, Point Clouds and Gaussian Splats.",
        canonical_path=f"/Client/MyMapDetail.aspx?id={map_id}" if map_id > 0 else "/Client/MyMapDetail.aspx",
        robots="noindex, nofollow",
    )

    head_extra = """
    <link rel="stylesheet" href="/assets/css/MyMapDetail.css?v=5" />
    <link rel="stylesheet" href="/assets/css/external-asset-import.css?v=7" />
    <link rel="stylesheet" href="/assets/css/mymap-refinements.css?v=4" />
    <style>
        #MyMapDetail .input-group-4d {
            background: #141414 !important;
            border: 1px solid #99CC00 !important;
            border-radius: 999px !important;
            padding: 2px 12px !important;
            align-items: center !important;
        }
        #MyMapDetail .input-group-4d input {
            background: transparent !important;
            border: 0 !important;
            color: #fff !important;
            box-shadow: none !important;
            font-size: 13px !important;
        }
        #MyMapDetail .input-group-4d .input-group-text {
            background: transparent !important;
            border: 0 !important;
            color: #fff !important;
        }
    </style>"""

    body_content = f"""
    <section id="MyMapDetail" style="padding-top: 40px;">
        <div class="container">
            <div class="row" style="margin-bottom: 15px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="row">
                                <div class="col-6">
                                    <a class="subpage-nav" href="/{norm_lang}/MyMaps">MyMosaics</a>
                                </div>
                                <div class="col-6 text-end">
                                    <button type="button" id="btnCancel" class="btn btn-secondary" onclick="saveMyMap()" style="margin-right: 20px; padding-left: 25px!important; padding-right: 25px!important;">SAVE</button>
                                </div>
                            </div>
                        </div>
                        <div class="content-box">
                            <p id="mosaicLoadStatus" role="status" aria-live="polite">Loading your assets and saved selection…</p>
                            <button type="button" id="mosaicLoadRetry" class="btn btn-secondary" hidden>Retry loading</button>
                            <div class="row">
                                <div class="col-md-6 col-12">
                                    <div class="form-group">
                                        <label for="inputName">Mosaic Name</label>
                                        <input type="text" class="form-control input-margin" id="inputName" maxlength="250" placeholder="Name" value="{html.escape(mname, quote=True)}" />
                                    </div>
                                </div>
                                <div class="col-md-6 col-12">
                                    <div class="form-group">
                                        <label for="selectMyMapVisibility">Visibility &amp; Sharing</label>
                                        <select class="form-control input-margin" id="selectMyMapVisibility" onchange="updateEditMyMosaicVisibility(this.value)">
                                            <option value="private"{' selected="selected"' if mvis == 'private' else ''}>Private — Only visible to you</option>
                                            <option value="unlisted"{' selected="selected"' if mvis == 'unlisted' else ''}>Unlisted — Shared via secret link only (not in Public Mosaics)</option>
                                            <option value="public"{' selected="selected"' if mvis == 'public' else ''}>Public — Listed in Public Mosaics directory</option>
                                        </select>
                                    </div>
                                </div>
                            </div>
                            {folder_bar_html}
                        </div>
                    </div>
                </div>
            </div>
            <div class="row">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="row">
                                <div class="col-7">
                                    MosaicAssets <br/> <span class="mosaic-assets-help">Combine your local layers with public MosaicAssets. Local layers stay on this computer; public layers need an Internet connection.</span>
                                    <span id="mosaicSelectionCount" class="mosaic-selection-summary" aria-live="polite">0 MosaicAssets selected</span>
                                </div>
                                <div class="col-md-4">
                                    <div class="input-group mb-3 input-group-4d">
                                        <input id="txtSearch" class="form-control" onkeyup="filtrarTexto()" placeholder="Search" />
                                        <div class="input-group-append">
                                            <span class="input-group-text"><i class="fas fa-search"></i></span>
                                        </div>
                                    </div>
                                </div>
                                <div class="col-md-1">
                                    <div class="dropdown ms-0 me-0" style="background: none; cursor: pointer;">
                                        <a class="btn-rounded dropdown-toggle" href="#" role="button" id="dropdown-filters" data-bs-toggle="dropdown" aria-expanded="false" data-bs-auto-close="outside">
                                            <img src="/assets/resources/Icon material-filter-list.svg" width="17" />
                                        </a>
                                        <div class="dropdown-menu box-filters" aria-labelledby="dropdown-filters" style="cursor: default;">
                                            <div class="row">
                                                <div class="col-12">
                                                    <div class="form-group">
                                                        <label>Category</label>
                                                        <select id="inputCategories" class="form-control">
                                                            <option value="2">Select a option</option>
                                                            <option value="1">Collection</option>
                                                            <option value="0">Own projects</option>
                                                        </select>
                                                    </div>
                                                </div>
                                                <div class="col-12">
                                                    <div class="form-group">
                                                        <label>Type</label>
                                                        <select id="inputType" class="form-control">
                                                            <option value="0">Select a option</option>
                                                            <option value="1">Pointcloud</option>
                                                            <option value="2">Maps &amp; Layers</option>
                                                            <option value="3">3D</option>
                                                        </select>
                                                    </div>
                                                </div>
                                                <div class="col-12 text-end">
                                                    <a id="lnkCancel" style="cursor: pointer;" class="me-4" onclick="clearFilters()">Clean</a>
                                                    <a id="lnkSearch" style="cursor: pointer;" onclick="applyFilters()">Apply</a>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="mosaic-filter-pills" role="group" aria-label="Filter MosaicAssets">
                            <button type="button" class="active filter-all" data-mosaic-filter-action="all" aria-pressed="true">All</button>
                            <strong>Ownership</strong><button type="button" data-mosaic-filter-group="ownership" data-mosaic-filter-value="created" aria-pressed="false">Created</button><button type="button" data-mosaic-filter-group="ownership" data-mosaic-filter-value="acquired" aria-pressed="false">Acquired</button>
                            <span></span><strong>Source</strong><button type="button" data-mosaic-filter-group="source" data-mosaic-filter-value="imported" aria-pressed="false">Imported</button><button type="button" data-mosaic-filter-group="source" data-mosaic-filter-value="processed" aria-pressed="false">Processed</button>
                            <span></span><strong>Family</strong><button type="button" data-mosaic-filter-group="type" data-mosaic-filter-value="2" aria-pressed="false">Maps &amp; Layers</button><button type="button" data-mosaic-filter-group="type" data-mosaic-filter-value="3" aria-pressed="false">3D Models</button><button type="button" data-mosaic-filter-group="type" data-mosaic-filter-value="1" aria-pressed="false">Point Clouds</button>
                            <span></span><strong>Access</strong><button type="button" data-mosaic-filter-group="access" data-mosaic-filter-value="public" aria-pressed="false">Public</button><button type="button" data-mosaic-filter-group="access" data-mosaic-filter-value="private" aria-pressed="false">Private</button>
                            <div class="mosaic-extension-filter"><span>File extensions</span><select multiple aria-label="File extensions" id="mosaicExtension"><option value="">All extensions</option></select></div>
                            <span class="mosaic-filter-dates"><label>From <input type="date" id="mosaicDateFrom" /></label><label>To <input type="date" id="mosaicDateTo" /></label></span><small id="mosaicFilterCount" aria-live="polite"></small><button type="button" class="filter-reset" id="mosaicFilterReset">Reset</button>
                        </div>
                        <div class="content-box">
                            <div id="items" class="row"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </section>
    <input type="hidden" id="hdnTranslations" value="{{}}" />
    <input type="hidden" id="hdnid" value="{map_id if map_id > 0 else 0}" />"""

    scripts_extra = """
    <script src="/js/front/MyMapDetail.js?v=20261106-visibility"></script>
    <script>
        MYLIBRARY.setUrl_page('/Client/MyMapDetail.aspx');
        loadItems("");
        CargarActiveMenu("li_mymaps");
        function updateEditMyMosaicVisibility(visVal) {
            var mapIdVal = Number((document.getElementById('hdnid') || {}).value || 0);
            if (mapIdVal <= 0) return;
            fetch('/api/v2/mymosaics/visibility', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mapId: mapIdVal, visibility: visVal})
            }).then(function(r) { return r.json(); }).then(function(data) {
                if (data && data.ok) {
                    if (typeof toastr !== 'undefined') toastr.success('MyMosaic visibility updated to ' + (data.badge || visVal) + '!');
                } else if (data && data.message) {
                    if (typeof toastr !== 'undefined') toastr.error(data.message);
                }
            }).catch(function(){});
        }
        (function() {
            var origSave = window.saveMyMap;
            if (typeof origSave === 'function') {
                window.saveMyMap = function() {
                    var visEl = document.getElementById('selectMyMapVisibility');
                    var mapIdVal = Number((document.getElementById('hdnid') || {}).value || 0);
                    var args = arguments;
                    var self = this;
                    if (visEl && mapIdVal > 0) {
                        fetch('/api/v2/mymosaics/visibility', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({mapId: mapIdVal, visibility: visEl.value})
                        }).finally(function() {
                            origSave.apply(self, args);
                        });
                        return;
                    }
                    return origSave.apply(this, arguments);
                };
            }
        })();
    </script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_mymap_viewer_page(user: Optional[Dict[str, Any]], id_or_slug: str, lang: str = "en") -> Optional[str]:
    norm_lang = normalize_lang(lang)
    store = get_store()
    uid = int(user["ID"]) if user else 0
    map_row = None
    map_id = 0
    raw = str(id_or_slug or "").strip()
    if raw.isdigit():
        map_id = int(raw)
    else:
        m_prefix = re.match(r"^(\d+)(?:-|$)", raw)
        if m_prefix:
            map_id = int(m_prefix.group(1))
        else:
            m_suffix = re.search(r"-(\d+)$", raw)
            if m_suffix:
                map_id = int(m_suffix.group(1))

    if uid > 0:
        for m in store.list_user_mymaps(uid):
            if (map_id > 0 and int(m.get("ID") or 0) == map_id) or str(m.get("SEO_URL_FRIENDLY") or "").lower() == raw.lower():
                map_row = m
                map_id = int(m.get("ID") or 0)
                break

    if not map_row and map_id > 0:
        for m in store.tables.get("dbo.WEB_MYMAPS", []):
            if int(m.get("ID") or 0) == map_id:
                map_row = m
                break

    if not map_row and map_id <= 0:
        return None

    is_desktop_local = os.environ.get("MOSAICEARTH_DESKTOP_MODE", "") == "1"
    mname = str(map_row.get("NAME") if map_row else f"MyMosaic #{map_id}")
    map_owner_id = int((map_row.get("USER_ID") or map_row.get("CREATE_ID") or 0) if map_row else 0)
    is_owner = (uid > 0 and uid == map_owner_id)
    owner_storage = store.get_user_storage_summary(uid) if is_owner else {}
    owner_plan = str(owner_storage.get("plan_code") or "free").lower() if is_owner else "pro"
    is_subscriber = (owner_plan in ("pro", "studio", "enterprise")) or is_desktop_local
    is_container_preview = bool((map_row or {}).get("IS_CONTAINER_PREVIEW")) or mname.startswith(("Survey: ", "Project: ", "Task: "))
    map_vis = str((map_row or {}).get("VISIBILITY") or ("private" if is_container_preview else "public")).strip().lower()
    if map_vis not in ("private", "unlisted", "public"):
        map_vis = "public"

    seo_head = build_head_metadata_html(
        title=f"{mname} · MyMosaic · MosaicEarth",
        description=f"Combined 3D and Orthophoto scene for {mname} on MosaicEarth.",
        canonical_path=f"/MyMap.aspx?id={map_id}",
        robots="noindex, nofollow",
    )

    if map_vis == "private" and not is_owner:
        restricted_body = f"""
        <section style="padding: 80px 16px; min-height: calc(100vh - 140px); display: flex; align-items: center; justify-content: center;">
            <div style="max-width: 520px; width: 100%; background: #141c17; border: 1px solid #3c4b40; border-radius: 10px; padding: 32px; text-align: center;">
                <div style="width: 48px; height: 48px; border-radius: 10px; background: rgba(153,204,0,0.14); border: 1px solid rgba(153,204,0,0.45); color: #99CC00; display: inline-flex; align-items: center; justify-content: center; font-size: 20px; margin-bottom: 14px;">
                    <i class="fas fa-lock"></i>
                </div>
                <h1 style="font-size: 20px; color: #fff; font-family: OpenSansBold, Arial, sans-serif; margin-bottom: 8px;">Private MyMosaic</h1>
                <p style="font-size: 13.5px; color: #b8c6be; line-height: 1.6; margin-bottom: 20px;">
                    This MyMosaic (<strong>{html.escape(mname)}</strong>) is set to <strong>Private (Owner Only)</strong>. Only the creator can view it, or the creator can switch its visibility to <strong>Unlisted (Secret Link)</strong> or <strong>Public</strong>.
                </p>
                <div style="display: flex; justify-content: center; gap: 10px; flex-wrap: wrap;">
                    <a href="/Default.aspx?ReturnUrl=%2FMyMap.aspx%3Fid%3D{map_id}" class="btn btn-primary" style="background:#99CC00;color:#0b110d;border:none;font-weight:700;padding:8px 18px;">Sign In as Owner</a>
                    <a href="/{norm_lang}/PublicMosaics" class="btn btn-secondary" style="padding:8px 18px;">Explore Public Mosaics</a>
                </div>
            </div>
        </section>"""
        return render_home_master("", seo_head, restricted_body, "", user=user, lang=norm_lang)

    cesium_key = store.get_cesium_browser_token()

    head_extra = """
    <link href="/vendor/cesium/Widgets/widgets.css" rel="stylesheet" />
    <script src="/vendor/cesium/Cesium.js"></script>
    <script src="/js/front/MosaicCesiumRuntime.js?v=__MOSAIC_MAP_RUNTIME_VERSION__"></script>
    <script src="/js/front/MosaicAnnotations.js?v=20261016-r45"></script>
    <link rel="stylesheet" href="/css/MosaicAnnotations.css?v=20261016-r45" />
    <link rel="stylesheet" href="/assets/css/Profile.css" />
    <link rel="stylesheet" href="/assets/css/MyMap.css?v=12" />
    <link rel="stylesheet" href="/assets/css/mosaic-temporal-filter.css?v=2" />
    <style>
        #toolbar {
            background: #080c0a;
            border-bottom: 1px solid rgba(153, 204, 0, 0.22);
            padding: 6px 18px;
            min-height: 46px;
            position: relative;
            z-index: 1020;
            display: flex;
            align-items: center;
        }
        #toolbar > .row {
            width: 100%;
            margin: 0 !important;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .container-fluid2 {
            display: flex;
            position: relative !important;
            height: calc(100dvh - 134px) !important;
            max-height: calc(100dvh - 134px) !important;
            overflow: hidden !important;
        }
        #itemsContainer {
            width: 290px;
            height: 100% !important;
            max-height: 100% !important;
            background-color: #0b0f0c;
            border-right: 1px solid rgba(255, 255, 255, 0.08);
            overflow-y: auto !important;
            padding: 10px 10px 24px 10px;
            scrollbar-width: thin;
        }
        #cesiumContainer {
            flex: 1;
            height: 100% !important;
            max-height: 100% !important;
            background-color: #111111;
            position: relative;
        }
        .titulo-mapedit { font-size: 15.5px; padding: 2px 4px; color: #99CC00; display: flex; align-items: center; gap: 6px; margin: 0; }
        .menu-map-edit { padding: 0; display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
        #toolbar #btnEdit, #toolbar #btnShare {
            margin: 0 !important;
            padding: 5px 16px !important;
            font-size: 12px !important;
            min-height: 32px !important;
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
        }
        .menu-map .icon-menu,
        .menu-map .icon-menu path,
        .menu-map .icon-menu circle,
        .menu-map .icon-menu rect {
            fill: currentColor !important;
        }
        .mymosaic-filter-stack {
            position: absolute !important;
            top: 14px !important;
            left: 306px !important;
            z-index: 1010 !important;
        }
        .container-fluid2 .cesium-viewer-toolbar {
            top: 14px !important;
            right: 14px !important;
        }
        @media (max-width: 1024px) {
            .mymosaic-filter-stack {
                position: absolute !important;
                top: 10px !important;
                left: 8px !important;
                right: 8px !important;
                width: calc(100vw - 16px) !important;
                max-width: calc(100vw - 16px) !important;
                z-index: 1010 !important;
            }
        }
        @media (max-width: 991.98px) {
            #toolbar {
                min-height: 42px !important;
                padding: 4px 10px !important;
            }
            #toolbar .row {
                display: flex !important;
                flex-wrap: wrap !important;
                align-items: center !important;
                justify-content: space-between !important;
                gap: 6px;
            }
            #toolbar .titulo-mapedit {
                width: 100% !important;
                flex: 1 0 100% !important;
                min-width: 0 !important;
                font-size: 13.5px !important;
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
                padding: 2px 4px !important;
            }
            #toolbar .titulo-mapedit > a { flex-shrink: 0; }
            #toolbar #nameProject { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
            #toolbar .menu-map-edit {
                width: 100% !important;
                flex: 1 0 100% !important;
                display: flex !important;
                align-items: center !important;
                gap: 6px !important;
                padding: 0 !important;
            }
            #toolbar .me-mymosaic-rule-note {
                display: none !important;
            }
            #toolbar #btnShare, #toolbar #btnEdit {
                margin: 0 !important;
                padding: 5px 12px !important;
                min-height: 44px !important;
                font-size: 11px !important;
            }
            #form1 .mymosaic-filter-stack > .menu-map { display: grid !important; grid-template-columns: repeat(4, minmax(0, 1fr)); }
            #form1 .mymosaic-filter-stack .menu-item { min-width: 0; overflow: visible; white-space: normal !important; line-height: 1.25; }
            #form1 .mymosaic-filter-stack .icon-menu { display: none !important; }
            .mymosaic-map-controls {
                bottom: 58px !important;
                right: 10px !important;
                z-index: 1005 !important;
            }
        }
    </style>"""

    vis_pill_lbl = (
        '<i class="fas fa-lock me-1"></i> PRIVATE · OWNER ONLY'
        if map_vis == "private"
        else ('<i class="fas fa-link me-1"></i> UNLISTED · SECRET LINK' if map_vis == "unlisted" else '<i class="fas fa-globe me-1"></i> PUBLIC MOSAIC')
    )

    edit_btn_html = (
        f'<span id="viewerVisBadge" style="display:inline-flex;align-items:center;padding:4px 10px;border-radius:999px;background:#17211b;border:1px solid #5a7a22;color:#99CC00;font-size:11px;font-weight:700;margin-right:8px;">{vis_pill_lbl}</span>'
        f'<a id="btnEdit" href="/Client/MyMapDetail.aspx?id={map_id}" class="btn btn-secondary btn-large btn_width br-25" style="padding-left: 20px; padding-right: 20px; margin-top: 6px; margin-right: 8px;"><span>EDIT</span></a>'
        if is_owner
        else f'<span id="viewerVisBadge" style="display:inline-flex;align-items:center;padding:4px 10px;border-radius:999px;background:#17211b;border:1px solid #5a7a22;color:#99CC00;font-size:11px;font-weight:700;margin-right:8px;">{vis_pill_lbl}</span><span class="me-mymosaic-rule-note" style="font-size:11px;color:#d9f99d;margin-right:12px;">Private assets inside are 3D View-Only; Public Free (0 &euro;) &amp; Purchased Marketplace (&euro;) assets allow original download</span>'
    )

    owner_vis_select_html = ""
    if is_owner:
        sel_priv = ' selected="selected"' if map_vis == "private" else ""
        sel_unl = ' selected="selected"' if map_vis == "unlisted" else ""
        sel_pub = ' selected="selected"' if map_vis == "public" else ""
        owner_vis_select_html = f"""
        <div class="col-12 mb-3">
            <label style="font-size:12px;color:#99CC00;font-weight:700;margin-bottom:4px;display:block;">MyMosaic Visibility</label>
            <select id="shareModalVisSelect" class="form-control" onchange="updateViewerMosaicVisibility({map_id}, this.value)" style="background:#121915;border:1px solid #45564b;color:#fff;font-size:12.5px;">
                <option value="private"{sel_priv}>Private &mdash; Only you can view</option>
                <option value="unlisted"{sel_unl}>Unlisted &mdash; Anyone with this link (hidden from Public Mosaics)</option>
                <option value="public"{sel_pub}>Public &mdash; Listed in Public Mosaics directory</option>
            </select>
        </div>"""

    if is_owner and not is_subscriber:
        share_modal_body_html = f"""
        <div style="background:rgba(153,204,0,0.08);border:1px solid rgba(153,204,0,0.35);border-radius:8px;padding:16px;text-align:center;">
            <div style="font-size:15px;font-family:OpenSansBold,Arial,sans-serif;color:#99CC00;margin-bottom:8px;">
                <i class="fas fa-crown me-1"></i> Subscriber-Only Feature
            </div>
            <p style="font-size:12.5px;color:#dce6dc;line-height:1.55;margin-bottom:14px;">
                This feature is exclusively available for subscribers. Upgrade to <strong>Pro (19 &euro;/month)</strong> or <strong>Studio (59 &euro;/month)</strong> to share MyMosaics via secret links (<strong>Unlisted</strong>) or publish them to <strong>Public Mosaics</strong>.
            </p>
            <a href="/{norm_lang}/CreditPacks" class="btn btn-primary" style="background:#99CC00;color:#0b110d;border:none;font-family:OpenSansBold,Arial,sans-serif;font-size:12px;padding:9px 22px;border-radius:6px;text-decoration:none;">
                VIEW SUBSCRIPTION PLANS &rarr;
            </a>
        </div>"""
        share_modal_btn_html = ""
    else:
        share_modal_body_html = f"""
        <div class="row">
            {owner_vis_select_html}
            <div class="col-12 processtop" style="padding-top: 8px; padding-bottom: 12px; font-size: 12.5px; color: #d1d5db; line-height: 1.5;">
                <strong>How Asset Access Works Inside a Shared MyMosaic:</strong>
                <ul style="padding-left:18px;margin:6px 0 0;color:#b8c6be;font-size:12px;">
                    <li><strong><i class="fas fa-lock me-1" style="color:#99CC00;"></i>Private Assets:</strong> Visitors can inspect &amp; fly over them in 3D (<strong>View-Only</strong>), but original file download is strictly blocked.</li>
                    <li><strong><i class="fas fa-globe me-1" style="color:#99CC00;"></i>Free Public Assets (0 &euro;):</strong> Visitors can view in 3D and download the original file for free.</li>
                    <li><strong><i class="fas fa-tag me-1" style="color:#99CC00;"></i>Marketplace Assets (&gt; 0 &euro;):</strong> Visitors can preview in 3D and purchase a commercial license (&euro;) to download the original file.</li>
                </ul>
            </div>
            <div class="col-12">
                <div class="form-group">
                    <input class="form-control input-margin" id="inputShare" placeholder="Share link..." />
                    <span id="copyMessage" style="display: none; color: #99CC00; font-size: 12px;">Copied!</span>
                </div>
            </div>
        </div>"""
        share_modal_btn_html = '<button id="changePasswordButton" type="button" onclick="copyLink()" class="btn btn-primary" style="font-size: 12px!important; padding: 10px 28px;">COPY LINK</button>'

    if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1" and is_owner:
        share_modal_body_html = """<p>This link opens the MyMosaic on this computer only. To share it online, synchronize it from MyMosaics and configure sharing on the completed cloud copy.</p><input class="form-control" id="inputShare" aria-label="Local MyMosaic link" readonly /><span id="copyMessage" style="display:none">Copied!</span>"""
        share_modal_btn_html = '<button type="button" onclick="copyLink()" class="btn btn-primary">COPY LOCAL LINK</button>'

    body_content = f"""
    <div id="toolbar">
        <div class="row">
            <div class="col-5 titulo-mapedit">
                <a style="color: white!important; font-family: OpenSansRegular; text-decoration: none;" href="/{norm_lang}/{'MyMaps' if user else 'PublicMosaics'}">{'MyMosaics' if user else 'Public MyMosaics'}</a>
                <span style="color:#6b7f73;">/</span>
                <label id="nameProject" style="margin:0;">{html.escape(mname)}</label>
            </div>
            <div class="col-7 menu-map-edit text-end">
                {edit_btn_html}
                <button type="button" id="btnShare" onclick="updateMyMosaicShareUrl()" data-bs-toggle="modal" data-bs-target="#staticBackdrop4" class="btn btn-primary btn-large btn_width br-25"><span>SHARE</span></button>
            </div>
        </div>
    </div>
    <div class="container-fluid2">
        <div class="mymosaic-filter-stack">
            <div class="menu-map" style="cursor: pointer;">
                <button type="button" aria-pressed="false" id="all-assets-menu" class="menu-item" style="border-right: 1px solid rgba(255,255,255,0.10);" onclick="loadCesium(0, null, null)">
                    <svg class="icon-menu" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="3" cy="3" r="2"/><path d="M7 1h6v4H7z"/><path d="M1 7h4v6H1z"/><circle cx="10" cy="10" r="3"/></svg>
                    All Assets
                </button>
                <button type="button" aria-pressed="false" id="orthophoto-menu" class="menu-item" style="border-right: 1px solid rgba(255,255,255,0.10);" onclick="loadCesium(2, null, null)">
                    {ICON_ORTHOPHOTO.replace('<svg ', '<svg class="icon-menu" ').replace('width="20" height="20"', 'width="13" height="13"')}
                    Maps &amp; Layers
                </button>
                <button type="button" aria-pressed="false" id="3d-menu" class="menu-item" style="border-right: 1px solid rgba(255,255,255,0.10);" onclick="loadCesium(3, null, null)">
                    {ICON_3D.replace('<svg ', '<svg class="icon-menu" ').replace('width="20" height="20"', 'width="13" height="13"')}
                    3D Models
                </button>
                <button type="button" aria-pressed="false" id="pointcloud-menu" class="menu-item" onclick="loadCesium(1, null, null)">
                    {ICON_CLOUD.replace('<svg ', '<svg class="icon-menu" ').replace('width="20" height="20"', 'width="13" height="13"')}
                    Point Clouds
                </button>
            </div>
            <div class="map-secondary-filters">
                <div class="mosaic-extension-filter">
                    <span>File extensions</span>
                    <select multiple aria-label="File extensions" id="myMosaicExtension">
                        <option value="">All extensions</option>
                        {_ext_options_html()}
                    </select>
                </div>
                <div id="myMosaicTemporalFilter" class="mosaic-temporal-host mymosaic-time"></div>
            </div>
        </div>
        <button id="mapMobileToggle" type="button" class="map-mobile-toggle is-collapsed" aria-controls="itemsContainer" aria-expanded="false">Explore assets</button>
        <div id="itemsContainer" class="is-mobile-collapsed">
            <div id="items" class="section-marketplace"></div>
        </div>
        <div id="cesiumContainer"></div>
    </div>
    <div class="mymosaic-map-controls" aria-label="Map controls">
        <div class="base-row">
            <button type="button" class="active" data-base="satellite" onclick="setMyMosaicBaseLayer('satellite')">Satellite</button>
            <button type="button" data-base="topographic" onclick="setMyMosaicBaseLayer('topographic')">Topographic</button>
            <button type="button" onclick="launchMyMosaicFly({map_id})" style="background:#99CC00;color:#070b09;font-weight:700;border-color:#99CC00;margin-left:6px;"><i class="fas fa-plane-departure me-1"></i> Fly View</button>
        </div>
    </div>

    <div class="modal fade" id="staticBackdrop4" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-labelledby="staticBackdropLabel4" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" style="max-width: 460px!important; min-width: 0!important;">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="staticBackdropLabel4">Share &amp; Visibility Rules</h5>
                </div>
                <div class="modal-body">
                    {share_modal_body_html}
                </div>
                <div class="modal-footer" style="margin-top: 0px!important;">
                    <div class="row w-100">
                        <div class="col-6">
                            <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CLOSE</button>
                        </div>
                        <div class="col-6 text-end">
                            {share_modal_btn_html}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <input type="hidden" id="hdnUrl" value="0" />
    <input type="hidden" id="hdnMapId" value="{map_id}" />"""

    scripts_extra = f"""
    <script>
        function launchMyMosaicFly(mapId) {{
            var url = '/Fly.aspx?asset_id=mymap-' + mapId;
            try {{
                var v = window.viewer || window.mosaicViewer;
                if (v && v.camera && v.camera.positionCartographic) {{
                    var c = v.camera.positionCartographic;
                    var lat = Cesium.Math.toDegrees(c.latitude);
                    var lon = Cesium.Math.toDegrees(c.longitude);
                    if (isFinite(lat) && isFinite(lon) && (Math.abs(lat) > 0.01 || Math.abs(lon) > 0.01)) {{
                        url += '&lat=' + lat.toFixed(6) + '&lon=' + lon.toFixed(6);
                    }}
                }}
            }} catch (e) {{}}
            window.location.href = url;
        }}
        function copyLink() {{
            updateMyMosaicShareUrl();
            mosaicCopyShareLink(document.getElementById("inputShare"), document.getElementById("copyMessage"));
        }}
        if ({'true' if is_desktop_local else 'false'}) {{
            window.addEventListener('DOMContentLoaded', function() {{
                fetch('/api/desktop/share-origin').then(function(r){{ return r.json(); }}).then(function(d){{
                    if (d && d.shareOrigin) {{
                        window.DESKTOP_LAN_SHARE_ORIGIN = d.shareOrigin;
                        var inp = document.getElementById('inputShare');
                        if (inp) inp.value = d.shareOrigin + '/MyMap.aspx?id={map_id}';
                    }}
                }}).catch(function(){{}});
            }});
        }}
        function updateViewerMosaicVisibility(mapId, visibility) {{
            fetch('/api/v2/mymosaics/visibility', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{mapId: mapId, visibility: visibility}})
            }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
                if (data && data.ok) {{
                    if (typeof toastr !== 'undefined') toastr.success(data.message || 'Visibility updated.');
                    var b = document.getElementById('viewerVisBadge');
                    if (b) {{
                        b.innerHTML = visibility === 'private' ? '<i class="fas fa-lock me-1"></i> PRIVATE · OWNER ONLY' : (visibility === 'unlisted' ? '<i class="fas fa-link me-1"></i> UNLISTED · SECRET LINK' : '<i class="fas fa-globe me-1"></i> PUBLIC MOSAIC');
                    }}
                }} else {{
                    alert((data && data.message) || 'Could not update visibility.');
                }}
            }});
        }}
        MYLIBRARY.setUrl_page('/MyMap.aspx');
        const URL_CESIUM = '';
        const MYMAP_ID = '{map_id}';
        const HEIGHT1 = 0;
        const HEIGHT2 = 0;
        const HEIGHT3 = 0;
        const HEIGHT4 = 0;
        const tipo = '';
        const initialAssetType = 0;
        const MYMOSAIC_SHARE_VIEW = {json.dumps(not is_owner)};
        const latMarket = '';
        const lonMarket = '';
        const heightMarket = '';
        const key = {json.dumps(cesium_key)};
        CargarActiveMenu("li_mymaps");
        function syncMyMosaicViewerHeight() {{
            var tb = document.getElementById('toolbar');
            var cf = document.querySelector('.container-fluid2');
            var ft = document.querySelector('footer.me-site-footer');
            if (!cf) return;
            var topOff = tb ? Math.ceil(tb.getBoundingClientRect().bottom) : 108;
            var ftH = ft ? Math.ceil(ft.getBoundingClientRect().height) : 28;
            var availH = Math.max(260, window.innerHeight - topOff - ftH);
            cf.style.setProperty('height', availH + 'px', 'important');
            cf.style.setProperty('max-height', availH + 'px', 'important');
            try {{
                if (typeof viewer !== 'undefined' && viewer && typeof viewer.resize === 'function') {{
                    viewer.resize();
                }}
            }} catch(e) {{}}
        }}
        syncMyMosaicViewerHeight();
        window.addEventListener('DOMContentLoaded', syncMyMosaicViewerHeight);
        window.addEventListener('load', syncMyMosaicViewerHeight);
        window.addEventListener('resize', syncMyMosaicViewerHeight);
    </script>
    <script src="/js/front/MosaicTemporalFilter.js?v=20260916-ux"></script>
    <script src="/js/front/MyMap.js?v=20261106-placement"></script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_profile_page(user: Dict[str, Any], lang: str = "en") -> str:
    norm_lang = normalize_lang(lang)
    store = get_store()
    uid = int(user["ID"])

    username = html.escape(str(user.get("USERNAME") or user.get("NAME") or ""))
    email = html.escape(str(user.get("EMAIL") or ""))
    credits_int = int(float(user.get("CREDITS") or 0))
    is_ext_acc = bool(user.get("IsExternalAccount"))
    ext_provider = html.escape(str(user.get("ExternalAccountProvider") or "Google"))

    odm = store.get_user_odm_settings(uid)
    is_desktop_mode = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
    use_custom_odm = bool(odm.get("UseCustom") or odm.get("Enabled"))
    if is_desktop_mode and not bool(odm.get("ExplicitCloudMode")):
        use_custom_odm = True
    raw_ep = str(odm.get("EndpointUrl") or odm.get("Endpoint") or "")
    if is_desktop_mode and not raw_ep:
        raw_ep = os.environ.get("MOSAICEARTH_LOCAL_NODEODM", "http://127.0.0.1:3000")
    odm_endpoint = html.escape(raw_ep)
    has_odm_token = bool(odm.get("HasToken"))
    odm_status = html.escape(
        str(
            odm.get("StatusSummary")
            or (f"Using custom NodeODM ({odm_endpoint})" if use_custom_odm and odm_endpoint else "Using MosaicEarth managed processing")
        )
    )

    fiscals = store.get_user_fiscal_data(uid)
    transactions = store.get_user_transactions(uid)
    premium_projects = store.get_user_premium_projects(uid)
    eligible_projects = store.get_eligible_premium_projects(uid)
    storage_info = store.get_user_storage_summary(uid)
    used_gb = float(storage_info.get("used_gb") or 0.0)
    quota_gb = float(storage_info.get("quota_gb") or 5.0)
    pct_used = float(storage_info.get("percent") or 0.0)
    plan_name = html.escape(str(storage_info.get("plan_name") or "Free Plan (5 GiB)"))

    seo_head = build_head_metadata_html(
        title="Account, Storage & Processing · MosaicEarth",
        description="Manage your MosaicEarth account, NodeODM processing mode, billing profiles, Euro balance and MyEarth Private Storage Plan.",
        canonical_path=f"/{norm_lang}/Profile",
        robots="noindex, nofollow",
    )

    head_extra = """
    <link rel="stylesheet" href="/assets/css/Profile.css?v=3" />
    <link rel="stylesheet" href="/assets/css/owl.carousel.min.css" />
    <link rel="stylesheet" href="/assets/css/owl.theme.default.min.css" />"""

    if is_ext_acc:
        security_ctrl = f"""
        <div class="external-account-security-note">
            <span class="external-account-badge">Signed in with {ext_provider}</span>
            <small>Email and password are managed by {ext_provider}.</small>
        </div>"""
    else:
        security_ctrl = """
        <button type="button" class="btn-secondary-2 me-2 mb-2" data-bs-toggle="modal" data-bs-target="#modalChangeEmail">CHANGE EMAIL</button>
        <button type="button" class="btn-secondary-2 mb-2" data-bs-toggle="modal" data-bs-target="#staticBackdrop4">CHANGE PASSWORD</button>"""

    fiscal_cards = []
    fiscal_edit_modals = []
    for f in fiscals:
        fid = int(f.get("ID") or 0)
        falias = html.escape(str(f.get("ALIAS") or f.get("FISCAL_NAME") or f.get("NAME") or "Default"))
        fname = html.escape(str(f.get("FISCAL_NAME") or f.get("NAME") or f.get("ALIAS") or ""))
        fvat = html.escape(str(f.get("VAT_NUMBER") or ""))
        fcountry = html.escape(str(f.get("COUNTRY") or ""))
        fstate = html.escape(str(f.get("REGION") or f.get("STATE") or ""))
        fcity = html.escape(str(f.get("CITY") or ""))
        fpostal = html.escape(str(f.get("ZIP_CODE") or f.get("POSTAL_CODE") or ""))
        faddr = html.escape(str(f.get("ADDRESS_LINE_1") or f.get("ADDRESS") or ""))
        faddr2 = html.escape(str(f.get("ADDRESS_LINE_2") or ""))
        fphone = html.escape(str(f.get("PHONE_NUMBER") or f.get("PHONE") or ""))
        femail = html.escape(str(f.get("EMAIL") or ""))
        is_def = bool(f.get("SELECTED_FISCAL") or f.get("DEFAULT_DATA"))

        fiscal_cards.append(
            f"""
            <div class="item-fiscal item">
                <div class="row">
                    <div class="col-10">
                        <div class="form-check form-check-inline">
                            <input class="form-check-input" type="radio" onclick="actualizarDefault({fid})" name="inlineRadioOptions" id="inlineRadio_{fid}" value="option1" {'checked' if is_def else ''} />
                            <label class="form-check-label alias-fiscal" for="inlineRadio_{fid}">{falias}</label>
                        </div>
                    </div>
                    <div class="col-2 text-end">
                        <button type="button" class="btn btn-cancel" style="font-size: 11px; padding: 7px 14px;" data-bs-toggle="modal" data-bs-target="#editFiscal{fid}">EDIT</button>
                    </div>
                    <div class="col-12" style="margin-top: 15px;">{fname}</div>
                    <div class="col-12">{fvat}</div>
                    <div class="col-12">{faddr}</div>
                    <div class="col-12">{fpostal} {fcity}, {fstate}</div>
                    <div class="col-12">{fcountry}</div>
                    <div class="col-12">{fphone}</div>
                </div>
            </div>"""
        )

        fiscal_edit_modals.append(
            f"""
            <div class="modal fade" id="editFiscal{fid}" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-labelledby="editLabelFiscal{fid}" aria-hidden="true">
                <div class="modal-dialog modal-dialog-centered" style="width: calc(100% - 24px); max-width: 600px!important; margin-left: auto; margin-right: auto;">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title" id="editLabelFiscal{fid}">Edit Fiscal Data</h5>
                        </div>
                        <div class="modal-body">
                            <div class="row">
                                <div class="col-12" style="padding-top: 10px; padding-bottom: 10px;">
                                    <div class="form-group">
                                        <label for="txtAlias{fid}">Alias *</label>
                                        <input type="text" class="form-control" id="txtAlias{fid}" placeholder="Alias" value="{falias}" required aria-required="true" />
                                    </div>
                                </div>
                                <div class="col-6" style="padding-top: 10px; padding-bottom: 10px;">
                                    <div class="form-group">
                                        <label for="txtFiscalName{fid}">Fiscal name *</label>
                                        <input type="text" class="form-control" id="txtFiscalName{fid}" placeholder="Fiscal Name" value="{fname}" required aria-required="true" />
                                    </div>
                                </div>
                                <div class="col-6" style="padding-top: 10px; padding-bottom: 10px;">
                                    <div class="form-group">
                                        <label for="txtVATnumber{fid}">VAT number *</label>
                                        <input type="text" class="form-control" id="txtVATnumber{fid}" placeholder="VAT Number" value="{fvat}" required aria-required="true" />
                                    </div>
                                </div>
                                <div class="col-6" style="padding-top: 10px; padding-bottom: 10px;">
                                    <div class="form-group">
                                        <label for="txtAddress1{fid}">Address line 1 *</label>
                                        <input type="text" class="form-control" id="txtAddress1{fid}" placeholder="Address line 1" value="{faddr}" required aria-required="true" />
                                    </div>
                                </div>
                                <div class="col-6" style="padding-top: 10px; padding-bottom: 10px;">
                                    <div class="form-group">
                                        <label for="txtAddress2{fid}">Address line 2</label>
                                        <input type="text" class="form-control" id="txtAddress2{fid}" placeholder="Address line 2 (Optional)" value="{faddr2}" />
                                    </div>
                                </div>
                                <div class="col-6" style="padding-top: 10px; padding-bottom: 10px;">
                                    <div class="form-group">
                                        <label for="txtZipCode{fid}">ZIP code *</label>
                                        <input type="text" class="form-control" id="txtZipCode{fid}" placeholder="ZIP code" value="{fpostal}" required aria-required="true" />
                                    </div>
                                </div>
                                <div class="col-6" style="padding-top: 10px; padding-bottom: 10px;">
                                    <div class="form-group">
                                        <label for="txtCity{fid}">City *</label>
                                        <input type="text" class="form-control" id="txtCity{fid}" placeholder="City" value="{fcity}" required aria-required="true" />
                                    </div>
                                </div>
                                <div class="col-6" style="padding-top: 10px; padding-bottom: 10px;">
                                    <div class="form-group">
                                        <label for="txtRegion{fid}">Region *</label>
                                        <input type="text" class="form-control" id="txtRegion{fid}" placeholder="Region" value="{fstate}" required aria-required="true" />
                                    </div>
                                </div>
                                <div class="col-6" style="padding-top: 10px; padding-bottom: 10px;">
                                    <div class="form-group">
                                        <label for="txtCountry{fid}">Country</label>
                                        <input type="text" class="form-control" id="txtCountry{fid}" placeholder="Country" value="{fcountry}" />
                                    </div>
                                </div>
                                <div class="col-6" style="padding-top: 10px; padding-bottom: 10px;">
                                    <div class="form-group">
                                        <label for="txtPhone{fid}">Phone number *</label>
                                        <input type="text" class="form-control" id="txtPhone{fid}" placeholder="Phone number" value="{fphone}" required aria-required="true" />
                                    </div>
                                </div>
                                <div class="col-6" style="padding-top: 10px; padding-bottom: 10px;">
                                    <div class="form-group">
                                        <label for="txtEmailFiscal{fid}">Email</label>
                                        <input type="text" class="form-control" id="txtEmailFiscal{fid}" placeholder="Email (optional)" value="{femail}" />
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer" style="margin-top: 0px!important;">
                            <div class="row w-100">
                                <div class="col-6">
                                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                                </div>
                                <div class="col-6 text-end">
                                    <button id="borrarPapelera{fid}" class="borrar-fiscal" onclick="borrarFiscalData({fid})" type="button">
                                        <img src="/Resources/papelera.svg" /></button>
                                    <button id="editFiscalData{fid}" type="button" onclick="editFiscalData({fid})" class="btn btn-primary" style="font-size: 12px!important; padding: 10px 34px 10px 34px;">SAVE</button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>"""
        )

    tx_rows = []
    for t in transactions:
        tid = html.escape(str(t.get("ID") or ""))
        tconcept = html.escape(str(t.get("Description") or t.get("CONCEPTO") or ""))
        tdate = html.escape(str(t.get("DateDisplay") or str(t.get("CREATE_DATE") or "")[:10]))
        signed_str = str(t.get("SignedCredits") or t.get("CREDITS") or "0")
        color = "#FF0000" if signed_str.startswith("-") else "#99CC00"
        tx_rows.append(
            f"""
            <div class="row row-table">
                <div class="col-2 text-row-table">{tid}</div>
                <div class="col-5 text-row-table">{tconcept}</div>
                <div class="col-3 text-row-table">{tdate}</div>
                <div class="col-2 text-row-table"><span style="color: {color}">{html.escape(signed_str)}</span></div>
            </div>"""
        )

    prem_rows = []
    for pp in premium_projects:
        pp_id = int(pp.get("PROJECT_ID") or pp.get("ID") or 0)
        pp_name = html.escape(str(pp.get("NAME") or ""))
        prem_rows.append(
            f"""
            <div class="col-12" style="padding-bottom: 40px;">
                <div class="row">
                    <div class="col-6 titleMetaMask showAccount">
                        <span class="showAccount">{pp_name}</span>
                    </div>
                    <div class="col-5 text-end">
                        <span class="editLink" data-bs-toggle="modal" data-bs-target="#modalPremiumProject" onclick="loadInfoPopUpProject({pp_id})">EDIT</span>
                    </div>
                </div>
            </div>"""
        )

    elig_opts = ['<option value="-1">Select a project</option>']
    for ep in eligible_projects:
        elig_opts.append(f'<option value="{ep["ID"]}">{html.escape(str(ep.get("NAME") or ""))}</option>')

    email_help_html = (
        f'<small class="profile-field-help">Your sign-in email is managed by {ext_provider}.</small>'
        if is_ext_acc
        else '<small class="profile-field-help">Your email identifies the account. A change only takes effect after verification and preserves your projects and MosaicAssets.</small><button type="button" class="btn btn-cancel" style="margin-top:10px" data-bs-toggle="modal" data-bs-target="#modalChangeEmail">CHANGE EMAIL</button>'
    )
    security_col_html = (
        f'<div class="col-md-12 external-account-note"><strong>External sign-in</strong><span>Email and password changes are managed by {ext_provider}.</span></div>'
        if is_ext_acc
        else '<div class="col-md-12"><div class="form-group"><label>Account security</label><small class="profile-field-help">Use a unique password of at least 10 characters. Password changes are applied immediately.</small><input type="button" id="inputNewPassword" data-bs-toggle="modal" data-bs-target="#staticBackdrop4" class="btn btn-primary" value="CHANGE PASSWORD" /></div></div>'
    )

    body_content = f"""
    <section id="profile">
        <div class="container">
            <div class="row" style="margin-bottom: 15px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="d-inline-block" style="width: 48%!important;">Account</div>
                            <div class="d-inline-block w-50 text-end" style="padding-right: 25px;">
                                <button type="button" class="btn btn-titulo" onclick="guardarDatos()">SAVE CHANGES</button>
                            </div>
                        </div>
                        <div class="content-box">
                            <div class="row">
                                <div class="col-12 col-lg-6 profile-column" style="padding-right: 30px;">
                                    <div class="row">
                                        <div class="col-md-12">
                                            <div class="form-group">
                                                <label for="inputUsername">Username</label>
                                                <input type="text" class="form-control input-margin" id="inputUsername" placeholder="How other users will see you" maxlength="80" value="{username}" />
                                                <small class="profile-field-help">This is your public creator name. It does not need to be your legal name.</small>
                                            </div>
                                        </div>
                                        <div class="col-md-12">
                                            <div class="form-group">
                                                <label for="inputEmail">Sign-in email</label>
                                                <input type="email" class="form-control input-margin readonly" id="inputEmail" placeholder="Email" value="{email}" readonly="readonly" />
                                                {email_help_html}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div class="col-12 col-lg-6 profile-column profile-security-column" style="padding-left: 50px;">
                                    <div class="row">
                                        {security_col_html}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="row" style="margin-bottom: 20px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">Processing service (Serverless Cloud GPU vs. Local NodeODM)</div>
                        <div class="content-box">
                            <div class="processing-mode-picker">
                                <label class="processing-mode {'selected' if not use_custom_odm else ''}" data-processing-mode="managed"><input type="radio" name="processingMode" value="managed" {'checked' if not use_custom_odm else ''}/><span class="processing-mode-icon">ME</span><span><strong>MosaicEarth Serverless Cloud GPU</strong><small>Recommended. Uses your monthly Pro/Studio GPU quota ({int(storage_info.get('included_gpu_images_month') or 0)} images/mo &amp; {int(storage_info.get('included_gaussian_splats_month') or 0)} Gaussian Splats/mo included) or bills pay-per-task in Euros (&euro;) only upon completion ({float(storage_info.get('extra_gpu_rate_eur_per_image') or 0.03):.3f} &euro;/img).</small><em>Monthly Plan Quota or Pay-Per-Task (&euro;) &middot; Zero setup</em></span></label>
                                <label class="processing-mode {'selected' if use_custom_odm else ''}" data-processing-mode="custom"><input type="radio" name="processingMode" value="custom" {'checked' if use_custom_odm else ''}/><span class="processing-mode-icon">API</span><span><strong>Use my own local / custom NodeODM</strong><small>Connect MosaicEarth Desktop or your own compatible NodeODM / NodeODX service. Processing runs on your own hardware at 0 &euro; platform cost.</small><em>0 &euro; per task &middot; Local / Custom NodeODM</em></span></label>
                            </div>
                            <div id="customOdmSettings" class="row align-items-end custom-odm-settings {'is-visible' if use_custom_odm else ''}">
                                <div class="col-12 col-lg-5">
                                    <div class="form-group">
                                        <label for="odmEndpoint">Your NodeODM or NodeODX endpoint</label>
                                        <input type="url" class="form-control input-margin" id="odmEndpoint" placeholder="https://node.example.com" value="{odm_endpoint}" />
                                    </div>
                                </div>
                                <div class="col-12 col-lg-4">
                                    <div class="form-group">
                                        <label for="odmToken">API token</label>
                                        <input type="password" class="form-control input-margin" id="odmToken" placeholder="{'Saved — leave blank to keep it' if has_odm_token else 'Token'}" autocomplete="new-password" />
                                    </div>
                                </div>
                                <input type="checkbox" id="odmEnabled" {'checked' if use_custom_odm else ''} hidden />
                                <div class="col-12" style="margin-top:18px;">
                                    <button type="button" class="btn btn-primary" onclick="saveUserOdm(false)">SAVE ODM SETTINGS</button>
                                    <button type="button" class="btn btn-cancel" style="margin-left:10px;" onclick="saveUserOdm(true)">TEST CONNECTION</button>
                                    <button type="button" class="btn btn-cancel" style="margin-left:10px;" onclick="clearUserOdm()">CLEAR</button>
                                    <span id="odmSettingsResult" style="margin-left:15px;"></span>
                                </div>
                            </div>
                            <div id="managedOdmExplanation" class="managed-processing-note {'is-visible' if not use_custom_odm else ''}"><strong>Managed processing is selected.</strong><span>New projects use the MosaicEarth platform node. Your own endpoint and token remain stored but inactive until you select “Use my own processing node”.</span><button type="button" class="btn btn-cancel managed-test-button" onclick="testManagedOdm()">CHECK PLATFORM NODE</button><span id="managedOdmResult"></span></div>
                        </div>
                    </div>
                </div>
            </div>

            {''.join(fiscal_edit_modals)}

            <div class="row" style="margin-bottom: 20px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="d-inline-block" style="width: 48%!important;">Fiscal data</div>
                            <div class="d-inline-block w-50 text-end" style="padding-right: 25px;">
                                <button type="button" class="btn btn-titulo" data-bs-toggle="modal" data-bs-target="#staticBackdrop5">+ NEW</button>
                            </div>
                        </div>
                        <div class="content-box" style="padding-top: 0px; padding-bottom: 65px; padding-right: 30px;">
                            <div class="row">
                                <div class="owl-carousel owl-theme">
                                    {''.join(fiscal_cards)}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="row" style="margin-bottom: 20px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">MyEarth Private Storage Plan &amp; Quota</div>
                        <div class="content-box" style="padding-top: 20px; padding-bottom: 35px; padding-right: 30px;">
                            <div style="background:#171717;border:1px solid #2a2a2a;border-radius:14px;padding:18px 20px;">
                                <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:10px;">
                                    <div>
                                        <span style="background:rgba(153,204,0,0.16);color:#99CC00;border:1px solid #99CC00;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:700;">{plan_name}</span>
                                        <strong style="color:#fff;font-size:14px;margin-left:10px;">{used_gb:.2f} GiB / {quota_gb:.0f} GiB Private Storage Used ({pct_used:.1f}%)</strong>
                                    </div>
                                    <a href="/{norm_lang}/CreditPacks" class="btn btn-primary" style="font-size:11px;padding:7px 18px;">UPGRADE STORAGE PLAN (250 GiB / 1,000 GiB)</a>
                                </div>
                                <div style="height:10px;background:#0d0d0d;border-radius:999px;overflow:hidden;border:1px solid #2d2d2d;">
                                    <div style="width:{max(2.0, min(100.0, pct_used)):.1f}%;height:100%;background:linear-gradient(90deg,#99CC00,#c8ff29);border-radius:999px;"></div>
                                </div>
                                <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-top:10px;font-size:12px;color:#9ca3af;">
                                    <span><i class="fas fa-lock me-1" style="color:#99CC00;"></i><strong>Private Assets:</strong> Stored permanently within your {quota_gb:.0f} GiB quota ({int(storage_info.get('private_assets_count') or 0)} private layers).</span>
                                    <span style="color:#d9f99d;"><i class="fas fa-globe me-1" style="color:#99CC00;"></i><strong>Public Global Map Assets:</strong> Consume <strong>0 GiB</strong> of your private quota ({int(storage_info.get('public_assets_count') or 0)} public layers).</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="row" style="margin-bottom: 20px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">Creator Marketplace Earnings (&euro;) &amp; Pay-Per-Purchase Billing</div>
                        <div class="content-box" style="padding-top: 0px; padding-bottom: 65px; padding-right: 20px!important;">
                            <div class="credit-balance-grid">
                                <div class="credit-balance-card">
                                    <div class="credit-balance-summary">
                                        <span class="balance-label">Creator Marketplace Revenue ({int(storage_info.get('creator_payout_pct') or 80)}% Payout)</span>
                                        <div><label id="labelCredits" class="numberCredits">{float(storage_info.get('creator_earnings_eur') or credits_int):.2f}</label><span class="creditsName"> &euro;</span></div>
                                    </div>
                                    <div class="credit-balance-copy">
                                        <p><strong>No prepaid wallet required:</strong> Marketplace assets and pay-per-task Cloud GPU jobs (beyond your plan's monthly included quota of <strong>{int(storage_info.get('included_gpu_images_month') or 0)} images/mo</strong>) are billed directly per purchase in Euros (&euro;). Every sale of your published Marketplace assets pays <strong>{int(storage_info.get('creator_payout_pct') or 80)}% creator revenue</strong> directly to your account.</p>
                                        <div class="credit-actions">
                                            <a href="/{norm_lang}/CreditPacks" class="btn btn-primary">SUBSCRIPTION PLANS &amp; CLOUD RATES (&euro;)</a>
                                            <a href="#" class="credit-history-link" data-bs-toggle="modal" data-bs-target="#staticBackdrop1">PURCHASE &amp; PAYOUT HISTORY</a>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </section>

    <!-- Change Password Modal -->
    <div class="modal fade" id="staticBackdrop4" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header"><h5 class="modal-title">Change Password</h5></div>
                <div class="modal-body">
                    <div class="form-group mb-3"><label>Current password</label><input type="password" id="txtOldPassword" class="form-control" /></div>
                    <div class="form-group mb-3"><label>New password</label><input type="password" id="txtNewPassword" class="form-control" /></div>
                    <div class="form-group mb-3"><label>Repeat new password</label><input type="password" id="txtRepeatPassword" class="form-control" /></div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                    <button type="button" class="btn btn-primary" onclick="actualizarContrasena();">CHANGE</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Change Email Modal -->
    <div class="modal fade" id="modalChangeEmail" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Change Email</h5>
                </div>
                <div class="modal-body">
                    <div class="form-group mb-3"><label>New email</label><input type="email" id="txtNewEmail" class="form-control" placeholder="name@domain.com" /></div>
                    <div class="form-group mb-2"><label>Current password</label><input type="password" id="txtEmailCurrentPassword" class="form-control" /></div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                    <button type="button" class="btn btn-primary" onclick="requestEmailChange();">SEND CONFIRMATION</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Add Fiscal Modal -->
    <div class="modal fade" id="staticBackdrop5" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" style="max-width: 600px!important;">
            <div class="modal-content">
                <div class="modal-header"><h5 class="modal-title">Add Fiscal Data</h5></div>
                <div class="modal-body">
                    <div class="row">
                        <div class="col-6 mb-3"><div class="form-group"><label>Full name / Company</label><input type="text" id="txtFullNameAdd" class="form-control" placeholder="Full name / Company" /></div></div>
                        <div class="col-6 mb-3"><div class="form-group"><label>VAT number</label><input type="text" id="txtNifAdd" class="form-control" placeholder="NIF/CIF/NIE" /></div></div>
                        <div class="col-6 mb-3"><div class="form-group"><label>Country</label><input type="text" id="txtCountryAdd" class="form-control" placeholder="Country" /></div></div>
                        <div class="col-6 mb-3"><div class="form-group"><label>Region/State</label><input type="text" id="txtRegionAdd" class="form-control" placeholder="Region/State" /></div></div>
                        <div class="col-6 mb-3"><div class="form-group"><label>City</label><input type="text" id="txtCityAdd" class="form-control" placeholder="City" /></div></div>
                        <div class="col-6 mb-3"><div class="form-group"><label>ZIP code</label><input type="text" id="txtPostalAdd" class="form-control" placeholder="Postal code" /></div></div>
                        <div class="col-6 mb-3"><div class="form-group"><label>Address</label><input type="text" id="txtAddressAdd" class="form-control" placeholder="Address" /></div></div>
                        <div class="col-6 mb-3"><div class="form-group"><label>Phone number</label><input type="text" id="txtPhoneAdd" class="form-control" placeholder="Phone number" /></div></div>
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                    <button type="button" class="btn btn-primary" onclick="anadirFiscalData()">SAVE</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Transaction History Modal -->
    <div class="modal fade" id="staticBackdrop1" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered modal-lg">
            <div class="modal-content">
                <div class="modal-header"><h5 class="modal-title">Transaction History</h5></div>
                <div class="modal-body">
                    <div class="row header-table">
                        <div class="col-2 text-header-table">ID</div>
                        <div class="col-5 text-header-table">Concept</div>
                        <div class="col-3 text-header-table">Date</div>
                        <div class="col-2 text-header-table">Credits</div>
                    </div>
                    <div id="transactions" class="body-table">
                        {''.join(tx_rows) if tx_rows else '<div class="row row-table"><div class="col-12 text-row-table">No credit transactions recorded yet.</div></div>'}
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- New Premium Project Modal -->
    <div class="modal fade" id="modalNewPremiumProject" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header"><h5 class="modal-title">Upgrade Project to Premium</h5></div>
                <div class="modal-body">
                    <select id="selectProjectList" class="form-control mb-4">
                        {''.join(elig_opts)}
                    </select>
                    <p style="font-size: 12px; color:#ccc;">The monthly fee (20 credits) is automatically deducted from your credit balance every 30 days.</p>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CANCEL</button>
                    <button type="button" class="btn btn-primary" onclick="upgradeProject()">UPGRADE FOR 20 CREDITS</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Cancel Premium Modal -->
    <div class="modal fade" id="modalPremiumProject" data-bs-backdrop="static" data-bs-keyboard="false" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header"><h5 class="modal-title">Cancel Subscription</h5></div>
                <div class="modal-body">
                    <p id="infoPanelPremium" style="font-size: 12px; color:#ccc;"></p>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-cancel" data-bs-dismiss="modal">CLOSE</button>
                    <button type="button" id="btnCancelProject" class="btn btn-primary">CANCEL SUBSCRIPTION</button>
                </div>
            </div>
        </div>
    </div>
    <input type="hidden" id="hdnTranslations" value="{{}}" />"""

    scripts_extra = """
    <script src="/js/front/Profile.js?v=3"></script>
    <script src="/js/front/owl.carousel.js"></script>
    <script>
        MYLIBRARY.setUrl_page('/Client/Profile.aspx');
        function saveUserOdm(testConnection) {
            var result = document.getElementById('odmSettingsResult');
            result.textContent = testConnection ? 'Testing…' : 'Saving…';
            result.style.color = '#8f8f8f';
            $.ajax({
                type: 'POST',
                url: '/Client/Profile.aspx/SaveUserOdmSettings',
                data: JSON.stringify({
                    mode: document.getElementById('odmEnabled').checked ? 'custom' : 'managed',
                    endpointUrl: document.getElementById('odmEndpoint').value,
                    token: document.getElementById('odmToken').value,
                    testConnection: testConnection
                }),
                contentType: 'application/json; charset=utf-8',
                dataType: 'json'
            }).done(function (response) {
                var payload = typeof response.d === 'string' ? JSON.parse(response.d) : response.d;
                result.textContent = payload.message || 'Settings saved.';
                result.style.color = payload.ok !== false ? '#99CC00' : '#e05252';
            });
        }
        function clearUserOdm() {
            $.ajax({
                type: 'POST',
                url: '/Client/Profile.aspx/ClearUserOdmSettings',
                data: '{}',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json'
            }).done(function () {
                document.getElementById('odmEndpoint').value = '';
                document.getElementById('odmToken').value = '';
                selectProcessingMode('managed', false);
            });
        }
        function selectProcessingMode(mode, persistManaged) {
            var custom = mode === 'custom';
            document.getElementById('odmEnabled').checked = custom;
            document.getElementById('customOdmSettings').classList.toggle('is-visible', custom);
            document.getElementById('managedOdmExplanation').classList.toggle('is-visible', !custom);
            document.querySelectorAll('[data-processing-mode]').forEach(function (card) {
                card.classList.toggle('selected', card.getAttribute('data-processing-mode') === mode);
            });
            if (!custom && persistManaged) saveUserOdm(false);
        }
        function testManagedOdm() {
            var result = document.getElementById('managedOdmResult');
            result.textContent = 'Checking…';
            $.ajax({
                type: 'POST',
                url: '/Client/Profile.aspx/TestManagedOdm',
                data: '{}',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json'
            }).done(function (response) {
                var payload = typeof response.d === 'string' ? JSON.parse(response.d) : response.d;
                result.textContent = payload.message || 'Online';
                result.style.color = '#99CC00';
            });
        }
        document.querySelectorAll('input[name="processingMode"]').forEach(function (radio) {
            radio.addEventListener('change', function () { selectProcessingMode(radio.value, radio.value === 'managed'); });
        });
        $('.owl-carousel').owlCarousel({
            loop: false,
            margin: 90,
            nav: false,
            responsive: {
                0: { items: 1 },
                600: { items: 2 },
                1000: { items: 3 }
            }
        });
    </script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_credit_packs_page(user: Dict[str, Any], lang: str = "en") -> str:
    from .blob_store import get_blob_store
    norm_lang = normalize_lang(lang)
    store = get_store()
    uid = int(user["ID"])
    storage_info = store.get_user_storage_summary(uid)
    active_plan = str(storage_info.get("plan_key") or "free").strip().lower()
    blob_status = get_blob_store().get_storage_status()

    seo_head = build_head_metadata_html(
        title="Subscription Plans, Cloud Storage & Pay-Per-Use Processing (€) · MosaicEarth",
        description="Choose your MosaicEarth Subscription Plan (Free 5 GiB, Pro 250 GiB + 1,000 Cloud GPU images/mo, Studio 1,000 GiB + 4,000 Cloud GPU images/mo + Desktop License) and pay per purchase in Euros (€) with no prepaid wallet required.",
        canonical_path=f"/{norm_lang}/CreditPacks",
        robots="noindex, nofollow",
    )

    head_extra = """
    <link rel="stylesheet" href="/assets/css/CreditPacks.css?v=3" />
    <style>
        .me-rate-box code { color:#d9edb4; font-size:inherit; background:transparent; }
        #requestPayoutButton:disabled, #setupPayoutButton:disabled { opacity:0.45; cursor:not-allowed; }
        .me-plan-card {
            background: #141c17;
            border: 1.5px solid #2e3d33;
            border-radius: 12px;
            padding: 22px;
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: border-color 0.16s ease, box-shadow 0.16s ease;
        }
        .me-plan-card--active {
            border: 2px solid #99CC00;
            background: linear-gradient(180deg, rgba(153,204,0,0.09) 0%, rgba(20,28,23,0.98) 100%);
            box-shadow: 0 12px 30px rgba(0,0,0,0.5);
        }
        .me-rate-box {
            background: #121915;
            border: 1px solid #2e3d33;
            border-radius: 10px;
            padding: 18px 20px;
            height: 100%;
        }
    </style>"""

    btn_free = (
        '<button type="button" class="btn btn-secondary w-100" disabled style="font-size:12px;font-weight:700;">ACTIVE PLAN (0 &euro;/MO)</button>'
        if active_plan == "free"
        else '<button type="button" class="btn btn-secondary w-100" onclick="switchSubscriptionPlan(\'free\')" style="font-size:12px;font-weight:700;">SWITCH TO FREE (0 &euro;/MO)</button>'
    )
    btn_pro = (
        '<button type="button" class="btn btn-primary w-100" disabled style="font-size:12px;font-weight:700;">ACTIVE PLAN (PRO &middot; 19 &euro;/MO)</button>'
        if active_plan == "pro"
        else '<button type="button" class="btn btn-primary w-100" onclick="switchSubscriptionPlan(\'pro\')" style="font-size:12px;font-weight:700;">ACTIVATE PRO PLAN (19 &euro;/MO)</button>'
    )
    is_legacy_studio = bool(storage_info.get("is_legacy_studio"))
    btn_studio = (
        '<button type="button" class="btn btn-primary w-100" disabled style="font-size:12px;font-weight:700;">ACTIVE PLAN (STUDIO &middot; LEGACY &middot; 1,000 GiB INCLUDED)</button>'
        if (active_plan == "studio" and is_legacy_studio)
        else (
            '<button type="button" class="btn btn-primary w-100" disabled style="font-size:12px;font-weight:700;">ACTIVE PLAN (STUDIO &middot; 59 &euro;/MO)</button>'
            if active_plan == "studio"
            else '<button type="button" class="btn btn-primary w-100" onclick="switchSubscriptionPlan(\'studio\')" style="font-size:12px;font-weight:700;">ACTIVATE STUDIO PLAN (59 &euro;/MO)</button>'
        )
    )

    body_content = f"""
    <section id="creditPacks">
        <div class="container">
            <div class="row" style="margin-bottom: 15px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
                                <div>
                                    <a class="section-back-link" href="/{norm_lang}/Profile" style="margin-right:10px;"><span aria-hidden="true">&larr;</span> Account</a>
                                    <span class="credit-packs-title">Subscription Plans, Cloud Storage &amp; Direct Pay-Per-Use (&euro;)</span>
                                </div>
                                <span style="font-size:12px;color:#99CC00;font-family:OpenSansBold,Arial,sans-serif;">Active Plan: {html.escape(str(storage_info.get('plan_name') or 'Free (5 GiB)'))}</span>
                            </div>
                        </div>
                        <div class="content-box" style="padding: 24px;">
                            <p class="page-purpose" style="margin-bottom:20px;">
                                <strong>Choose a plan or pay for individual cloud tasks in Euros (&euro;).</strong>
                                Every account includes <strong>5 GiB of Free Private Storage</strong> forever (plus <strong>0 GiB quota cost</strong> for any asset published to the Global 3D Map).
                                Upgrade to <strong>Pro (19 &euro;/mo)</strong> or <strong>Studio (59 &euro;/mo)</strong> for expanded private cloud storage, included monthly Cloud GPU processing, unlisted client link sharing, and higher Marketplace creator payouts (up to 90%). Outside your plan's monthly quota, you simply pay per individual task or asset purchase in Euros (&euro;).
                            </p>

                            <p style="font-size:13px;color:#cbd5cf;">Storage is measured in GiB (1 GiB = 1,073,741,824 bytes). Cloud photogrammetry beyond your allowance costs 0.030 €/effective image on Free, 0.020 € on Pro or 0.015 € on Studio, with a 2.00 € minimum per billed task. Photos averaging over 12 MiB use a size multiplier: for example, 100 photos averaging 24 MiB count as 200 effective images (fractions round up). Included allowances use the same effective-image count and reset on the first day of each UTC calendar month; unused allowance does not roll over. Extra Gaussian reconstructions cost 3.00 €, 2.00 € or 1.50 €, respectively. The final quote is shown before payment. Processing failure triggers refund reconciliation; bank delays may apply.</p>
                            <!-- 3 Subscription Plans -->
                            <div class="row g-4 mb-4">
                                <div class="col-lg-4 col-md-12">
                                    <div class="me-plan-card {'me-plan-card--active' if active_plan == 'free' else ''}">
                                        <div>
                                            <div style="display:flex;justify-content:space-between;align-items:center;">
                                                <span style="color:#99CC00;font-weight:800;font-size:12px;letter-spacing:0.6px;">FREE FOREVER</span>
                                                {('<span style="background:#99CC00;color:#0b110d;font-size:10px;font-weight:800;padding:2px 8px;border-radius:999px;">CURRENT PLAN</span>' if active_plan == 'free' else '')}
                                            </div>
                                            <div style="color:#fff;font-size:28px;font-weight:800;margin:6px 0;">0 &euro; <small style="font-size:13px;color:#9ca3af;">/ month</small></div>
                                            <div style="color:#d9f99d;font-size:13px;font-weight:700;margin-bottom:12px;">5 GiB Private Storage + Unlimited Public Assets</div>
                                            <ul style="color:#d1d5db;font-size:12.5px;line-height:1.7;padding-left:18px;margin-bottom:18px;">
                                                <li><strong>5 GiB Private Cloud Storage</strong> (Projects &rarr; Surveys &rarr; Tasks &rarr; Assets)</li>
                                                <li><strong>0 GiB Quota Used</strong> by Public Global Map &amp; Marketplace assets</li>
                                                <li><strong>WebODM, WebUI, QGIS &amp; CLI Cloud Sync INCLUDED</strong> (uses your 5 GiB Private Quota or 0 GiB if Public)</li>
                                                <li><strong>Public &amp; Private MyMosaics</strong> &amp; <strong>80% Creator Payout</strong> on Marketplace sales (&euro;)</li>
                                                <li><strong>0 &euro; Local Desktop Processing</strong> or Pay-Per-Task Cloud GPU (<code>0.03 &euro;/effective image</code>, min <code>2 &euro;</code>; <code>3 &euro;</code>/Gaussian Splat)</li>
                                            </ul>
                                        </div>
                                        {btn_free}
                                    </div>
                                </div>

                                <div class="col-lg-4 col-md-12">
                                    <div class="me-plan-card {'me-plan-card--active' if active_plan == 'pro' else ''}">
                                        <div>
                                            <div style="display:flex;justify-content:space-between;align-items:center;">
                                                <span style="color:#99CC00;font-weight:800;font-size:12px;letter-spacing:0.6px;">PRO WORKSPACE &amp; CLOUD GPU</span>
                                                {('<span style="background:#99CC00;color:#0b110d;font-size:10px;font-weight:800;padding:2px 8px;border-radius:999px;">CURRENT PLAN</span>' if active_plan == 'pro' else '<span style="background:rgba(153,204,0,0.18);color:#99CC00;border:1px solid #99CC00;font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;">MOST POPULAR</span>')}
                                            </div>
                                            <div style="color:#fff;font-size:28px;font-weight:800;margin:6px 0;">19 &euro; <small style="font-size:13px;color:#9ca3af;">/ month</small></div>
                                            <div style="color:#d9f99d;font-size:13px;font-weight:700;margin-bottom:12px;">250 GiB Private Storage + Monthly Cloud GPU Quota</div>
                                            <ul style="color:#e5e7eb;font-size:12.5px;line-height:1.7;padding-left:18px;margin-bottom:18px;">
                                                <li><strong>250 GiB Private Cloud Storage</strong> for heavy Ortho, 3D Tiles &amp; LiDAR</li>
                                                <li><strong>1,000 Cloud GPU Images/month + 3 Gaussian Splats/month INCLUDED</strong> (extra images at discounted <code>0.02 &euro;/img</code>)</li>
                                                <li><strong>Unlisted Secret Client Links</strong> for MyMosaics, Projects &amp; Surveys</li>
                                                <li><strong>85% Creator Payout</strong> on Marketplace asset sales (&euro;)</li>
                                                <li><strong>Cloud Photogrammetry</strong> &amp; Project Sync (WebODM, WebUI, QGIS &amp; CLI)</li>
                                            </ul>
                                        </div>
                                        {btn_pro}
                                    </div>
                                </div>

                                <div class="col-lg-4 col-md-12">
                                    <div class="me-plan-card {'me-plan-card--active' if active_plan == 'studio' else ''}">
                                        <div>
                                            <div style="display:flex;justify-content:space-between;align-items:center;">
                                                <span style="color:#99CC00;font-weight:800;font-size:12px;letter-spacing:0.6px;">STUDIO &amp; ENTERPRISE</span>
                                                {('<span style="background:#99CC00;color:#0b110d;font-size:10px;font-weight:800;padding:2px 8px;border-radius:999px;">CURRENT PLAN</span>' if active_plan == 'studio' else '')}
                                            </div>
                                            <div style="color:#fff;font-size:28px;font-weight:800;margin:6px 0;">59 &euro; <small style="font-size:13px;color:#9ca3af;">/ month</small></div>
                                            <div style="color:#d9f99d;font-size:13px;font-weight:700;margin-bottom:12px;">1,000 GiB Private Storage + 4,000 GPU Imgs/mo + Desktop App</div>
                                            <ul style="color:#d1d5db;font-size:12.5px;line-height:1.7;padding-left:18px;margin-bottom:18px;">
                                                <li><strong>1,000 GiB Private Cloud Storage</strong> &amp; Unlisted Sharing Links</li>
                                                <li><strong>4,000 Cloud GPU Images/month + 12 Gaussian Splats/month INCLUDED</strong> (extra images at <code>0.015 &euro;/img</code>)</li>
                                                <li><strong>Includes MosaicEarth Desktop Turnkey License (29 &euro; value)</strong> for Windows &amp; Linux</li>
                                                <li><strong>90% Creator Payout</strong> on Marketplace asset sales (&euro;)</li>
                                                <li><strong>Local CPU / compatible NVIDIA GPU processing</strong> with Desktop</li>
                                            </ul>
                                        </div>
                                        {btn_studio}
                                    </div>
                                </div>
                            </div>

                            <!-- Extra Private Cloud Storage Add-on Packs -->
                            <div class="row g-3 mb-4">
                                <div class="col-12">
                                    <div class="me-rate-box" style="border-color:rgba(153,204,0,0.45);background:linear-gradient(90deg, rgba(153,204,0,0.08) 0%, rgba(18,25,21,0.98) 100%);">
                                        <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:16px;">
                                            <div style="flex:1;min-width:280px;">
                                                <div style="font-size:13px;font-family:OpenSansBold,Arial,sans-serif;color:#99CC00;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">
                                                    <i class="fas fa-hdd me-1"></i> Need More Private Cloud Storage? Add Extra Storage Packs Anytime
                                                </div>
                                                <div style="font-size:12.5px;color:#dce6dc;line-height:1.55;">
                                                    Current Private Quota: <strong style="color:#fff;">{float(storage_info.get('used_gb') or 0.0):.2f} GiB used of {float(storage_info.get('quota_gb') or 5.0):.0f} GiB</strong>
                                                    {f' <span style="color:#99CC00;font-weight:700;">(includes +{int(storage_info.get("extra_storage_gb") or 0)} GiB Extra Storage)</span>' if float(storage_info.get("extra_storage_gb") or 0) > 0 else ''}.
                                                    Add extra dedicated Private Cloud Storage on top of any plan (Free, Pro, Studio, or Studio &middot; Legacy):
                                                </div>
                                            </div>
                                            <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;">
                                                <button type="button" class="btn btn-outline-light btn-sm" onclick="purchaseExtraStorage(250)" style="border-color:#99CC00;color:#99CC00;font-weight:700;font-size:12px;padding:8px 14px;">
                                                    <i class="fas fa-plus-circle me-1"></i> +250 GiB Extra &middot; 9 &euro;/mo
                                                </button>
                                                <button type="button" class="btn btn-primary btn-sm" onclick="purchaseExtraStorage(1000)" style="font-weight:700;font-size:12px;padding:8px 14px;">
                                                    <i class="fas fa-plus-circle me-1"></i> +1,000 GiB Extra &middot; 29 &euro;/mo
                                                </button>
                                                <button type="button" class="btn btn-outline-light btn-sm" onclick="purchaseExtraStorage(5000)" style="border-color:#99CC00;color:#99CC00;font-weight:700;font-size:12px;padding:8px 14px;">
                                                    <i class="fas fa-plus-circle me-1"></i> +5,000 GiB Extra &middot; 119 &euro;/mo
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <!-- Direct Pay-Per-Purchase & Cloud Storage / Serverless GPU Architecture -->
                            <div class="row g-4">
                                <div class="col-lg-6 col-12">
                                    <div class="me-rate-box">
                                        <div style="font-size:13px;font-family:OpenSansBold,Arial,sans-serif;color:#99CC00;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:10px;">
                                            <i class="fas fa-credit-card me-1"></i> Direct Pay-Per-Purchase Rates (&euro;) &mdash; No Prepaid Wallet
                                        </div>
                                        <p style="font-size:12.5px;color:#b8c6be;margin-bottom:12px;">
                                            You never need to pre-load credits or keep idle money in a balance. Every transaction outside your monthly plan allowance is paid directly in Euros (&euro;):
                                        </p>
                                        <ul style="font-size:12.5px;color:#dce6dc;line-height:1.7;padding-left:18px;margin-bottom:0;">
                                            <li><strong>Cloud Photogrammetry:</strong> <code>0.030 &euro;</code> per effective image on Free, <code>0.020 &euro;</code> on Pro and <code>0.015 &euro;</code> on Studio after the included allowance. Minimum <code>2.00 &euro;</code> per billed task on every plan; the size multiplier described above applies.</li>
                                            <li><strong>3D Gaussian Splatting:</strong> extra tasks cost <code>3.00 &euro;</code> on Free, <code>2.00 &euro;</code> on Pro and <code>1.50 &euro;</code> on Studio (3/mo included in Pro, 12/mo in Studio).</li>
                                            <li><strong>Local Workstation / Desktop Processing:</strong> <strong>0.00 &euro; forever</strong> using your own CPU/GPU via MosaicEarth Desktop or local NodeODM.</li>
                                            <li><strong>Marketplace Commercial Licenses:</strong> Direct Euro (&euro;) checkout per asset (80%&ndash;90% credited to the creator wallet; bank withdrawal after verification).</li>
                                        </ul>
                                    </div>
                                </div>
                                <div class="col-lg-6 col-12">
                                    <div class="me-rate-box">
                                        <div style="font-size:13px;font-family:OpenSansBold,Arial,sans-serif;color:#99CC00;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:10px;">
                                            <i class="fas fa-shield-alt me-1"></i> Upfront Quote &amp; Failed-Task Refunds
                                        </div>
                                        <p style="font-size:12.5px;color:#b8c6be;margin-bottom:12px;">
                                            Transparent pricing with zero surprises before launching any photogrammetry or 3D reconstruction task:
                                        </p>
                                        <ul style="font-size:12.5px;color:#dce6dc;line-height:1.7;padding-left:18px;margin-bottom:0;">
                                            <li><strong>Upfront Quote Before Processing:</strong> Every task displays the exact image count, dataset size, and final Euro (&euro;) price (or monthly plan quota coverage) before you confirm.</li>
                                            <li><strong>Refund reconciliation:</strong> Failed processing starts automatic reconciliation of the paid amount. Your wallet is restored after confirmation; card refunds can take time to appear at your bank. Included allowance is restored only within the same quota month. A pending refund is shown as pending, not completed.</li>
                                            <li><strong>Versioned photogrammetry engines:</strong> Managed cloud processing uses tested engine releases. Local processing is available through Desktop or your own compatible NodeODM / NodeODX service.</li>
                                        </ul>
                                    </div>
                                </div>
                            </div>
                            <div class="row g-3 mt-1">
                                <div class="col-12">
                                    <div class="me-rate-box" style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:14px;">
                                        <div>
                                            <div style="font-size:13px;font-family:OpenSansBold,Arial,sans-serif;color:#99CC00;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">
                                                <i class="fas fa-wallet me-1"></i> Creator Marketplace Earnings Wallet ({storage_info.get('creator_payout_pct', 80)}% Revenue Share)
                                            </div>
                                            <div style="font-size:12.5px;color:#b8c6be;">
                                                Available Creator Earnings: <strong id="creatorAvailableBalance" style="color:#fff;font-size:15px;">{float(storage_info.get('creator_earnings_eur') or 0.0):.2f} &euro;</strong> &middot; Available for one-off purchases/processing. Bank withdrawals require Stripe verification (minimum 10.00 &euro;); bank processing times apply.
                                            </div>
                                        </div>
                                        <div class="d-flex flex-wrap gap-2 align-items-center">
                                            <label style="font-size:12px;color:#b8c6be;">Payout country (ISO code)
                                                <input id="payoutCountryInput" type="text" maxlength="2" placeholder="ES" aria-label="Country of your Stripe payout account, two-letter ISO code" style="width:55px;background:#17221b;color:#fff;border:1px solid #637858;border-radius:4px;padding:5px;" />
                                            </label>
                                            <button id="setupPayoutButton" type="button" class="btn btn-secondary btn-sm" onclick="setupCreatorPayout()" disabled>Set up bank withdrawals</button>
                                            <button id="requestPayoutButton" type="button" class="btn btn-primary btn-sm" onclick="requestCreatorPayout()" disabled>Request bank withdrawal</button>
                                            <button type="button" class="btn btn-secondary btn-sm" onclick="loadCreatorPayouts()">Refresh status</button>
                                            <div id="creatorPayoutStatus" role="status" style="width:100%;font-size:13px;color:#d1d5db;">Loading withdrawal status…</div>
                                            <div id="creatorPayoutError" role="alert" style="width:100%;font-size:13px;color:#ffb7ac;"></div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </section>
    <input type="hidden" id="hdnTranslations" value="{{}}" />"""

    scripts_extra = """
    <script>
        MYLIBRARY.setUrl_page('/Client/CreditPacks.aspx');
        function switchSubscriptionPlan(planKey) {
            if (planKey === 'free' && !window.confirm('Switch to Free now? Your paid plan will be canceled immediately, with no further plan charges. Separately purchased storage subscriptions remain active. Your files will be preserved.')) return;
            fetch('/api/v2/plans/upgrade', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({plan: planKey})
            }).then(function(r) { return r.json(); }).then(function(data) {
                if (data && data.requiresStripe && data.checkoutUrl) {
                    window.location.href = data.checkoutUrl;
                } else if (data && data.ok) {
                    if (typeof toastr !== 'undefined') toastr.success(data.message || 'Subscription plan updated!');
                    setTimeout(function() { window.location.reload(); }, 450);
                } else {
                    alert((data && data.message) || 'Could not update subscription plan.');
                }
            });
        }
        function purchaseExtraStorage(extraGb) {
            fetch('/api/v2/storage/upgrade-extra', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({extra_gb: extraGb})
            }).then(function(r) { return r.json(); }).then(function(data) {
                if (data && data.requiresStripe && data.checkoutUrl) {
                    window.location.href = data.checkoutUrl;
                } else if (data && data.ok) {
                    alert(data.message || ('+' + extraGb + ' GiB Extra Storage activated!'));
                    window.location.reload();
                } else {
                    alert((data && data.message) || 'Could not start Extra Storage checkout.');
                }
            });
        }
        async function payoutRequest(url, body) {
            var response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body || {})});
            var data = await response.json();
            if (!data.ok) throw new Error(data.message || 'Withdrawal action could not be confirmed. Please retry.');
            return data;
        }
        async function setupCreatorPayout() {
            var button = document.getElementById('setupPayoutButton');
            button.disabled = true;
            document.getElementById('creatorPayoutError').textContent = '';
            try {
                var country = document.getElementById('payoutCountryInput').value.trim().toUpperCase();
                if (window.creatorPayoutReady) {
                    var dashboard = await payoutRequest('/api/v2/wallet/connect', {dashboard:true});
                    window.location.href = dashboard.url;
                    return;
                }
                if (!/^[A-Z]{2}$/.test(country)) throw new Error('Enter the two-letter country code for your payout account, for example ES.');
                var data = await payoutRequest('/api/v2/wallet/connect', {country:country});
                if (data.url) window.location.href = data.url;
            } catch(e) { document.getElementById('creatorPayoutError').textContent = e.message; }
            finally { await loadCreatorPayouts(); }
        }
        var payoutRefreshTimer;
        async function loadCreatorPayouts() {
            clearTimeout(payoutRefreshTimer);
            var box = document.getElementById('creatorPayoutStatus');
            if (!box) return;
            try {
                var response = await fetch('/api/v2/wallet/payouts');
                if (!response.ok) throw new Error('Withdrawal status is temporarily unavailable.');
                var data = await response.json();
                window.creatorPayoutReady = !!data.ready;
                document.getElementById('creatorAvailableBalance').textContent = data.availableEur.toFixed(2) + ' €';
                if (data.country) document.getElementById('payoutCountryInput').value = data.country;
                document.getElementById('payoutCountryInput').disabled = !!data.hasAccount || !data.enabled;
                document.getElementById('setupPayoutButton').disabled = !data.enabled;
                document.getElementById('setupPayoutButton').textContent = data.ready ? 'Manage payout account in Stripe' : 'Set up bank withdrawals';
                box.textContent = 'Available: ' + data.availableEur.toFixed(2) + ' € · Pending bank withdrawal: ' + data.pendingEur.toFixed(2) + ' € · Reserved for processing checkout: ' + (data.processingHeldEur || 0).toFixed(2) + ' €.';
                if (!data.enabled) box.appendChild(document.createTextNode(' Bank withdrawals are awaiting Stripe Connect activation; available earnings remain in your wallet.'));
                else if (!data.ready) box.appendChild(document.createTextNode(' Complete Stripe account verification before withdrawing.'));
                document.getElementById('requestPayoutButton').disabled = !data.enabled || !data.ready || data.availableEur < 10 || data.pendingEur > 0;
                var labels = {RESERVED:'Reserved, awaiting transfer', REQUESTED_STRIPE_PAYOUT:'Legacy request: reconciliation required', TRANSFER_INTENT:'Transfer confirmation pending', TRANSFERRED:'In your Stripe balance, awaiting bank payout', PAYOUT_INTENT:'Bank payout confirmation pending', PENDING:'Bank payout pending', IN_TRANSIT:'In transit to your bank', PAID:'Paid to bank', FAILED:'Bank payout failed: update bank details in Stripe and retry', REVIEW_REQUIRED:'Awaiting reconciliation', CANCELED:'Canceled, returned to wallet'};
                data.payouts.forEach(function(p) {
                    var line = document.createElement('div');
                    line.textContent = '#' + p.id + ' · ' + p.amountEur.toFixed(2) + ' € · ' + (labels[p.status] || p.status);
                    if (p.canCancel || p.canRetry) {
                        var button = document.createElement('button');
                        button.className = 'btn btn-secondary btn-sm';
                        button.textContent = p.canCancel ? 'Cancel withdrawal' : 'Retry bank payout';
                        button.onclick = async function() {
                            button.disabled = true;
                            document.getElementById('creatorPayoutError').textContent = '';
                            try { await payoutRequest('/api/v2/wallet/payouts/' + p.id + '/' + (p.canCancel ? 'cancel' : 'retry')); }
                            catch(e) { document.getElementById('creatorPayoutError').textContent = e.message; }
                            finally { await loadCreatorPayouts(); }
                        };
                        line.appendChild(button);
                    }
                    box.appendChild(line);
                });
                if (data.pendingEur > 0 && !document.hidden) payoutRefreshTimer = setTimeout(loadCreatorPayouts, 30000);
            } catch(e) {
                box.textContent = e.message + ' Use Refresh status to try again.';
                document.getElementById('requestPayoutButton').disabled = true;
            }
        }
        async function requestCreatorPayout() {
            document.getElementById('requestPayoutButton').disabled = true;
            document.getElementById('creatorPayoutError').textContent = '';
            try { await payoutRequest('/api/v2/wallet/request-payout'); }
            catch(e) { document.getElementById('creatorPayoutError').textContent = e.message; }
            finally { await loadCreatorPayouts(); }
        }
        document.addEventListener('visibilitychange', function() {
            if (document.hidden) clearTimeout(payoutRefreshTimer);
            else loadCreatorPayouts();
        });
        if (new URLSearchParams(location.search).get('connect') === 'return') {
            payoutRequest('/api/v2/wallet/connect', {refresh:true}).catch(function() {}).finally(loadCreatorPayouts);
        } else { loadCreatorPayouts(); }
    </script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def _render_unified_task_upload_page(
    user: Dict[str, Any],
    project_id: int,
    task_kind: str = "imported",
    lang: str = "en",
) -> Optional[str]:
    norm_lang = normalize_lang(lang)
    store = get_store()
    proj = store.get_project(project_id)
    if not proj or int(proj.get("CREATE_ID") or 0) != int(user.get("ID") or 0):
        return None
    root_pid, _ = store.get_subprojects_family(project_id)
    root_proj = store.get_project(root_pid) or proj
    root_pname = html.escape(str(root_proj.get("NAME") or f"Project #{root_pid}"))
    survey_pid = int(proj.get("SURVEY_ID") or proj.get("SURVEY_PROJECT_ID") or proj.get("PARENT_PROJECT_ID") or root_pid)
    survey_proj = store.get_project(survey_pid) or root_proj
    survey_pname = html.escape(str(survey_proj.get("NAME") or f"Survey #{survey_pid}"))
    pname = html.escape(str(proj.get("NAME") or f"Task #{project_id}"))
    pdesc = html.escape(str(proj.get("TASK_DESCRIPTION") or proj.get("DESCRIPTION") or ""))
    pdate = html.escape(str(proj.get("TASK_CREATION_DATE") or proj.get("CAPTURE_DATE") or "")[:10])
    pmethod = html.escape(str(proj.get("CAPTURE_METHOD") or proj.get("METHODOLOGY") or ("3D Gaussian Splatting" if task_kind == "gaussian" else "Direct File Import")))
    _raw_lat_v = proj.get("LATITUD")
    _raw_lon_v = proj.get("LONGITUD")
    _has_real_coords = False
    try:
        _lf = float(_raw_lat_v) if _raw_lat_v not in (None, "") else 0.0
        _lnf = float(_raw_lon_v) if _raw_lon_v not in (None, "") else 0.0
        if (abs(_lf) > 1e-5 or abs(_lnf) > 1e-5) and not (
            (abs(_lf - 41.8205) < 1e-4 and abs(_lnf - 1.8676) < 1e-4)
            or (abs(_lf - 41.9794) < 1e-4 and abs(_lnf - 2.8214) < 1e-4)
        ):
            _has_real_coords = True
    except Exception:
        _has_real_coords = False
    if proj.get("GCP_OBSERVATIONS") or proj.get("PHOTOGRAMMETRY_CONTROL"):
        _has_real_coords = True
    plat = html.escape(str(_raw_lat_v)) if _has_real_coords and _raw_lat_v is not None else ""
    plon = html.escape(str(_raw_lon_v)) if _has_real_coords and _raw_lon_v is not None else ""
    myearth_return_url = f"/{norm_lang}/Projects?open_project={root_pid}#taskNode_{project_id}"
    gcp_obs_doc = proj.get("GCP_OBSERVATIONS") if isinstance(proj.get("GCP_OBSERVATIONS"), dict) else {}
    gcp_attached_count = len([p for p in (gcp_obs_doc.get("points") or []) if isinstance(p, dict) and p.get("role", "gcp") != "check"])

    is_gaussian = task_kind == "gaussian"
    task_badge_label = "Gaussian Splatting Task" if is_gaussian else "Direct File Import Task"
    canonical_path = (
        f"/Client/GaussianProject.aspx?project_id={project_id}"
        if is_gaussian
        else f"/Client/ExternalAssetImport.aspx?project_id={project_id}"
    )

    seo_head = build_head_metadata_html(
        title=f"{pname} · {task_badge_label} · MosaicEarth",
        description=f"{task_badge_label} {pname} inside {root_pname} on MosaicEarth.",
        canonical_path=canonical_path,
        robots="noindex, nofollow",
    )

    existing_assets = [
        a for a in store.get_external_assets_by_project(project_id)
        if not bool(a.get("DELETED"))
    ]
    from .asset_uploads import asset_output_available
    latest = store.get_job_queue().get_job(project_id=project_id) or {}
    existing_rows_html = []
    for a in existing_assets:
        aid = int(a.get("ID") or 0)
        atitle = html.escape(str(a.get("TITLE") or a.get("DISPLAY_NAME") or a.get("ORIGINAL_FILENAME") or f"Asset #{aid}"))
        afmt = html.escape(str(a.get("SOURCE_FORMAT") or a.get("FORMAT") or "GLB").upper())
        alat = html.escape(str(a.get("LATITUDE") or plat))
        alon = html.escape(str(a.get("LONGITUDE") or plon))
        v_href = f"/Client/ExternalAssetDetail.aspx?project_id={project_id}&asset_id={aid}"
        dl_href = f"/ExternalAssetFile.ashx?project_id={project_id}&asset_id={aid}&download=1"
        ready = asset_output_available(a)
        if ready:
            afmt = html.escape(str(a.get("OUTPUT_FORMAT") or a.get("SOURCE_FORMAT") or a.get("FORMAT") or "").upper())
        if not ready:
            detail = html.escape(str(a.get("STATUS_DETAIL") or "Original saved."))
            existing_rows_html.append(
                f'<div class="mb-2"><strong>{atitle}</strong> · {afmt} source file · {detail} '
                f'<a href="/ExternalAssetFile.ashx?project_id={project_id}&amp;asset_id={aid}&amp;mode=original&amp;download=1">Download original</a></div>'
            )
            continue
        existing_rows_html.append(
            f"""
            <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:10px;padding:11px 14px;background:#121714;border:1px solid rgba(255,255,255,0.10);border-radius:9px;margin-bottom:8px;">
                <div style="display:flex;align-items:center;gap:10px;">
                    <span style="padding:2px 8px;border-radius:5px;background:rgba(153,204,0,0.16);border:1px solid rgba(153,204,0,0.4);color:#99CC00;font-size:10.5px;font-weight:700;">{afmt}</span>
                    <div>
                        <div style="font-size:13.5px;font-weight:700;color:#fff;">{atitle}</div>
                        <div style="font-size:11px;color:#9ca3af;">WGS84: {alat}, {alon}</div>
                    </div>
                </div>
                <div style="display:flex;gap:8px;align-items:center;">
                    <a href="{v_href}" class="btn btn-sm" style="background:rgba(153,204,0,0.14);border:1px solid #99CC00;color:#99CC00;font-weight:700;font-size:11px;">3D View</a>
                    <a href="{dl_href}" class="btn btn-sm" style="background:#1c221e;border:1px solid rgba(255,255,255,0.16);color:#e5e7eb;font-size:11px;">Download</a>
                </div>
            </div>"""
        )

    existing_block = ""
    if existing_rows_html:
        existing_block = f"""
        <div style="margin-bottom:20px;padding:16px;background:rgba(153,204,0,0.05);border:1px solid rgba(153,204,0,0.28);border-radius:11px;">
            <div style="font-size:11.5px;font-weight:700;color:#99CC00;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:10px;">
                Assets already in this Task ({len(existing_rows_html)})
            </div>
            {''.join(existing_rows_html)}
        </div>"""

    accepted_exts = (
        ".ply,.splat,.spz,.ksplat,.mp4,.mov,.webm,.m4v,.zip,.jpg,.jpeg,.png,.webp,.srt,.gpx,.csv"
        if is_gaussian
        else ".tif,.tiff,.laz,.las,.e57,.obj,.fbx,.glb,.gltf,.dae,.ply,.splat,.spz,.ksplat,.zip,.geojson,.json,.kml,.kmz,.gpkg,.dxf,.shp,.gpx,.csv"
    )
    uid = int(user.get("ID") or 0)
    upfront_task_quote_banner = ""
    cancel_gaussian_html = ""
    if is_gaussian:
        if os.environ.get("MOSAICEARTH_GAUSSIAN_ENGINE") == "runpod-opensplat":
            active_job = store.get_job_queue().get_job(project_id=project_id) or {}
            active_payload = active_job.get("PAYLOAD") or {}
            if (active_job.get("STATUS") in ("QUEUED", "RUNNING") and active_payload.get("source_origin") == "gaussian"
                    and Path(str(active_payload.get("source_filename") or "")).suffix.lower() not in (".ply", ".splat", ".spz", ".ksplat")):
                cancel_gaussian_html = f'''
                    <div class="mb-3">
                        <button type="button" class="btn btn-secondary" id="cancel-gaussian-training">Cancel cloud training</button>
                        <span id="gaussian-cancel-status" role="status"></span>
                    </div>
                    <script>
                        document.getElementById('cancel-gaussian-training').addEventListener('click', async (event) => {{
                            const button = event.currentTarget;
                            const status = document.getElementById('gaussian-cancel-status');
                            button.disabled = true;
                            try {{
                                const result = await fetch('/api/v2/tasks/{project_id}/cancel', {{method:'POST'}});
                                if (!result.ok) throw new Error();
                                location.reload();
                            }} catch (_) {{
                                status.textContent = 'The training could not be canceled. Refresh to check its current status.';
                                button.disabled = false;
                            }}
                        }});
                    </script>'''
        g_quote = store.calculate_task_processing_quote(uid, 0, "gaussian")
        g_price = float(g_quote.get("total_price_eur") or 0.0)
        g_plan = html.escape(str(g_quote.get("plan_label") or "Free Plan"))
        g_badge = (
            f"0,00 € (Covered by {g_plan} Monthly Quota)"
            if g_price <= 0.0
            else f"{g_price:.2f}".replace(".", ",") + " € Upfront Pay-Per-Task (100% Auto-Refund if failed)"
        )
        upfront_task_quote_banner = f"""
        <div style="margin-bottom:18px;padding:13px 16px;background:rgba(153,204,0,0.08);border:1px solid rgba(153,204,0,0.38);border-radius:10px;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:10px;">
            <div>
                <div style="font-size:11px;font-weight:700;color:#99CC00;text-transform:uppercase;letter-spacing:0.05em;">Upfront Processing Quote &amp; Failure Protection</div>
                <div style="font-size:12.5px;color:#e2e8f0;margin-top:2px;">
                    Direct <code>.PLY / .SPLAT / .SPZ / .KSPLAT</code> import is <strong>0,00 € (Free)</strong>. Cloud GPU Gaussian Splatting training from video/photos is quoted upfront before launch and <strong>100% automatically refunded (0,00 € charged)</strong> if processing fails.
                </div>
            </div>
            <div style="padding:6px 12px;border-radius:8px;background:#121814;border:1px solid #99CC00;color:#99CC00;font-weight:800;font-size:13px;white-space:nowrap;">
                {g_badge}
            </div>
        </div>"""
    if is_gaussian and os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1":
        upfront_task_quote_banner = '<section style="margin-bottom:18px;padding:16px;border:1px solid #607b39;border-radius:10px;background:#132017"><strong>Local COLMAP + OpenSplat · 0.00 €</strong><p>Photos and videos are reconstructed on this computer. Automatic mode uses a compatible GPU when available and falls back to CPU. See Processing settings in the account menu for the active engines. Local processing uses no cloud credits or storage quota. Finished Gaussian files can also be imported directly.</p></section>'
    format_pills_html = (
        """
        <span class="workflow-pill">Gaussian Splatting Task</span>
        <span class="workflow-pill">Direct Splat (.PLY / .SPLAT / .SPZ / .KSPLAT) or Video/Image Set (.MP4 / .MOV / .ZIP)</span>
        <span class="workflow-pill">Output stored in Survey Task subfolder</span>
        """
        if is_gaussian
        else """
        <span class="workflow-pill">Direct File Import Task · Free (0 €)</span>
        <span class="workflow-pill">Orthophoto / DSM (.TIF), 3D Mesh (.OBJ / .FBX / .GLB), Point Cloud (.LAS / .LAZ / .E57), Splat (.PLY / .SPLAT)</span>
        <span class="workflow-pill">Auto-converted to Web 3D Tiles / COG</span>
        """
    )

    head_extra = """
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script defer src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <link rel="stylesheet" href="/assets/css/Projects.css?v=7" />
    <link rel="stylesheet" href="/assets/css/Project.css?v=7" />
    <link rel="stylesheet" href="/assets/css/external-asset-import.css?v=12" />
    <script defer src="/js/front/AssetUpload.js?v=20261113-unified"></script>
    <style>
        [data-asset-upload] input[type=date] { color-scheme: dark; }
        [data-asset-upload] button[type=submit]:disabled { opacity:1; color:#acbbb0!important; background:#253328!important; border-color:#48614f!important; }
        #taskUploadWorkspace code { color: #ff8ac4; }
        #asset-upload-status, #asset-upload-selection { color: #dce6dc; }
        #taskUploadWorkspace .workflow-pill { color:#dce6dc; background:#19251d; border:1px solid #48614f; border-radius:6px; display:inline-block; padding:6px 10px; margin:3px; font-size:12px; }
        .me-unified-dropzone { border:2px dashed rgba(153,204,0,0.55); border-radius:11px; padding:16px; background:#0e1511; transition:border-color 0.16s ease, background 0.16s ease; }
        .me-unified-dropzone.is-dragover { border-color:#99CC00; background:rgba(153,204,0,0.10); }
        .me-upload-state-locked { opacity:0.62; pointer-events:none; }
    </style>"""

    geovideo_link_card_html = ""
    if not is_gaussian:
        geovideo_link_card_html = f"""
                            <details id="geovideo-link-box" style="margin-top:16px;background:#101713;border:1px solid rgba(245,158,11,0.38);border-radius:12px;padding:15px 20px;">
                                <summary style="cursor:pointer;font-size:12.5px;font-weight:800;color:#fbbf24;letter-spacing:0.03em;list-style:none;display:flex;justify-content:space-between;align-items:center;">
                                    <span>&#127916; Or Link a 0-MB External Geo-Video (YouTube / Vimeo / Direct MP4 URL + DJI .SRT / .GPX / .CSV)</span>
                                    <span style="font-size:11px;color:#99CC00;font-weight:700;">0 MB Storage Quota</span>
                                </summary>
                                <form id="geovideo-link-form" style="margin-top:14px;">
                                    <input type="hidden" name="project_id" value="{project_id}" />
                                    <div class="row g-3">
                                        <div class="col-md-6">
                                            <label style="font-size:11.5px;color:#cbd5e1;font-weight:600;margin-bottom:4px;display:block;">YouTube, Vimeo, or Direct HTTPS .MP4 URL *</label>
                                            <input type="url" name="external_video_url" required class="form-control" style="background:#161c18;border:1px solid #f59e0b;color:#fff;font-size:13px;" placeholder="https://www.youtube.com/watch?v=... or https://vimeo.com/..." />
                                        </div>
                                        <div class="col-md-6">
                                            <label style="font-size:11.5px;color:#cbd5e1;font-weight:600;margin-bottom:4px;display:block;">Telemetry File (.SRT, .GPX, .CSV) &mdash; optional</label>
                                            <input type="file" name="telemetry_file" accept=".srt,.gpx,.csv" class="form-control" style="background:#161c18;border:1px solid rgba(255,255,255,0.18);color:#fff;font-size:13px;" />
                                        </div>
                                        <div class="col-md-5">
                                            <label style="font-size:11.5px;color:#cbd5e1;font-weight:600;margin-bottom:4px;display:block;">Video Display Title</label>
                                            <input type="text" name="title" class="form-control" style="background:#161c18;border:1px solid rgba(255,255,255,0.16);color:#fff;font-size:13px;" placeholder="e.g. Ridge FPV Flight + DJI SRT" />
                                        </div>
                                        <div class="col-md-3">
                                            <label style="font-size:11.5px;color:#cbd5e1;font-weight:600;margin-bottom:4px;display:block;">Capture Date</label>
                                            <input type="date" name="capture_date" value="{pdate}" class="form-control" style="background:#161c18;border:1px solid rgba(255,255,255,0.16);color:#fff;font-size:13px;" />
                                        </div>
                                        <div class="col-md-2">
                                            <label style="font-size:11.5px;color:#cbd5e1;font-weight:600;margin-bottom:4px;display:block;">Fallback Lat</label>
                                            <input type="text" name="lat" value="{plat}" class="form-control" style="background:#161c18;border:1px solid rgba(255,255,255,0.16);color:#fff;font-size:13px;" />
                                        </div>
                                        <div class="col-md-2">
                                            <label style="font-size:11.5px;color:#cbd5e1;font-weight:600;margin-bottom:4px;display:block;">Fallback Lon</label>
                                            <input type="text" name="lon" value="{plon}" class="form-control" style="background:#161c18;border:1px solid rgba(255,255,255,0.16);color:#fff;font-size:13px;" />
                                        </div>
                                    </div>
                                    <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mt-3">
                                        <span id="geovideo-link-status" style="font-size:12px;color:#dce6dc;"></span>
                                        <button type="submit" class="btn btn-primary btn-sm" style="font-weight:700;padding:8px 18px;">
                                            &#127916; REGISTER 0-MB GEO-VIDEO &amp; 3D TRACK
                                        </button>
                                    </div>
                                </form>
                                <script>
                                (function(){{
                                    var gf = document.getElementById('geovideo-link-form');
                                    if (!gf) return;
                                    gf.addEventListener('submit', async function(ev){{
                                        ev.preventDefault();
                                        var st = document.getElementById('geovideo-link-status');
                                        var btn = gf.querySelector('button[type="submit"]');
                                        btn.disabled = true;
                                        st.textContent = 'Parsing telemetry & registering 3D Geo-Video...';
                                        try {{
                                            var fd = new FormData(gf);
                                            var r = await fetch('/api/v2/geovideo/create', {{ method: 'POST', body: fd, credentials: 'same-origin' }});
                                            var data = await r.json();
                                            if (!r.ok || !data.ok) throw new Error((data && (data.detail || data.error)) || 'Could not register Geo-Video.');
                                            st.textContent = 'Geo-Video registered (100%)! Returning to MyEarth...';
                                            window.location.href = '{myearth_return_url}';
                                        }} catch (err) {{
                                            st.textContent = err.message || 'Error registering Geo-Video.';
                                            btn.disabled = false;
                                        }}
                                    }});
                                }})();
                                </script>
                            </details>"""

    zip_migration_banner_html = ""
    if not is_gaussian:
        zip_migration_banner_html = f"""<div class="mb-3" style="background:rgba(153,204,0,0.07);border:1px solid rgba(153,204,0,0.32);border-radius:10px;padding:11px 15px;font-size:12.5px;color:#dce8e0;">
                                <strong style="color:#99CC00;"><i class="fas fa-file-archive me-1"></i> 1-Click Multi-Platform .ZIP Migration:</strong>
                                Drop a full project export <code>.zip</code> from <strong>DJI Terra</strong>, <strong>PIX4D</strong>, <strong>Polycam</strong>, <strong>Luma AI</strong>, <strong>DroneDeploy</strong>, <strong>Metashape</strong>, or <strong>WebODM</strong> in the file selector below &mdash; MosaicEarth automatically unpacks and registers every Orthomosaic, DSM, 3D Mesh, Point Cloud/Splat, Vector layer, and Quality Report PDF into this Survey!
                            </div>
                            <div id="import-quick-modes" class="mb-3" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:8px;">
                                <button type="button" data-import-preset="all" data-accept="{accepted_exts}" class="btn btn-sm" style="text-align:left;padding:9px 12px;border-radius:9px;background:#1b2a1f;border:1.5px solid #99CC00;color:#eff8e9;font-weight:700;font-size:12px;">
                                    &#128506; All GIS, 3D Scans &amp; .ZIP<br/><span style="font-weight:400;font-size:10.5px;color:#b7cbbd;">GeoTIFF, LAZ, GLB/OBJ/USDZ, Vector, ZIP</span>
                                </button>
                                <button type="button" data-import-preset="pano" data-accept=".jpg,.jpeg,.webp,.png,.jgw,.pgw,.wld" class="btn btn-sm" style="text-align:left;padding:9px 12px;border-radius:9px;background:#121915;border:1px solid rgba(255,255,255,0.16);color:#dce6dc;font-weight:700;font-size:12px;">
                                    &#127760; 360&deg; Panoramas &amp; EXIF Photos<br/><span style="font-weight:400;font-size:10.5px;color:#9eb2a5;">1 or multiple .JPG/.WEBP (auto Virtual Tour)</span>
                                </button>
                                <button type="button" data-import-preset="video_file" data-accept=".mp4,.mov,.webm,.m4v,.srt,.gpx,.csv" class="btn btn-sm" style="text-align:left;padding:9px 12px;border-radius:9px;background:#121915;border:1px solid rgba(255,255,255,0.16);color:#dce6dc;font-weight:700;font-size:12px;">
                                    &#127909; Hosted Geo-Video File<br/><span style="font-weight:400;font-size:10.5px;color:#9eb2a5;">Select .MP4/.MOV + .SRT/.GPX together</span>
                                </button>
                                <button type="button" data-import-preset="video_link" class="btn btn-sm" style="text-align:left;padding:9px 12px;border-radius:9px;background:#121915;border:1px solid rgba(245,158,11,0.45);color:#fde68a;font-weight:700;font-size:12px;">
                                    &#127916; 0-MB YouTube / Vimeo Link<br/><span style="font-weight:400;font-size:10.5px;color:#e5c98a;">External video URL + .SRT/.GPX (0 GiB)</span>
                                </button>
                            </div>
                            <div id="import-preset-hint" class="mb-3" style="display:none;padding:9px 13px;border-radius:8px;background:#132018;border-left:3px solid #99CC00;color:#dce8e0;font-size:12px;"></div>
                            <script>
                            (function(){{
                                var wrap = document.getElementById('import-quick-modes');
                                if (!wrap) return;
                                var fileInp = document.getElementById('upload-file');
                                var hintEl = document.getElementById('import-preset-hint');
                                var gvBox = document.getElementById('geovideo-link-box');
                                var hints = {{
                                    all: '',
                                    pano: 'Select one or multiple 360° equirectangular panoramas (DJI, Insta360, Ricoh Theta) or geotagged drone photos (.JPG/.WEBP/.PNG). EXIF GPS, altitude, and gimbal orientation are extracted automatically!',
                                    video_file: 'Select your drone or action-cam video (.MP4 / .MOV / .WEBM) — hold Ctrl/Cmd to also select its companion DJI .SRT, .GPX, or .CSV flight log at the same time.',
                                    video_link: 'Paste your YouTube, Vimeo, or HTTPS .MP4 URL in the 0-MB Geo-Video panel below and attach an optional .SRT / .GPX telemetry track.'
                                }};
                                function activatePreset(key, scrollToLink) {{
                                    var btns = wrap.querySelectorAll('[data-import-preset]');
                                    btns.forEach(function(b){{
                                        var active = b.getAttribute('data-import-preset') === key;
                                        b.style.background = active ? '#1b2a1f' : '#121915';
                                        b.style.borderColor = active ? '#99CC00' : (b.getAttribute('data-import-preset') === 'video_link' ? 'rgba(245,158,11,0.45)' : 'rgba(255,255,255,0.16)');
                                    }});
                                    var targetBtn = wrap.querySelector('[data-import-preset="' + key + '"]');
                                    if (fileInp && targetBtn && targetBtn.getAttribute('data-accept')) {{
                                        fileInp.setAttribute('accept', targetBtn.getAttribute('data-accept'));
                                    }}
                                    if (hintEl) {{
                                        var msg = hints[key] || '';
                                        hintEl.textContent = msg;
                                        hintEl.style.display = msg ? 'block' : 'none';
                                    }}
                                    if (key === 'video_link' && gvBox) {{
                                        gvBox.open = true;
                                        if (scrollToLink) gvBox.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                                    }}
                                }}
                                wrap.addEventListener('click', function(ev){{
                                    var b = ev.target && ev.target.closest && ev.target.closest('[data-import-preset]');
                                    if (!b) return;
                                    activatePreset(b.getAttribute('data-import-preset'), true);
                                }});
                                try {{
                                    var sp = new URLSearchParams(window.location.search);
                                    if (sp.get('focus') === 'media') {{
                                        if (gvBox) gvBox.open = true;
                                        activatePreset('pano', false);
                                    }}
                                }} catch (e) {{}}
                            }})();
                            </script>"""

    body_content = f"""
    <section id="taskUploadWorkspace" class="workspace-section pt-4">
        <div class="container">
            <div class="row mb-3">
                <div class="col-12">
                    <div class="box-4d" style="padding:14px 20px;background:#111512;border:1px solid rgba(153,204,0,0.35);border-radius:10px;">
                        <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
                            <div>
                                <span style="font-size:10.5px;font-weight:700;color:#99CC00;text-transform:uppercase;letter-spacing:0.06em;">MYEARTH TASK WORKSPACE (PROJECT &rarr; SURVEY &rarr; TASK &rarr; ASSETS)</span>
                                <div style="font-size:15.5px;font-weight:700;color:#fff;margin-top:2px;">
                                    {root_pname} <span style="color:#8fa099;font-weight:400;">/</span> {survey_pname} <span style="color:#8fa099;font-weight:400;">/</span> <span style="color:#99CC00;">{pname}</span>
                                </div>
                            </div>
                            <div class="d-flex flex-wrap align-items-center gap-2">
                                <a href="/{norm_lang}/Projects?open_project={root_pid}#taskNode_{project_id}" class="btn btn-primary btn-sm" style="font-weight:700;text-decoration:none;">
                                    &larr; BACK TO MYEARTH TREE
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="row">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
                                <div>{pname} &middot; <span style="color:#99CC00;">{task_badge_label}</span></div>
                                <span style="font-size:11.5px;color:#b7c4bc;">Task ID #{project_id}</span>
                            </div>
                        </div>
                        <div class="content-box" style="padding:22px 26px;">
                            <div class="project-workflow-summary mb-3">
                                {format_pills_html}
                            </div>

                            {upfront_task_quote_banner}

                            {cancel_gaussian_html}

                            {existing_block}

                            {zip_migration_banner_html}

                            <form data-asset-upload data-myearth-url="{myearth_return_url}" data-gcp-count="{gcp_attached_count}" data-latest-job="{int(latest.get('ID') or 0)}" data-latest-asset="{int(latest.get('ASSET_ID') or 0)}" action="/ExternalAssetImportUpload.ashx" method="post" enctype="multipart/form-data" style="background:#111613;border:1px solid rgba(255,255,255,0.12);border-radius:12px;padding:20px;">
                                <input type="hidden" name="project_id" value="{project_id}" />
                                <input type="hidden" name="source_origin" value="{'gaussian' if is_gaussian else 'imported'}" />
                                {f'''<input type="hidden" id="gaussianQuality" name="quality_preset" value="HIGH_QUALITY" />
                                <div class="mb-3" style="padding:12px 15px;background:#0e1712;border:1px solid rgba(153,204,0,0.35);border-radius:10px;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:10px;">
                                    <div>
                                        <div style="font-size:11.5px;font-weight:800;color:#99CC00;text-transform:uppercase;letter-spacing:0.05em;">
                                            <i class="fas fa-sparkles me-1"></i> Maximum Quality Always Active (COLMAP + OpenSplat &middot; 15,000 Iterations &middot; 2048px)
                                        </div>
                                        <div style="font-size:12px;color:#cbd5e1;margin-top:2px;">
                                            Photos &amp; videos (plus optional <code>.SRT / .GPX / .CSV</code> or GCPs) reconstruct into <code>.PLY</code>, compressed <code>.SPZ</code>, and Cesium <code>3D Tiles</code>. Pre-trained <code>.PLY / .SPLAT / .SPZ / .KSPLAT</code> (or <code>.ZIP</code>) import at <strong>0,00 &euro;</strong>.
                                        </div>
                                    </div>
                                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                                        <span id="gaussianGcpBadge" style="font-size:11.5px;padding:4px 10px;border-radius:999px;background:{'rgba(153,204,0,0.18)' if gcp_attached_count >= 3 else '#141d17'};border:1px solid {'#99CC00' if gcp_attached_count >= 3 else '#35483c'};color:{'#d9f99d' if gcp_attached_count >= 3 else '#9eb2a5'};font-weight:700;">
                                            {f'✓ {gcp_attached_count} Metric GCPs Attached' if gcp_attached_count >= 3 else 'Optional Metric GCPs'}
                                        </span>
                                        <button type="button" id="btnOpenGaussianGcpStudio" class="btn btn-sm" style="background:rgba(153,204,0,0.15);border:1px solid #99CC00;color:#99CC00;font-weight:700;font-size:11.5px;padding:6px 12px;">
                                            &#8982; Open MosaicEarth GCP Studio
                                        </button>
                                    </div>
                                </div>''' if is_gaussian else ''}

                                <div style="font-size:12px;font-weight:700;color:#99CC00;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:14px;">
                                    <i class="fas fa-cloud-upload-alt me-1"></i> Upload &amp; Register Asset into "{pname}"
                                </div>

                                <div id="unifiedUploadFieldsWrap" class="row g-3">
                                    <div class="col-md-7">
                                        <div id="unifiedDropzone" class="me-unified-dropzone">
                                            <label for="upload-file" style="font-size:12.5px;color:#fff;font-weight:700;margin-bottom:6px;display:block;cursor:pointer;">
                                                <i class="fas fa-file-upload me-1" style="color:#99CC00;"></i> Drag &amp; Drop or Select File(s) to Upload *
                                            </label>
                                            <input type="file" id="upload-file" name="file" required multiple accept="{accepted_exts}" class="form-control" style="background:#161c18;border:1px solid #99CC00;color:#fff;font-size:13px;" />
                                            <small style="color:#8fa099;font-size:11px;display:block;margin-top:6px;">Supported: {accepted_exts}</small>
                                        </div>
                                    </div>
                                    <div class="col-md-5">
                                        <label for="upload-title" style="font-size:11.5px;color:#cbd5e1;font-weight:600;margin-bottom:5px;display:block;">Asset Display Name (optional, defaults to filename)</label>
                                        <input type="text" id="upload-title" name="title" class="form-control mb-2" style="background:#161c18;border:1px solid rgba(255,255,255,0.16);color:#fff;font-size:13px;" placeholder="e.g. {pname} - Main Layer" />
                                        <div class="row g-2">
                                            <div class="col-6">
                                                <label for="upload-capture_date" style="font-size:11.5px;color:#cbd5e1;font-weight:600;margin-bottom:4px;display:block;">Capture Date</label>
                                                <input type="date" id="upload-capture_date" name="capture_date" value="{pdate}" class="form-control" style="background:#161c18;border:1px solid rgba(255,255,255,0.16);color:#fff;font-size:12.5px;" />
                                            </div>
                                            <div class="col-6">
                                                <label for="upload-capture_method" style="font-size:11.5px;color:#cbd5e1;font-weight:600;margin-bottom:4px;display:block;">Methodology / Sensor</label>
                                                <input type="text" id="upload-capture_method" name="capture_method" value="{pmethod}" class="form-control" style="background:#161c18;border:1px solid rgba(255,255,255,0.16);color:#fff;font-size:12.5px;" placeholder="e.g. DJI RTK / LiDAR" />
                                            </div>
                                        </div>
                                    </div>

                                    <div class="col-12">
                                        <div style="background:#0e1511;border:1px solid #2b3c32;border-radius:10px;padding:13px 15px;">
                                            <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px;">
                                                <div>
                                                    <span style="font-size:12px;font-weight:700;color:#fff;"><i class="fas fa-map-marked-alt me-1" style="color:#99CC00;"></i> Georeferencing &amp; Map Location</span>
                                                    <span id="georefStatusBadge" style="margin-left:8px;font-size:11px;padding:3px 9px;border-radius:999px;background:#18241d;border:1px solid #3c5244;color:#cbd5e1;font-weight:700;">
                                                        {'✓ Initial coordinates set' if (plat and plon) else 'Select files to auto-detect GPS/CRS or click the map'}
                                                    </span>
                                                </div>
                                                <div style="display:flex;gap:6px;align-items:center;min-width:240px;">
                                                    <input type="text" id="mapPlaceSearchInput" class="form-control form-control-sm" style="background:#161c18;border:1px solid #35483c;color:#fff;font-size:12px;" placeholder="Search city / address on map..." />
                                                    <button type="button" id="btnMapPlaceSearch" class="btn btn-sm btn-secondary" style="font-size:11.5px;white-space:nowrap;">Find</button>
                                                </div>
                                            </div>
                                            <div id="uploadLocationMap" style="width:100%;height:230px;border-radius:8px;border:1px solid #29382f;background:#0a0f0c;margin-bottom:10px;"></div>
                                            <div class="row g-2 align-items-end">
                                                <div class="col-md-3 col-6">
                                                    <label for="upload-lat" style="font-size:11px;color:#cbd5e1;font-weight:600;margin-bottom:3px;display:block;">Latitude (WGS84)</label>
                                                    <input type="text" id="upload-lat" name="lat" value="{plat}" placeholder="Click map or auto-detected" class="form-control form-control-sm" style="background:#161c18;border:1px solid rgba(255,255,255,0.18);color:#fff;font-size:12.5px;" />
                                                </div>
                                                <div class="col-md-3 col-6">
                                                    <label for="upload-lon" style="font-size:11px;color:#cbd5e1;font-weight:600;margin-bottom:3px;display:block;">Longitude (WGS84)</label>
                                                    <input type="text" id="upload-lon" name="lon" value="{plon}" placeholder="Click map or auto-detected" class="form-control form-control-sm" style="background:#161c18;border:1px solid rgba(255,255,255,0.18);color:#fff;font-size:12.5px;" />
                                                </div>
                                                <div class="col-md-6 col-12">
                                                    <small id="georefHelperCopy" style="color:#9eb2a5;font-size:11.5px;display:block;line-height:1.4;">
                                                        Georeferenced files (GeoTIFF, LAS/LAZ, GeoJSON/KML, EXIF GPS photos, SRT/GPX video) place their pin automatically. For unreferenced 3D models/scans, <strong>click anywhere on the map</strong> to set an approximate spot (you can fine-tune X, Y, Z &amp; rotation later in MyEarth &rarr; Adjust Placement).
                                                    </small>
                                                </div>
                                            </div>
                                        </div>
                                    </div>

                                    <div class="col-12">
                                        <label for="upload-description" style="font-size:11.5px;color:#cbd5e1;font-weight:600;margin-bottom:5px;display:block;">Task / Asset Description</label>
                                        <textarea id="upload-description" name="description" rows="2" class="form-control" style="background:#161c18;border:1px solid rgba(255,255,255,0.16);color:#fff;font-size:13px;">{pdesc}</textarea>
                                    </div>
                                </div>

                                <p id="asset-upload-selection" class="mt-3"></p>
                                <progress id="asset-upload-progress" class="w-100" aria-label="File upload progress" hidden></progress>
                                <p id="asset-upload-status" role="status" aria-live="polite" class="mt-2"></p>
                                <a id="asset-upload-result" class="btn btn-primary" hidden>Open result</a>
                                <button type="button" id="asset-upload-pause" class="btn btn-secondary" hidden>Pause upload</button>
                                <button type="button" id="asset-upload-cancel" class="btn btn-secondary" hidden>Cancel upload</button>
                                <button type="button" id="asset-upload-retry" class="btn btn-primary" hidden>Retry using saved originals</button>
                                <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mt-4">
                                    <a class="btn btn-secondary" href="/{norm_lang}/Projects?open_project={root_pid}#taskNode_{project_id}">&larr; BACK TO MYEARTH</a>
                                    <button type="submit" class="btn btn-primary" style="font-weight:700;padding:9px 22px;">
                                        <i class="fas fa-upload me-1"></i> {'UPLOAD & GENERATE GAUSSIAN SPLAT' if is_gaussian else 'UPLOAD & IMPORT ASSET TO TASK'}
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </section>"""
    return render_home_master(head_extra, seo_head, body_content, "<script>CargarActiveMenu('li_projects');</script>", user=user, lang=norm_lang, wrap_form=False)


def render_gaussian_project_page(user: Dict[str, Any], project_id: int, lang: str = "en") -> Optional[str]:
    return _render_unified_task_upload_page(user, project_id, task_kind="gaussian", lang=lang)


def render_external_asset_import_page(user: Dict[str, Any], project_id: int, lang: str = "en") -> Optional[str]:
    return _render_unified_task_upload_page(user, project_id, task_kind="imported", lang=lang)


def render_internal_viewer_page(
    user: Optional[Dict[str, Any]],
    project_id: int,
    file_type_id: int = 3,
    mode: str = "view",
    pub_id: int = 0,
    asset_id: int = 0,
) -> Optional[str]:
    """Renders /Map.aspx, /MapView.aspx, /MapEdit.aspx (3-Axis Placement), or /MapAnnotations.aspx (3D Annotations) for both processed and external/Gaussian Splat assets."""
    store = get_store()
    proj = store.get_project(project_id)
    ext_asset = store.get_external_asset(project_id, asset_id) if asset_id > 0 else None
    if asset_id > 0:
        from .asset_uploads import asset_output_available
        if not ext_asset or not asset_output_available(ext_asset):
            return None
    if not proj and not ext_asset:
        return None
    root_pid, _ = store.get_subprojects_family(project_id) if proj else (project_id, [project_id])
    pname = (
        str(ext_asset.get("TITLE") or ext_asset.get("DISPLAY_NAME") or f"Asset #{asset_id}")
        if ext_asset
        else str((proj or {}).get("NAME") or f"Project #{project_id}")
    )
    folder = str((proj or {}).get("FOLDER") or "")
    seo_slug = str((proj or {}).get("SEO_URL_FRIENDLY") or project_id)
    owner_uid = int(
        (ext_asset.get("USER_ID") if ext_asset else None)
        or ((proj or {}).get("CREATE_ID") or (proj or {}).get("USER_ID") or 0)
    )
    uid_curr = int(user.get("ID") or 0) if user else 0
    is_admin = bool(user and (int(user.get("ROL_ID") or 0) == 1 or bool(user.get("IS_ADMIN")) or bool(user.get("IsAdmin"))))
    can_manage = bool(uid_curr > 0 and (uid_curr == owner_uid or is_admin))
    if mode == "edit" and not can_manage:
        return None

    back_href = f"/en/Projects?open_project={root_pid}" if can_manage else (f"/en/assets/{seo_slug}-{pub_id}" if pub_id > 0 else "/en/Map")
    back_label = "&larr; Back to MyEarth" if can_manage else "&larr; Back to Asset"

    loc = store.get_project_location(project_id) if proj else None
    from .asset_metadata import derivative_version
    derivative_v = derivative_version(ext_asset or {})
    if ext_asset:
        lat_val = float(ext_asset.get("LATITUDE") if ext_asset.get("LATITUDE") is not None else 41.8205)
        lon_val = float(ext_asset.get("LONGITUDE") if ext_asset.get("LONGITUDE") is not None else 1.8676)
        height_val = float(ext_asset.get("ALTITUDE") or 0.0)
        heading_val = float(ext_asset.get("HEADING") or 0.0)
        pitch_val = float(ext_asset.get("PITCH") or 0.0)
        roll_val = float(ext_asset.get("ROLL") or 0.0)
        asset_key_str = f"external-{asset_id}"
        ext_vkind = str(ext_asset.get("VIEWER_KIND") or "").lower()
        ext_fmt = str(ext_asset.get("SOURCE_FORMAT") or ext_asset.get("FORMAT") or "glb").lower()
        is_ortho_2d = (str(ext_asset.get("ASSET_CLASS") or "").upper() in
                       ("RASTER_IMAGERY", "RASTER_GENERIC", "RASTER_ELEVATION") or
                       ext_vkind in ("raster", "tms", "orthophoto", "vector-raster") or ext_fmt in ("tif", "tiff"))
        is_vector_2d = ext_vkind == "geojson" or ext_fmt in ("geojson", "json", "kml", "kmz", "gpkg", "dxf", "shp", "gpx")
        url_tiles = f"/ExternalAsset3DTile.ashx/{project_id}/{asset_id}/tileset.json"
        url_layer = f"/ExternalAssetViewer.ashx?project_id={project_id}&asset_id={asset_id}&z={{z}}&x={{x}}&y={{y}}&v={derivative_v}"
        url_geojson = f"/ExternalAssetFile.ashx?project_id={project_id}&asset_id={asset_id}&mode=geojson"
    else:
        lat_val = float(str((proj or {}).get("LATITUD") or (loc.get("LATITUD") if loc else "41.9702")).replace(",", "."))
        lon_val = float(str((proj or {}).get("LONGITUD") or (loc.get("LONGITUD") if loc else "2.3885")).replace(",", "."))
        cms = store.get_cesium_models_by_project(project_id, file_type_id)
        cm = cms[0] if cms else None
        height_val = float(cm.get("Height") or 0.0) if cm else 0.0
        heading_val = float(cm.get("Heading") or 0.0) if cm else 0.0
        pitch_val = float(cm.get("Pitch") or 0.0) if cm else 0.0
        roll_val = float(cm.get("Roll") or 0.0) if cm else 0.0
        asset_key_str = f"processed-{file_type_id}-{project_id}"
        is_ortho_2d = (file_type_id == 2)
        is_vector_2d = False
        url_layer = f"/ProcessedAssetFile.ashx/{folder}/procesado/orthophoto_tiles/"
        contract = (proj or {}).get("CANONICAL_RESULT_CONTRACT") or {}
        tile_path = contract.get("pointcloud_visualization" if file_type_id == 1 else "mesh_visualization")
        tile_path = tile_path or (f"{folder}/procesado/3d_tiles/pointcloud/tileset.json" if file_type_id == 1 else f"{folder}/procesado/output/tileset.json")
        url_tiles = f"/ProcessedAssetFile.ashx/{tile_path}"
        url_geojson = ""

    cesium_key = store.get_cesium_browser_token()
    mode_title = "3D Placement & Rotation Studio" if mode == "edit" else ("3D Annotations Studio" if mode == "annotations" else "3D Viewer")
    seo_head = build_head_metadata_html(
        title=f"{pname} · {mode_title} · MosaicEarth",
        description=f"{mode_title} for {pname}",
        canonical_path=f"/MapView.aspx?id={project_id}&type={file_type_id}",
        robots="noindex, nofollow",
    )

    head_extra = """
    <link href="/vendor/cesium/Widgets/widgets.css" rel="stylesheet" />
    <script src="/vendor/cesium/Cesium.js"></script>
    <script src="/js/front/MosaicCesiumRuntime.js?v=__MOSAIC_MAP_RUNTIME_VERSION__"></script>
    <script src="/js/front/MosaicAnnotations.js?v=20261104"></script>
    <link rel="stylesheet" href="/css/MosaicAnnotations.css?v=20261104" />
    <link rel="stylesheet" href="/assets/css/mosaic-map-controls.css?v=7" />"""

    enable_annotations_js = "true" if mode == "annotations" else "false"
    enable_edit_js = "true" if (mode == "edit" and can_manage) else "false"
    is_external_js = "true" if asset_id > 0 else "false"

    viewer_js = f"""
    var token = {json.dumps(cesium_key)};
    Cesium.Ion.defaultAccessToken = token;
    var viewer = MosaicCesiumRuntime.createViewer('cesiumContainer', {{
        imageryProvider: MosaicCesiumRuntime.createBaseProvider('satellite'),
        timeline: false, animation: false, scene3DOnly: true, fullscreenButton: false,
        homeButton: false, sceneModePicker: false, baseLayerPicker: false,
        navigationHelpButton: false, geocoder: false, infoBox: false, selectionIndicator: false
    }}, token, {{requestVertexNormals:true,requestWaterMask:true}});
    window.viewer = viewer;
    var currentTileset = null;
    var baseOriginLat = {lat_val};
    var baseOriginLon = {lon_val};
    var baseOriginAlt = {height_val};
    var basePitchX = {pitch_val};
    var baseRollY = {roll_val};
    var baseHeadingZ = {heading_val};
    var currentLat = {lat_val};
    var currentLon = {lon_val};
    var currentAlt = {height_val};
    var currentPitchX = {pitch_val};
    var currentRollY = {roll_val};
    var currentHeadingZ = {heading_val};
    var initialEnuMatrix = null;
    var nativeOrigin = null;
    var referenceLat = {float(((proj or {}).get('CANONICAL_RESULT_CONTRACT') or {}).get('avg_lat', lat_val))};
    var referenceLon = {float(((proj or {}).get('CANONICAL_RESULT_CONTRACT') or {}).get('avg_lon', lon_val))};
    var placementPinEntity = null;
    var placementDropLineEntity = null;
    window.__clickMapPlacementActive = {enable_edit_js};

    function buildEnuRotMatrix(lonDeg, latDeg, altM, pitchXDeg, rollYDeg, headingZDeg) {{
        var mEnu = Cesium.Transforms.eastNorthUpToFixedFrame(
            Cesium.Cartesian3.fromDegrees(lonDeg, latDeg, altM)
        );
        var rx = Cesium.Math.toRadians(pitchXDeg || 0);
        var ry = Cesium.Math.toRadians(rollYDeg || 0);
        var rz = Cesium.Math.toRadians(headingZDeg || 0);
        var cx = Math.cos(rx), sx = Math.sin(rx);
        var cy = Math.cos(ry), sy = Math.sin(ry);
        var cz = Math.cos(rz), sz = Math.sin(rz);
        var rotLocal = new Cesium.Matrix3(
            cz * cy,  cz * sy * sx - sz * cx,  cz * sy * cx + sz * sx,
            sz * cy,  sz * sy * sx + cz * cx,  sz * sy * cx - cz * sx,
            -sy,      cy * sx,                 cy * cx
        );
        return Cesium.Matrix4.multiplyByMatrix3(mEnu, rotLocal, new Cesium.Matrix4());
    }}

    if ({enable_annotations_js} && window.MosaicAnnotations) {{
        MosaicAnnotations.register(viewer, {json.dumps(asset_key_str)}, {{
            getVisual: function() {{ return currentTileset; }},
            allowSurfacePicking: true
        }});
        setTimeout(function() {{
            var annBtn = document.querySelector('[data-annotation-asset]');
            if (annBtn) annBtn.click();
        }}, 650);
    }}

    function updatePlacementMarker(lon, lat, alt) {{
        var pos = Cesium.Cartesian3.fromDegrees(lon, lat, alt);
        var groundPos = Cesium.Cartesian3.fromDegrees(lon, lat, 0);
        if (!placementPinEntity) {{
            placementPinEntity = viewer.entities.add({{
                position: pos,
                point: {{ pixelSize: 12, color: Cesium.Color.fromCssColorString('#99CC00'), outlineColor: Cesium.Color.BLACK, outlineWidth: 2, disableDepthTestDistance: Number.POSITIVE_INFINITY }},
                label: {{ text: 'Asset Origin (X, Y, Z)', font: '700 12px sans-serif', fillColor: Cesium.Color.fromCssColorString('#99CC00'), style: Cesium.LabelStyle.FILL_AND_OUTLINE, outlineWidth: 3, verticalOrigin: Cesium.VerticalOrigin.BOTTOM, pixelOffset: new Cesium.Cartesian2(0, -14), disableDepthTestDistance: Number.POSITIVE_INFINITY }}
            }});
            placementDropLineEntity = viewer.entities.add({{
                polyline: {{
                    positions: [groundPos, pos],
                    width: 2,
                    material: new Cesium.PolylineDashMaterialProperty({{ color: Cesium.Color.fromCssColorString('#99CC00') }})
                }}
            }});
        }} else {{
            placementPinEntity.position = pos;
            if (placementDropLineEntity && placementDropLineEntity.polyline) {{
                placementDropLineEntity.polyline.positions = [groundPos, pos];
            }}
        }}
    }}

    if ({enable_edit_js}) {{
        updatePlacementMarker(currentLon, currentLat, currentAlt);
    }}

    var currentVectorDs = null;
    var currentRasterRectangle = null;
    window.locateCurrentAsset = function() {{
        if (currentTileset) {{
            MosaicCesiumRuntime.flyToTopDown(viewer, currentTileset, Math.max(currentTileset.boundingSphere.radius * 2.8, 3), 0.8);
        }} else if (currentVectorDs) {{
            MosaicCesiumRuntime.focusVectorDataSource(viewer, currentVectorDs, 0.8);
        }} else if (currentRasterRectangle) {{
            MosaicCesiumRuntime.flyToRectangleTopDown(viewer, currentRasterRectangle, 0.8);
        }} else {{
            viewer.camera.flyTo({{
                destination: Cesium.Cartesian3.fromDegrees(currentLon, currentLat, 1400),
                orientation: {{heading: 0, pitch: MosaicCesiumRuntime.topDownOffset(0).pitch, roll: 0}},
                duration: 0.8
            }});
        }}
    }};

    if ({'true' if is_ortho_2d else 'false'}) {{
        if ({is_external_js}) {{
            MosaicCesiumRuntime.loadXyzRaster(viewer, {json.dumps(url_layer)}, '/ExternalAssetFile.ashx?project_id={project_id}&asset_id={asset_id}&mode=raster_bounds&v={derivative_v}').then(function(layer) {{
                currentRasterRectangle = layer.imageryProvider.rectangle;
            }}).catch(function(error) {{ MosaicCesiumRuntime.showViewerError(viewer, error.message); }});
        }} else {{
            MosaicCesiumRuntime.loadTmsLayer(viewer, {json.dumps(url_layer)}, {{maximumLevel:22}}).then(function(layer) {{
                currentRasterRectangle = layer.imageryProvider.rectangle;
                window.locateCurrentAsset();
            }});
        }}
        window.locateCurrentAsset();
    }} else if ({'true' if is_vector_2d else 'false'}) {{
        Cesium.GeoJsonDataSource.load({json.dumps(url_geojson)}, {{stroke: Cesium.Color.fromCssColorString('#99CC00'), fill: Cesium.Color.fromCssColorString('#99CC00').withAlpha(0.35), strokeWidth: 3, clampToGround: true}}).then(async function(ds) {{
            await MosaicCesiumRuntime.prepareVectorDataSource(viewer, ds);
            currentVectorDs = ds;
            viewer.dataSources.add(ds);
            window.locateCurrentAsset();
        }}).catch(function(error) {{ MosaicCesiumRuntime.showViewerError(viewer, error.message); }});
    }} else {{
        MosaicCesiumRuntime.loadTileset(viewer, {json.dumps(url_tiles)}, {{maximumScreenSpaceError: 12}}).then(function(t){{
            currentTileset = t;
            if (t.root && t.root.transform && !Cesium.Matrix4.equals(t.root.transform, Cesium.Matrix4.IDENTITY)) {{
                initialEnuMatrix = Cesium.Matrix4.clone(t.root.transform);
            }} else {{
                var c0 = Cesium.Cartographic.fromCartesian(t.boundingSphere.center);
                initialEnuMatrix = Cesium.Transforms.eastNorthUpToFixedFrame(
                    Cesium.Cartesian3.fromRadians(c0.longitude, c0.latitude, 0.0)
                );
            }}
            if (!{is_external_js}) {{
                var originPosition = Cesium.Matrix4.getTranslation(initialEnuMatrix, new Cesium.Cartesian3());
                nativeOrigin = Cesium.Cartographic.fromCartesian(originPosition);
                initialEnuMatrix = Cesium.Transforms.eastNorthUpToFixedFrame(originPosition);
            }} else if ({'true' if ext_asset and ext_asset.get('ASSET_CLASS') == 'CESIUM_3D_TILES' and ext_asset.get('HAS_EMBEDDED_GEOREF') else 'false'}) {{
                // Preserve the native tileset's rotation and scale. Placement is
                // a delta from its saved ENU frame, not a replacement root matrix.
                initialEnuMatrix = buildEnuRotMatrix(currentLon, currentLat, currentAlt, currentPitchX, currentRollY, currentHeadingZ);
            }}
            window.applyLivePlacement3D();
            window.locateCurrentAsset();
        }});
    }}

    window.applyLivePlacement3D = function() {{
        var latEl = document.getElementById('latInput3D');
        var lonEl = document.getElementById('lonInput3D');
        var altEl = document.getElementById('altInput3D');
        var sliderEl = document.getElementById('heightSlider');
        var pitchEl = document.getElementById('pitchInput3D');
        var rollEl = document.getElementById('rollInput3D');
        var headingEl = document.getElementById('headingInput3D');
        var pitchSliderEl = document.getElementById('pitchSlider');
        var rollSliderEl = document.getElementById('rollSlider');
        var headingSliderEl = document.getElementById('headingSlider');

        if (latEl && latEl.value !== '') currentLat = parseFloat(latEl.value);
        if (lonEl && lonEl.value !== '') currentLon = parseFloat(lonEl.value);
        if (altEl && altEl.value !== '') currentAlt = parseFloat(altEl.value);
        if (pitchEl && pitchEl.value !== '') currentPitchX = parseFloat(pitchEl.value);
        if (rollEl && rollEl.value !== '') currentRollY = parseFloat(rollEl.value);
        if (headingEl && headingEl.value !== '') currentHeadingZ = parseFloat(headingEl.value);

        if (sliderEl) sliderEl.value = String(currentAlt);
        if (pitchSliderEl) pitchSliderEl.value = String(currentPitchX);
        if (rollSliderEl) rollSliderEl.value = String(currentRollY);
        if (headingSliderEl) headingSliderEl.value = String(currentHeadingZ);

        var hOut = document.getElementById('heightOut');
        if (hOut) hOut.innerText = currentAlt.toFixed(1) + ' m';
        var pOut = document.getElementById('pitchOut');
        if (pOut) pOut.innerText = currentPitchX.toFixed(1) + '°';
        var rOut = document.getElementById('rollOut');
        if (rOut) rOut.innerText = currentRollY.toFixed(1) + '°';
        var hdOut = document.getElementById('headingOut');
        if (hdOut) hdOut.innerText = currentHeadingZ.toFixed(1) + '°';

        if ({enable_edit_js}) {{
            updatePlacementMarker(currentLon, currentLat, currentAlt);
        }}

        if (currentTileset && initialEnuMatrix) {{
            var mOldInv = Cesium.Matrix4.inverse(initialEnuMatrix, new Cesium.Matrix4());
            var mNew = buildEnuRotMatrix(currentLon, currentLat, currentAlt, currentPitchX, currentRollY, currentHeadingZ);
            if (nativeOrigin) {{
                // Photogrammetry tiles already carry their measured elevation.
                // Stored Height is a manual offset, never an absolute altitude.
                mNew = buildEnuRotMatrix(Cesium.Math.toDegrees(nativeOrigin.longitude) + currentLon - referenceLon,
                    Cesium.Math.toDegrees(nativeOrigin.latitude) + currentLat - referenceLat,
                    nativeOrigin.height + currentAlt, currentPitchX, currentRollY, currentHeadingZ);
            }}
            var deltaM = Cesium.Matrix4.multiply(mNew, mOldInv, new Cesium.Matrix4());
            currentTileset.modelMatrix = deltaM;
            if (currentTileset.gaussianSplatPrimitive) {{
                var gsp = currentTileset.gaussianSplatPrimitive;
                gsp._dirty = true;
                if (gsp._drawCommand && gsp._rootTransform) {{
                    Cesium.Matrix4.multiply(deltaM, gsp._rootTransform, gsp._drawCommand.modelMatrix);
                }}
            }}
            viewer.scene.requestRender();
        }}
    }};

    window.nudgePlacementAxis = function(axis, metersDelta) {{
        var latEl = document.getElementById('latInput3D');
        var lonEl = document.getElementById('lonInput3D');
        var altEl = document.getElementById('altInput3D');
        if (axis === 'alt' && altEl) {{
            altEl.value = (parseFloat(altEl.value || 0) + metersDelta).toFixed(1);
        }} else if (axis === 'lat' && latEl) {{
            var dDegLat = metersDelta / 111320.0;
            latEl.value = (parseFloat(latEl.value || currentLat) + dDegLat).toFixed(6);
        }} else if (axis === 'lon' && lonEl) {{
            var cosLat = Math.max(0.1, Math.cos(currentLat * Math.PI / 180.0));
            var dDegLon = metersDelta / (111320.0 * cosLat);
            lonEl.value = (parseFloat(lonEl.value || currentLon) + dDegLon).toFixed(6);
        }}
        window.applyLivePlacement3D();
    }};

    function wrapAngle180(deg) {{
        var d = ((deg + 180.0) % 360.0 + 360.0) % 360.0 - 180.0;
        return Math.abs(d + 180.0) < 1e-6 ? 180.0 : d;
    }}

    window.nudgePlacementRotation = function(axis, degDelta) {{
        var elId = axis === 'pitch' ? 'pitchInput3D' : (axis === 'roll' ? 'rollInput3D' : 'headingInput3D');
        var el = document.getElementById(elId);
        if (!el) return;
        var nextDeg = wrapAngle180((parseFloat(el.value || 0) || 0) + degDelta);
        el.value = nextDeg.toFixed(1);
        window.applyLivePlacement3D();
    }};

    window.setPlacementRotation = function(axis, degVal) {{
        var elId = axis === 'pitch' ? 'pitchInput3D' : (axis === 'roll' ? 'rollInput3D' : 'headingInput3D');
        var el = document.getElementById(elId);
        if (!el) return;
        el.value = parseFloat(degVal || 0).toFixed(1);
        window.applyLivePlacement3D();
    }};

    window.resetAllRotations3D = function() {{
        ['pitchInput3D', 'rollInput3D', 'headingInput3D'].forEach(function(id) {{
            var el = document.getElementById(id);
            if (el) el.value = '0.0';
        }});
        window.applyLivePlacement3D();
    }};

    window.toggleClickMapPlacement = function() {{
        window.__clickMapPlacementActive = !window.__clickMapPlacementActive;
        var btn = document.getElementById('btnToggleClickMap');
        if (btn) {{
            btn.style.background = window.__clickMapPlacementActive ? '#99CC00' : '#17211b';
            btn.style.color = window.__clickMapPlacementActive ? '#070b09' : '#99CC00';
            btn.innerHTML = window.__clickMapPlacementActive
                ? '<i class="fas fa-crosshairs me-1"></i> Click on Map to Place: ACTIVE (Click anywhere on globe)'
                : '<i class="fas fa-crosshairs me-1"></i> Enable Click on Map to Place Asset';
        }}
    }};

    if ({enable_edit_js}) {{
        var clickHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
        clickHandler.setInputAction(function(click) {{
            if (!window.__clickMapPlacementActive) return;
            var cartesian = null;
            try {{
                var ray = viewer.camera.getPickRay(click.position);
                if (ray && viewer.scene && viewer.scene.globe) {{
                    cartesian = viewer.scene.globe.pick(ray, viewer.scene);
                }}
            }} catch (e) {{}}
            if (!Cesium.defined(cartesian)) {{
                cartesian = viewer.scene.pickPosition(click.position);
            }}
            if (!Cesium.defined(cartesian)) {{
                cartesian = viewer.camera.pickEllipsoid(click.position, viewer.scene.globe.ellipsoid);
            }}
            if (Cesium.defined(cartesian)) {{
                var carto = Cesium.Cartographic.fromCartesian(cartesian);
                var pickedLon = Cesium.Math.toDegrees(carto.longitude);
                var pickedLat = Cesium.Math.toDegrees(carto.latitude);
                var latEl = document.getElementById('latInput3D');
                var lonEl = document.getElementById('lonInput3D');
                if (latEl) latEl.value = pickedLat.toFixed(6);
                if (lonEl) lonEl.value = pickedLon.toFixed(6);
                window.applyLivePlacement3D();
                if (typeof toastr !== 'undefined') {{
                    toastr.info('Moved asset to ' + Math.abs(pickedLat).toFixed(5) + (pickedLat >= 0 ? '°N, ' : '°S, ') + Math.abs(pickedLon).toFixed(5) + (pickedLon >= 0 ? '°E' : '°W') + '. Adjust Position & Rotation and click Save.');
                }}
            }}
        }}, Cesium.ScreenSpaceEventType.LEFT_CLICK);
    }}

    window.setAssetViewerBaseLayer = function(mode) {{
        if (window.MosaicCesiumRuntime && MosaicCesiumRuntime.setBaseLayer) {{
            viewer._mosaicBaseImageryLayer = MosaicCesiumRuntime.setBaseLayer(viewer, mode, viewer._mosaicBaseImageryLayer);
        }} else if (window.MosaicCesiumRuntime && MosaicCesiumRuntime.createBaseProvider) {{
            var prev = viewer.imageryLayers.length ? viewer.imageryLayers.get(0) : null;
            var next = viewer.imageryLayers.addImageryProvider(MosaicCesiumRuntime.createBaseProvider(mode), 0);
            if (prev) viewer.imageryLayers.remove(prev, true);
            viewer.scene.requestRender();
        }}
        document.querySelectorAll('.base-layer-switch [data-base]').forEach(function(btn) {{
            btn.classList.toggle('active', btn.getAttribute('data-base') === mode);
        }});
    }};

    window.saveTilesetPlacement3D = function() {{
        var h = parseFloat((document.getElementById('altInput3D') && document.getElementById('altInput3D').value) || 0);
        var latV = parseFloat((document.getElementById('latInput3D') && document.getElementById('latInput3D').value) || {lat_val});
        var lonV = parseFloat((document.getElementById('lonInput3D') && document.getElementById('lonInput3D').value) || {lon_val});
        var pitchV = parseFloat((document.getElementById('pitchInput3D') && document.getElementById('pitchInput3D').value) || 0);
        var rollV = parseFloat((document.getElementById('rollInput3D') && document.getElementById('rollInput3D').value) || 0);
        var headingV = parseFloat((document.getElementById('headingInput3D') && document.getElementById('headingInput3D').value) || 0);
        if ({is_external_js}) {{
            fetch('/api/v2/external-asset/save-placement', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{
                    project_id: {project_id},
                    asset_id: {asset_id},
                    lat: latV,
                    lon: lonV,
                    alt: h,
                    pitch: pitchV,
                    roll: rollV,
                    heading: headingV
                }})
            }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
                if (d && d.ok) {{
                    if (typeof toastr !== 'undefined') toastr.success('3D Position (X, Y, Z) & Rotation (Rx, Ry, Rz) saved!');
                }} else {{
                    alert('Could not save placement.');
                }}
            }});
        }} else {{
            $.ajax({{
                type: 'POST',
                url: '/MapEdit.aspx/saveData',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                data: JSON.stringify({{
                    height: String(h),
                    lat: String(latV),
                    lon: String(lonV),
                    pitch: String(pitchV),
                    roll: String(rollV),
                    heading: String(headingV),
                    id: {project_id},
                    type: {file_type_id}
                }}),
                success: function() {{
                    if (typeof toastr !== 'undefined') toastr.success('3D Position (X, Y, Z) & Rotation (Rx, Ry, Rz) saved!');
                }}
            }});
        }}
    }};"""

    edit_bar = ""
    if mode == "edit" and can_manage:
        edit_bar = f"""
        <div style="position:absolute;top:70px;left:16px;z-index:999;background:rgba(14,20,17,0.96);border:1.5px solid #99CC00;padding:14px 16px;border-radius:10px;color:#fff;width:405px;max-width:calc(100vw - 32px);max-height:calc(100vh - 150px);overflow-y:auto;box-shadow:0 16px 40px rgba(0,0,0,0.65);">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                <strong style="font-size:13.5px;color:#99CC00;"><i class="fas fa-crosshairs me-1"></i> 3D Placement &amp; Rotation Studio</strong>
                <span style="font-size:11px;color:#9ca3af;">Creator / Admin</span>
            </div>
            <p style="font-size:11px;color:#cbd5e1;margin-bottom:8px;line-height:1.35;">
                <strong>1. Position (X, Y, Z):</strong> Click on the 3D Map or adjust coordinates &amp; altitude.<br/>
                <strong>2. Rotation (Rx, Ry, Rz):</strong> Upright tilted/sideways models or orient on map.
            </p>
            <button type="button" id="btnToggleClickMap" onclick="toggleClickMapPlacement()" class="btn btn-sm w-100 mb-2" style="background:#99CC00;color:#070b09;font-weight:700;font-size:11px;border:1px solid #99CC00;padding:5px 8px;">
                <i class="fas fa-crosshairs me-1"></i> Click on Map to Place: ACTIVE (Click anywhere on globe)
            </button>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px;">
                <div>
                    <label style="font-size:10.5px;color:#9ca3af;display:block;margin-bottom:2px;">X &middot; Longitude (East/West)</label>
                    <input id="lonInput3D" type="number" step="0.00001" value="{lon_val:.6f}" oninput="applyLivePlacement3D()" class="form-control form-control-sm" style="background:#111814;border:1px solid #3c4d42;color:#fff;font-size:11.5px;" />
                    <div class="d-flex gap-1 mt-1">
                        <button type="button" onclick="nudgePlacementAxis('lon', -10)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-10m</button>
                        <button type="button" onclick="nudgePlacementAxis('lon', -1)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-1m</button>
                        <button type="button" onclick="nudgePlacementAxis('lon', 1)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+1m</button>
                        <button type="button" onclick="nudgePlacementAxis('lon', 10)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+10m</button>
                    </div>
                </div>
                <div>
                    <label style="font-size:10.5px;color:#9ca3af;display:block;margin-bottom:2px;">Y &middot; Latitude (North/South)</label>
                    <input id="latInput3D" type="number" step="0.00001" value="{lat_val:.6f}" oninput="applyLivePlacement3D()" class="form-control form-control-sm" style="background:#111814;border:1px solid #3c4d42;color:#fff;font-size:11.5px;" />
                    <div class="d-flex gap-1 mt-1">
                        <button type="button" onclick="nudgePlacementAxis('lat', -10)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-10m</button>
                        <button type="button" onclick="nudgePlacementAxis('lat', -1)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-1m</button>
                        <button type="button" onclick="nudgePlacementAxis('lat', 1)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+1m</button>
                        <button type="button" onclick="nudgePlacementAxis('lat', 10)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+10m</button>
                    </div>
                </div>
            </div>
            <div style="margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid rgba(153,204,0,0.22);">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">
                    <label style="font-size:10.5px;color:#9ca3af;">Z &middot; Altitude / Height (<span id="heightOut">{height_val:.1f} m</span>)</label>
                    <input id="altInput3D" type="number" step="0.5" value="{height_val:.1f}" oninput="applyLivePlacement3D()" class="form-control form-control-sm" style="width:90px;background:#111814;border:1px solid #3c4d42;color:#fff;font-size:11.5px;" />
                </div>
                <input id="heightSlider" type="range" min="-200" max="500" step="0.5" value="{height_val}" class="form-range" style="height:1rem;" oninput="document.getElementById('altInput3D').value=this.value;applyLivePlacement3D();" />
                <div class="d-flex gap-1">
                    <button type="button" onclick="nudgePlacementAxis('alt', -10)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-10m Z</button>
                    <button type="button" onclick="nudgePlacementAxis('alt', -1)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-1m Z</button>
                    <button type="button" onclick="nudgePlacementAxis('alt', 1)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+1m Z</button>
                    <button type="button" onclick="nudgePlacementAxis('alt', 10)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:10px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+10m Z</button>
                </div>
            </div>

            <div style="margin-bottom:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                    <strong style="font-size:12px;color:#99CC00;"><i class="fas fa-sync-alt me-1"></i> 3D Asset Rotation (Rx / Ry / Rz)</strong>
                    <button type="button" onclick="resetAllRotations3D()" class="btn btn-sm" style="padding:1px 6px;font-size:10px;background:#1a241e;color:#9ca3af;border:1px solid #36493d;">Reset 0&deg;</button>
                </div>

                <div style="margin-bottom:7px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">
                        <label style="font-size:10.5px;color:#cbd5e1;">Rx &middot; Pitch / Tilt X (<span id="pitchOut">{pitch_val:.1f}&deg;</span>) <span style="color:#9ca3af;font-size:9.5px;">(Upright sideways models)</span></label>
                        <input id="pitchInput3D" type="number" step="0.5" min="-180" max="180" value="{pitch_val:.1f}" oninput="applyLivePlacement3D()" class="form-control form-control-sm" style="width:78px;background:#111814;border:1px solid #3c4d42;color:#fff;font-size:11px;padding:2px 5px;" />
                    </div>
                    <input id="pitchSlider" type="range" min="-180" max="180" step="0.5" value="{pitch_val}" class="form-range" style="height:0.9rem;" oninput="document.getElementById('pitchInput3D').value=this.value;applyLivePlacement3D();" />
                    <div class="d-flex gap-1">
                        <button type="button" onclick="nudgePlacementRotation('pitch', -90)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-90&deg;</button>
                        <button type="button" onclick="nudgePlacementRotation('pitch', -15)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-15&deg;</button>
                        <button type="button" onclick="setPlacementRotation('pitch', 0)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#99CC00;border:1px solid #36493d;">0&deg;</button>
                        <button type="button" onclick="nudgePlacementRotation('pitch', 15)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+15&deg;</button>
                        <button type="button" onclick="nudgePlacementRotation('pitch', 90)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+90&deg;</button>
                    </div>
                </div>

                <div style="margin-bottom:7px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">
                        <label style="font-size:10.5px;color:#cbd5e1;">Ry &middot; Roll / Tilt Y (<span id="rollOut">{roll_val:.1f}&deg;</span>) <span style="color:#9ca3af;font-size:9.5px;">(Lateral roll)</span></label>
                        <input id="rollInput3D" type="number" step="0.5" min="-180" max="180" value="{roll_val:.1f}" oninput="applyLivePlacement3D()" class="form-control form-control-sm" style="width:78px;background:#111814;border:1px solid #3c4d42;color:#fff;font-size:11px;padding:2px 5px;" />
                    </div>
                    <input id="rollSlider" type="range" min="-180" max="180" step="0.5" value="{roll_val}" class="form-range" style="height:0.9rem;" oninput="document.getElementById('rollInput3D').value=this.value;applyLivePlacement3D();" />
                    <div class="d-flex gap-1">
                        <button type="button" onclick="nudgePlacementRotation('roll', -90)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-90&deg;</button>
                        <button type="button" onclick="nudgePlacementRotation('roll', -15)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-15&deg;</button>
                        <button type="button" onclick="setPlacementRotation('roll', 0)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#99CC00;border:1px solid #36493d;">0&deg;</button>
                        <button type="button" onclick="nudgePlacementRotation('roll', 15)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+15&deg;</button>
                        <button type="button" onclick="nudgePlacementRotation('roll', 90)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+90&deg;</button>
                    </div>
                </div>

                <div style="margin-bottom:8px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">
                        <label style="font-size:10.5px;color:#cbd5e1;">Rz &middot; Heading / Yaw Z (<span id="headingOut">{heading_val:.1f}&deg;</span>) <span style="color:#9ca3af;font-size:9.5px;">(Compass rotation)</span></label>
                        <input id="headingInput3D" type="number" step="0.5" min="-180" max="180" value="{heading_val:.1f}" oninput="applyLivePlacement3D()" class="form-control form-control-sm" style="width:78px;background:#111814;border:1px solid #3c4d42;color:#fff;font-size:11px;padding:2px 5px;" />
                    </div>
                    <input id="headingSlider" type="range" min="-180" max="180" step="0.5" value="{heading_val}" class="form-range" style="height:0.9rem;" oninput="document.getElementById('headingInput3D').value=this.value;applyLivePlacement3D();" />
                    <div class="d-flex gap-1">
                        <button type="button" onclick="nudgePlacementRotation('heading', -90)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-90&deg;</button>
                        <button type="button" onclick="nudgePlacementRotation('heading', -15)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">-15&deg;</button>
                        <button type="button" onclick="setPlacementRotation('heading', 0)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#99CC00;border:1px solid #36493d;">0&deg;</button>
                        <button type="button" onclick="nudgePlacementRotation('heading', 15)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+15&deg;</button>
                        <button type="button" onclick="nudgePlacementRotation('heading', 90)" class="btn btn-sm flex-fill" style="padding:1px 3px;font-size:9.5px;background:#1a241e;color:#dce6dc;border:1px solid #36493d;">+90&deg;</button>
                    </div>
                </div>
            </div>

            <button type="button" class="btn btn-sm w-100" style="background:#99CC00;color:#070b09;font-weight:700;padding:8px;" onclick="saveTilesetPlacement3D()">
                <i class="fas fa-save me-1"></i> SAVE PLACEMENT &amp; ROTATION (X, Y, Z + Rx, Ry, Rz)
            </button>
        </div>"""

    annotations_top_btn = (
        f'<button type="button" data-annotation-asset="{asset_key_str}" class="btn btn-sm btn-secondary" style="padding:7px 14px;border-radius:6px;background:#17211b;border:1px solid #99CC00;color:#99CC00;font-weight:700;font-size:12px;"><i class="fas fa-map-pin me-1"></i> 3D Annotations Studio</button>'
        if mode == "annotations"
        else ""
    )

    fly_bar = f"""
    <div style="position:absolute;top:16px;left:16px;right:16px;z-index:999;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;pointer-events:none;">
        <div style="pointer-events:auto;display:flex;align-items:center;gap:8px;background:rgba(12,17,14,0.92);border:1px solid rgba(153,204,0,0.35);border-radius:8px;padding:6px 12px;">
            <a href="{back_href}" style="color:#dce6dc;font-size:12px;font-family:OpenSansSemiBold,Arial,sans-serif;text-decoration:none;">{back_label}</a>
            <span style="color:#45564b;">|</span>
            <strong style="color:#99CC00;font-size:12.5px;font-family:OpenSansBold,Arial,sans-serif;">{html.escape(pname)} &middot; {html.escape(mode_title)}</strong>
        </div>
        <div style="pointer-events:auto;display:flex;gap:8px;flex-wrap:wrap;">
            {annotations_top_btn}
        </div>
    </div>"""

    fly_control_btn = (
        f'<button type="button" onclick="window.location.href=\'/Fly.aspx?asset_id={html.escape(asset_key_str, quote=True)}&lat={lat_val}&lon={lon_val}\'" style="background:#99CC00;color:#070b09;font-weight:700;border-color:#99CC00;margin-left:6px;"><i class="fas fa-plane-departure me-1"></i> Fly View</button>'
        if mode == "view"
        else ""
    )
    base_controls_bar = f"""
    <button type="button" class="processed-locate mosaic-map-locate" onclick="locateCurrentAsset()" title="Center on Asset" aria-label="Center on Asset" style="position:absolute;right:14px;bottom:64px;z-index:999;width:42px;height:42px;border:1px solid #72bd8a;border-radius:50%;background:#183524;color:#dff7e7;display:grid;place-items:center;box-shadow:0 6px 18px rgba(0,0,0,.35);cursor:pointer;">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v3M12 19v3M2 12h3M19 12h3"></path></svg>
    </button>
    <div class="base-layer-switch" aria-label="Map controls">
        <div class="base-row">
            <button type="button" class="active" data-base="satellite" onclick="setAssetViewerBaseLayer('satellite')">Satellite</button>
            <button type="button" data-base="topographic" onclick="setAssetViewerBaseLayer('topographic')">Topographic</button>
            <button type="button" onclick="locateCurrentAsset()" style="border-color:#72bd8a;color:#91dab0;font-weight:700;margin-left:4px;" title="Center camera on this asset"><i class="fas fa-crosshairs me-1"></i> Center on Asset</button>
            {fly_control_btn}
        </div>
    </div>"""

    body_content = f"""
    <div class="container-fluid2" style="position:relative;height:calc(100vh - 60px);width:100%;">
        {fly_bar}
        {edit_bar}
        <div id="cesiumContainer" style="width:100%;height:100%;"></div>
        {base_controls_bar}
    </div>"""

    scripts_extra = f"<script>{viewer_js}</script>"
    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang="en")
