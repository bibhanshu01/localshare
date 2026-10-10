"""
LocalShare - Fast Offline Wi-Fi File Sharing App
Run on any Windows version to share files from PC to any device (Phone, Tablet, PC)
Zero Internet Required • Lightning Fast Local Wi-Fi Speed • Ultra Lightweight
"""

import os
import sys
import socket
import subprocess
import re
import threading
import mimetypes
import urllib.parse
import io
import zipfile
import json
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import qrcode

# Enable High-DPI awareness on Windows for crisp typography
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


# ----------------------------------------------------------------------
# Network Utilities
# ----------------------------------------------------------------------

def get_interface_ip(ifname):
    """Get the IPv4 address assigned specifically to a given interface name."""
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        out = subprocess.check_output(
            ['netsh', 'interface', 'ipv4', 'show', 'addresses', ifname],
            text=True,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo
        )
        m = re.search(r'IP Address\s*:\s*([0-9\.]+)', out)
        if m:
            ip = m.group(1).strip()
            if ip and not ip.startswith('127.'):
                return ip
    except Exception:
        pass
    return None


def detect_network_status():
    """
    Check if the PC is actively connected to a Wi-Fi or Hotspot network.
    Returns: (is_connected: bool, network_name: str, ip: str)
    """
    # 1. Primary Check: Wi-Fi connection via netsh wlan
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        out = subprocess.check_output(
            'netsh wlan show interfaces',
            shell=True,
            text=True,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo
        )
        state_match = re.search(r'^\s*State\s*:\s*(.+)$', out, re.MULTILINE | re.IGNORECASE)
        ssid_match = re.search(r'^\s*SSID\s*:\s*(.+)$', out, re.MULTILINE | re.IGNORECASE)
        name_match = re.search(r'^\s*Name\s*:\s*(.+)$', out, re.MULTILINE | re.IGNORECASE)

        if state_match:
            state = state_match.group(1).strip().lower()
            if state == 'connected' and ssid_match:
                ssid = ssid_match.group(1).strip()
                if ssid and ssid.lower() != 'none':
                    ifname = name_match.group(1).strip() if name_match else 'WiFi'
                    ip = get_interface_ip(ifname)
                    if ip:
                        return True, ssid, ip
    except Exception:
        pass

    # 2. Secondary Fallback: Physical Ethernet cable (ignoring virtual/hotspot loopbacks)
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        out = subprocess.check_output(
            'netsh interface show interface',
            shell=True,
            text=True,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo
        )
        for line in out.splitlines():
            line_clean = line.strip()
            if 'Connected' in line_clean and 'Ethernet' in line_clean:
                lower_l = line_clean.lower()
                if 'vethernet' in lower_l or 'vmware' in lower_l or 'virtual' in lower_l:
                    continue
                parts = re.split(r'\s{2,}', line_clean)
                if len(parts) >= 4:
                    eth_name = parts[3].strip()
                    ip = get_interface_ip(eth_name)
                    if ip:
                        return True, f"Ethernet ({eth_name})", ip
    except Exception:
        pass

    return False, "Not Connected", None


def open_network_shortcut():
    """Open Windows available network flyout or Wi-Fi settings."""
    try:
        subprocess.Popen(['cmd', '/c', 'start', 'ms-availablenetworks:'], shell=True)
    except Exception:
        try:
            subprocess.Popen(['cmd', '/c', 'start', 'ms-settings:network-wifi'], shell=True)
        except Exception:
            pass


def format_size(size_bytes):
    """Format bytes into readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def pick_files_or_folders_windows(hwnd=None):
    """
    Directly opens native Windows File & Folder Open Dialog.
    Allows user to select multiple files or folders seamlessly in one dialog.
    Returns list of absolute filesystem paths, or None on cancel/fallback.
    """
    try:
        import ctypes
        from ctypes import wintypes, POINTER, byref, c_void_p, c_wchar_p, c_ulong, Structure, HRESULT, WINFUNCTYPE, cast

        class GUID(Structure):
            _fields_ = [
                ('Data1', wintypes.DWORD),
                ('Data2', wintypes.WORD),
                ('Data3', wintypes.WORD),
                ('Data4', wintypes.BYTE * 8)
            ]

        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx.argtypes = [c_void_p, wintypes.DWORD]
        ole32.CoInitializeEx.restype = HRESULT
        try:
            ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        except Exception:
            pass

        CLSID_FileOpenDialog = '{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}'
        IID_IFileOpenDialog = '{D57C7288-D4AD-4768-BE02-9D969532D960}'

        clsid = GUID()
        iid = GUID()
        ole32.CLSIDFromString(CLSID_FileOpenDialog, byref(clsid))
        ole32.IIDFromString(IID_IFileOpenDialog, byref(iid))

        pDialog = c_void_p()
        hr = ole32.CoCreateInstance(byref(clsid), None, 1, byref(iid), byref(pDialog))
        if hr != 0:
            return None

        vDialog = cast(cast(pDialog, POINTER(c_void_p)).contents, POINTER(c_void_p))
        ReleaseDialog = WINFUNCTYPE(c_ulong, c_void_p)(vDialog[2])
        Show = WINFUNCTYPE(HRESULT, c_void_p, wintypes.HWND)(vDialog[3])
        SetOptions = WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD)(vDialog[9])
        GetOptions = WINFUNCTYPE(HRESULT, c_void_p, POINTER(wintypes.DWORD))(vDialog[10])
        SetTitle = WINFUNCTYPE(HRESULT, c_void_p, c_wchar_p)(vDialog[17])
        SetOkButtonLabel = WINFUNCTYPE(HRESULT, c_void_p, c_wchar_p)(vDialog[18])
        GetResults = WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_void_p))(vDialog[27])

        # Enable multi-select and allow existing items (files or folders)
        opts = wintypes.DWORD()
        GetOptions(pDialog, byref(opts))
        # FOS_ALLOWMULTISELECT (0x200) | FOS_FILEMUSTEXIST (0x1000) | FOS_PATHMUSTEXIST (0x800)
        SetOptions(pDialog, opts.value | 0x200 | 0x1000 | 0x800)
        SetTitle(pDialog, "Select File(s) or Folder to Share")
        SetOkButtonLabel(pDialog, "Add to Share")

        hr_show = Show(pDialog, hwnd if hwnd else None)
        if hr_show != 0:  # User cancelled (e.g. 0x800704C7)
            ReleaseDialog(pDialog)
            return []

        pArray = c_void_p()
        hr_res = GetResults(pDialog, byref(pArray))
        ReleaseDialog(pDialog)

        if hr_res != 0 or not pArray.value:
            return []

        vArray = cast(cast(pArray, POINTER(c_void_p)).contents, POINTER(c_void_p))
        ReleaseArray = WINFUNCTYPE(c_ulong, c_void_p)(vArray[2])
        GetCount = WINFUNCTYPE(HRESULT, c_void_p, POINTER(wintypes.DWORD))(vArray[7])
        GetItemAt = WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD, POINTER(c_void_p))(vArray[8])

        count = wintypes.DWORD()
        GetCount(pArray, byref(count))

        results = []
        for i in range(count.value):
            pItem = c_void_p()
            if GetItemAt(pArray, i, byref(pItem)) == 0 and pItem.value:
                vItem = cast(cast(pItem, POINTER(c_void_p)).contents, POINTER(c_void_p))
                ReleaseItem = WINFUNCTYPE(c_ulong, c_void_p)(vItem[2])
                GetDisplayName = WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD, POINTER(c_wchar_p))(vItem[5])

                pszPath = c_wchar_p()
                # SIGDN_FILESYSPATH = 0x80058000
                if GetDisplayName(pItem, 0x80058000, byref(pszPath)) == 0 and pszPath.value:
                    results.append(str(pszPath.value))
                    ole32.CoTaskMemFree(cast(pszPath, c_void_p))
                ReleaseItem(pItem)

        ReleaseArray(pArray)
        return results
    except Exception:
        return None


def truncate_name(name, max_len=36):
    """Truncate long filenames cleanly in the middle to prevent box leaking."""
    if len(name) <= max_len:
        return name
    base, ext = os.path.splitext(name)
    keep_base = max(10, max_len - len(ext) - 4)
    return f"{base[:keep_base]}...{ext}"


def get_file_icon(name):
    """Return an intuitive emoji icon based on file extension."""
    ext = os.path.splitext(name)[1].lower()
    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp']:
        return "🖼️"
    elif ext in ['.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v']:
        return "🎬"
    elif ext in ['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac']:
        return "🎵"
    elif ext in ['.pdf']:
        return "📕"
    elif ext in ['.txt', '.py', '.json', '.html', '.css', '.js', '.md', '.log', '.csv', '.xml']:
        return "📝"
    elif ext in ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.iso']:
        return "📦"
    elif ext in ['.doc', '.docx']:
        return "📘"
    elif ext in ['.xls', '.xlsx']:
        return "📗"
    elif ext in ['.ppt', '.pptx']:
        return "📙"
    return "📄"


# ----------------------------------------------------------------------
# Local Lightning HTTP Server
# ----------------------------------------------------------------------

class ResettableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class LocalShareHTTPHandler(BaseHTTPRequestHandler):
    """Custom HTTP Handler serving files directly over local Wi-Fi with high speed."""

    def log_message(self, format, *args):
        pass

    def send_error_response(self, code, message):
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.end_headers()
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Error {code}</title>
        <style>body{{background:#0b0f17;color:#f1f5f9;font-family:sans-serif;text-align:center;padding:50px;}}</style>
        </head><body><h2>Error {code}</h2><p>{message}</p><a href="/" style="color:#38bdf8;">Return Home</a></body></html>"""
        self.wfile.write(html.encode('utf-8'))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        shared_files = getattr(self.server, 'shared_files', [])
        server_info = getattr(self.server, 'server_info', {})
        version_id = getattr(self.server, 'version_id', 1)

        if path == '/' or path == '/index.html':
            self.serve_dashboard(shared_files, server_info, version_id)
        elif path == '/api/files':
            self.serve_api_files(shared_files, server_info, version_id)
        elif path == '/download':
            file_id = params.get('id', [None])[0]
            self.serve_file(file_id, shared_files, as_attachment=True)
        elif path == '/view':
            file_id = params.get('id', [None])[0]
            self.serve_file(file_id, shared_files, as_attachment=False)
        elif path == '/download-all':
            self.serve_all_as_zip(shared_files)
        else:
            self.send_error_response(404, "Page Not Found")

    def serve_api_files(self, shared_files, server_info, version_id):
        """API endpoint returning active files as JSON for live auto-updating."""
        total_size = sum(f['size'] for f in shared_files)
        data = {
            'version': version_id,
            'network_name': server_info.get('network_name', 'Local Wi-Fi'),
            'total_size': format_size(total_size),
            'total_files': len(shared_files),
            'files': [
                {
                    'id': f['id'],
                    'name': f['name'],
                    'size': format_size(f['size']),
                    'icon': get_file_icon(f['name']),
                    'can_preview': os.path.splitext(f['name'])[1].lower() in [
                        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',
                        '.mp4', '.mkv', '.webm', '.mov', '.mp3', '.wav', '.ogg',
                        '.m4a', '.flac', '.pdf', '.txt', '.py', '.json', '.html',
                        '.css', '.js', '.md', '.log', '.csv'
                    ]
                }
                for f in shared_files
            ]
        }
        json_bytes = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.send_header('Content-Length', str(len(json_bytes)))
        self.end_headers()
        self.wfile.write(json_bytes)

    def serve_dashboard(self, shared_files, server_info, version_id):
        total_size = sum(f['size'] for f in shared_files)
        total_size_str = format_size(total_size)
        total_files = len(shared_files)
        network_name = server_info.get('network_name', 'Local Wi-Fi')

        # Generate initial file rows
        rows_html = ""
        for f in shared_files:
            file_id = f['id']
            name = f['name']
            size_str = format_size(f['size'])
            ext = os.path.splitext(name)[1].lower()
            icon = get_file_icon(name)
            can_preview = ext in [
                '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',
                '.mp4', '.mkv', '.webm', '.mov', '.mp3', '.wav', '.ogg',
                '.m4a', '.flac', '.pdf', '.txt', '.py', '.json', '.html',
                '.css', '.js', '.md', '.log', '.csv'
            ]

            preview_btn = f'<a href="/view?id={file_id}" target="_blank" class="btn btn-secondary">👁️ View</a>' if can_preview else ''
            download_btn = f'<a href="/download?id={file_id}" class="btn btn-primary">⬇️ Download</a>'

            rows_html += f"""
            <div class="file-card" id="file-card-{file_id}">
                <div class="file-icon-box">{icon}</div>
                <div class="file-info">
                    <div class="file-title" title="{name}">{name}</div>
                    <div class="file-meta">
                        <span class="file-size-badge">{size_str}</span>
                        <span class="file-ext-badge">{ext.lstrip('.').upper() or 'FILE'}</span>
                    </div>
                </div>
                <div class="file-actions">
                    {preview_btn}
                    {download_btn}
                </div>
            </div>
            """

        zip_btn_html = ""
        if total_files > 1:
            zip_btn_html = '<a href="/download-all" class="btn btn-zip">📦 Download All (ZIP)</a>'

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <title>LocalShare - Fast File Transfer</title>
    <style>
        :root {{
            --bg-gradient: radial-gradient(circle at 50% 0%, #172033 0%, #0a0d14 70%);
            --card-bg: rgba(21, 28, 42, 0.72);
            --card-border: rgba(255, 255, 255, 0.08);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --accent-glow: rgba(56, 189, 248, 0.15);
            --emerald: #10b981;
            --emerald-bg: rgba(16, 185, 129, 0.12);
            --emerald-border: rgba(16, 185, 129, 0.28);
            --primary-btn: #0284c7;
            --primary-btn-hover: #0369a1;
            --secondary-btn: rgba(51, 65, 85, 0.6);
            --secondary-btn-hover: rgba(71, 85, 105, 0.8);
            --zip-btn: #059669;
            --zip-btn-hover: #047857;
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            -webkit-tap-highlight-color: transparent;
        }}
        body {{
            background: #0a0d14;
            background-image: var(--bg-gradient);
            background-attachment: fixed;
            color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            min-height: 100vh;
            padding: 20px 14px 40px;
            display: flex;
            justify-content: center;
        }}
        .container {{
            max-width: 620px;
            width: 100%;
            margin: 0 auto;
        }}
        /* Header Card */
        .header {{
            text-align: center;
            margin-bottom: 18px;
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            padding: 22px 18px;
            box-shadow: 0 12px 30px -10px rgba(0, 0, 0, 0.5);
        }}
        .logo {{
            font-size: 2rem;
            font-weight: 800;
            letter-spacing: -0.5px;
            background: linear-gradient(135deg, #38bdf8 20%, #a78bfa 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }}
        .tagline {{
            color: var(--text-muted);
            font-size: 0.88rem;
            margin-top: 4px;
        }}
        .badge-row {{
            display: flex;
            justify-content: center;
            gap: 8px;
            margin-top: 14px;
            flex-wrap: wrap;
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(56, 189, 248, 0.08);
            color: #38bdf8;
            border: 1px solid rgba(56, 189, 248, 0.22);
            border-radius: 999px;
            padding: 5px 12px;
            font-size: 0.78rem;
            font-weight: 600;
            max-width: 100%;
            word-break: break-word;
        }}
        .badge.live {{
            background: var(--emerald-bg);
            color: #34d399;
            border-color: var(--emerald-border);
        }}
        .pulse-dot {{
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #10b981;
            box-shadow: 0 0 8px #10b981;
            animation: pulse 2s infinite ease-in-out;
        }}
        @keyframes pulse {{
            0%, 100% {{ transform: scale(1); opacity: 1; }}
            50% {{ transform: scale(1.4); opacity: 0.6; }}
        }}
        /* Summary & Refresh Toolbar */
        .toolbar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(18, 24, 37, 0.8);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 12px 16px;
            margin-bottom: 16px;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .toolbar-info {{
            font-size: 0.92rem;
            font-weight: 600;
            color: #e2e8f0;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .toolbar-actions {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}
        /* File Card Styling - STRICT NO LEAKAGE */
        .file-list {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .file-card {{
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 14px 16px;
            display: flex;
            align-items: center;
            gap: 14px;
            width: 100%;
            min-width: 0;
            transition: border-color 0.2s ease, transform 0.15s ease;
        }}
        .file-card:hover {{
            border-color: rgba(56, 189, 248, 0.4);
            transform: translateY(-1px);
        }}
        .file-icon-box {{
            font-size: 1.8rem;
            flex-shrink: 0;
            width: 44px;
            height: 44px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 12px;
        }}
        .file-info {{
            flex: 1;
            min-width: 0; /* CRITICAL: Allows ellipsis to work inside flex parent */
            overflow: hidden;
        }}
        .file-title {{
            font-weight: 600;
            font-size: 0.94rem;
            color: #f1f5f9;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 100%;
            display: block;
        }}
        .file-meta {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 4px;
        }}
        .file-size-badge {{
            color: var(--text-muted);
            font-size: 0.8rem;
        }}
        .file-ext-badge {{
            background: rgba(255, 255, 255, 0.06);
            color: #cbd5e1;
            border-radius: 4px;
            padding: 2px 6px;
            font-size: 0.68rem;
            font-weight: 700;
            text-transform: uppercase;
        }}
        .file-actions {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-shrink: 0;
        }}
        /* Calming Modern Buttons */
        .btn {{
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 8px 14px;
            border-radius: 10px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
            border: none;
        }}
        .btn-primary {{
            background: var(--primary-btn);
            color: #ffffff;
            box-shadow: 0 4px 12px rgba(2, 132, 199, 0.25);
        }}
        .btn-primary:hover {{
            background: var(--primary-btn-hover);
        }}
        .btn-secondary {{
            background: var(--secondary-btn);
            color: #e2e8f0;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }}
        .btn-secondary:hover {{
            background: var(--secondary-btn-hover);
        }}
        .btn-zip {{
            background: var(--zip-btn);
            color: #ffffff;
            padding: 8px 14px;
            border-radius: 10px;
        }}
        .btn-zip:hover {{
            background: var(--zip-btn-hover);
        }}
        .btn-refresh {{
            background: rgba(56, 189, 248, 0.12);
            color: #38bdf8;
            border: 1px solid rgba(56, 189, 248, 0.28);
            padding: 7px 12px;
            border-radius: 10px;
        }}
        .btn-refresh:hover {{
            background: rgba(56, 189, 248, 0.22);
        }}
        /* Toast notification */
        #toast {{
            position: fixed;
            bottom: 24px;
            left: 50%;
            transform: translateX(-50%) translateY(100px);
            background: rgba(16, 185, 129, 0.95);
            color: #ffffff;
            padding: 8px 18px;
            border-radius: 999px;
            font-size: 0.85rem;
            font-weight: 600;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
            transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            z-index: 999;
            pointer-events: none;
        }}
        #toast.show {{
            transform: translateX(-50%) translateY(0);
        }}
        .footer {{
            text-align: center;
            margin-top: 32px;
            color: #64748b;
            font-size: 0.8rem;
        }}
        /* Responsive Mobile Layout */
        @media (max-width: 500px) {{
            body {{
                padding: 14px 10px 30px;
            }}
            .file-card {{
                flex-wrap: wrap;
                gap: 10px;
                padding: 12px 14px;
            }}
            .file-info {{
                width: calc(100% - 60px);
            }}
            .file-actions {{
                width: 100%;
                justify-content: flex-end;
                padding-top: 8px;
                border-top: 1px solid rgba(255, 255, 255, 0.05);
            }}
            .btn {{
                padding: 7px 12px;
                font-size: 0.82rem;
            }}
            .toolbar {{
                flex-direction: column;
                align-items: stretch;
            }}
            .toolbar-actions {{
                justify-content: space-between;
                width: 100%;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">⚡ LocalShare</div>
            <div class="tagline">Calm & Lightning Fast Local Transfer</div>
            <div class="badge-row">
                <span class="badge live"><span class="pulse-dot"></span> Live Local Connection</span>
                <span class="badge">📶 {network_name}</span>
                <span class="badge">🛡️ 100% Offline (No Internet Data)</span>
            </div>
        </div>

        <div class="toolbar">
            <div class="toolbar-info" id="summary-info">
                📂 <span id="files-count">{total_files}</span> file{"s" if total_files != 1 else ""} available (<span id="files-size">{total_size_str}</span>)
            </div>
            <div class="toolbar-actions">
                <button type="button" class="btn btn-refresh" onclick="manualRefresh()" id="refresh-btn">🔄 Refresh</button>
                <span id="zip-btn-container">{zip_btn_html}</span>
            </div>
        </div>

        <div class="file-list" id="files-container">
            {rows_html if rows_html else '<p style="text-align:center;color:#94a3b8;padding:26px;">No files currently shared.</p>'}
        </div>

        <div class="footer">
            Direct device-to-device Wi-Fi transfer • Zero cloud • Maximum speed
        </div>
    </div>

    <div id="toast">✓ Synced with PC</div>

    <script>
        let currentVersion = {version_id};
        let isRefreshing = false;

        function showToast(msg) {{
            const toast = document.getElementById('toast');
            toast.textContent = msg;
            toast.classList.add('show');
            setTimeout(() => toast.classList.remove('show'), 2000);
        }}

        function renderFilesList(data) {{
            const container = document.getElementById('files-container');
            const countEl = document.getElementById('files-count');
            const sizeEl = document.getElementById('files-size');
            const zipContainer = document.getElementById('zip-btn-container');

            countEl.textContent = data.total_files;
            sizeEl.textContent = data.total_size;

            if (data.total_files > 1) {{
                zipContainer.innerHTML = '<a href="/download-all" class="btn btn-zip">📦 Download All (ZIP)</a>';
            }} else {{
                zipContainer.innerHTML = '';
            }}

            if (!data.files || data.files.length === 0) {{
                container.innerHTML = '<p style="text-align:center;color:#94a3b8;padding:26px;">No files currently shared.</p>';
                return;
            }}

            let html = '';
            data.files.forEach(f => {{
                const extClean = f.name.includes('.') ? f.name.split('.').pop().toUpperCase() : 'FILE';
                const previewBtn = f.can_preview ? `<a href="/view?id=${{f.id}}" target="_blank" class="btn btn-secondary">👁️ View</a>` : '';
                const downloadBtn = `<a href="/download?id=${{f.id}}" class="btn btn-primary">⬇️ Download</a>`;

                html += `
                <div class="file-card" id="file-card-${{f.id}}">
                    <div class="file-icon-box">${{f.icon}}</div>
                    <div class="file-info">
                        <div class="file-title" title="${{f.name}}">${{f.name}}</div>
                        <div class="file-meta">
                            <span class="file-size-badge">${{f.size}}</span>
                            <span class="file-ext-badge">${{extClean}}</span>
                        </div>
                    </div>
                    <div class="file-actions">
                        ${{previewBtn}}
                        ${{downloadBtn}}
                    </div>
                </div>
                `;
            }});
            container.innerHTML = html;
        }}

        function fetchLiveFiles(showFeedback = false) {{
            if (isRefreshing) return;
            isRefreshing = true;

            fetch('/api/files?t=' + Date.now(), {{
                cache: 'no-store',
                headers: {{ 'Pragma': 'no-cache', 'Cache-Control': 'no-cache' }}
            }})
            .then(res => res.json())
            .then(data => {{
                isRefreshing = false;
                if (data.version !== currentVersion || showFeedback) {{
                    currentVersion = data.version;
                    renderFilesList(data);
                    if (showFeedback) showToast('✓ Synced with PC');
                }}
            }})
            .catch(() => {{
                isRefreshing = false;
            }});
        }}

        function manualRefresh() {{
            const btn = document.getElementById('refresh-btn');
            btn.textContent = '⏳ Syncing...';
            fetchLiveFiles(true);
            setTimeout(() => {{
                btn.textContent = '🔄 Refresh';
            }}, 800);
        }}

        // Auto-poll every 2.5 seconds so added files show up automatically!
        setInterval(() => fetchLiveFiles(false), 2500);

        // Also refresh on window focus
        window.addEventListener('focus', () => fetchLiveFiles(false));
    </script>
</body>
</html>"""

        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.send_header('Content-Length', str(len(html.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def serve_file(self, file_id, shared_files, as_attachment=True):
        """Serve a specific file with range support and binary chunk streaming."""
        target_file = None
        for f in shared_files:
            if str(f['id']) == str(file_id):
                target_file = f
                break

        if not target_file:
            self.send_error_response(404, "Requested file not found in active share.")
            return

        filepath = target_file['path']
        if not os.path.isfile(filepath):
            self.send_error_response(404, "File is no longer accessible on disk.")
            return

        filename = target_file['name']
        file_size = os.path.getsize(filepath)
        mime_type, _ = mimetypes.guess_type(filepath)
        if not mime_type:
            mime_type = 'application/octet-stream'

        range_header = self.headers.get('Range', None)
        start = 0
        end = file_size - 1
        status_code = 200

        if range_header and range_header.startswith('bytes='):
            range_parts = range_header.replace('bytes=', '').split('-')
            try:
                if range_parts[0]:
                    start = int(range_parts[0])
                if len(range_parts) > 1 and range_parts[1]:
                    end = int(range_parts[1])
                status_code = 206
            except ValueError:
                start = 0
                end = file_size - 1

        if start > end or start >= file_size:
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{file_size}')
            self.end_headers()
            return

        content_length = (end - start) + 1

        self.send_response(status_code)
        self.send_header('Content-Type', mime_type)
        self.send_header('Accept-Ranges', 'bytes')

        if status_code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')

        self.send_header('Content-Length', str(content_length))

        safe_ascii_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', filename)
        encoded_name = urllib.parse.quote(filename)

        if as_attachment:
            self.send_header(
                'Content-Disposition',
                f'attachment; filename="{safe_ascii_name}"; filename*=UTF-8\'\'{encoded_name}'
            )
        else:
            self.send_header(
                'Content-Disposition',
                f'inline; filename="{safe_ascii_name}"; filename*=UTF-8\'\'{encoded_name}'
            )

        self.end_headers()

        chunk_size = 128 * 1024
        try:
            with open(filepath, 'rb') as f:
                f.seek(start)
                bytes_left = content_length
                while bytes_left > 0:
                    read_len = min(chunk_size, bytes_left)
                    data = f.read(read_len)
                    if not data:
                        break
                    self.wfile.write(data)
                    bytes_left -= len(data)
        except (ConnectionResetError, BrokenPipeError):
            pass

    def serve_all_as_zip(self, shared_files):
        """Bundle all shared files into a ZIP stream on the fly."""
        if not shared_files:
            self.send_error_response(400, "No files to bundle.")
            return

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in shared_files:
                filepath = f['path']
                arcname = f['name']
                if os.path.isfile(filepath):
                    zf.write(filepath, arcname=arcname)

        zip_data = zip_buffer.getvalue()
        zip_size = len(zip_data)

        self.send_response(200)
        self.send_header('Content-Type', 'application/zip')
        self.send_header('Content-Length', str(zip_size))
        self.send_header('Content-Disposition', 'attachment; filename="LocalShare_Bundle.zip"')
        self.end_headers()

        try:
            self.wfile.write(zip_data)
        except (ConnectionResetError, BrokenPipeError):
            pass


# ----------------------------------------------------------------------
# GUI Application Class (Calm & Modern Responsive Tkinter Design)
# ----------------------------------------------------------------------

class LocalShareApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LocalShare - Fast Offline Wi-Fi File Sharing")
        self.root.geometry("600x740")
        self.root.minsize(420, 480)

        # State Variables
        self.is_connected = False
        self.network_name = "Not Connected"
        self.local_ip = None
        self.has_prompted_shortcut = False
        self.is_sharing = False
        self.http_server = None
        self.server_port = 8080
        self.server_thread = None
        self.version_id = 1
        self.selected_items = []  # List of dicts: {'id', 'name', 'path', 'size'}
        self.qr_photo = None
        self.browse_visible = False

        # Shutter animation state for signature
        self.shutter_progress = 0.0
        self.shutter_hovered = False
        self.shutter_anim_id = None

        # Soothing & Calm Luxury Dark Theme Palette
        self.COLOR_BG = "#0b0f17"              # Deep calming obsidian
        self.COLOR_CARD = "#131a26"            # Soft night slate card
        self.COLOR_CARD_BORDER = "#1f293d"     # Delicate hairline border
        self.COLOR_CARD_ITEM = "#182130"       # Selected item chip background
        self.COLOR_TEXT_MAIN = "#f1f5f9"       # Soft white text
        self.COLOR_TEXT_MUTED = "#94a3b8"      # Gentle slate gray text
        self.COLOR_TEXT_DIM = "#64748b"        # Subdued gray text

        # Accents
        self.COLOR_CYAN = "#38bdf8"
        self.COLOR_INDIGO = "#818cf8"
        self.COLOR_PRIMARY_BTN = "#0284c7"
        self.COLOR_PRIMARY_HOVER = "#0369a1"

        # Connected (Soothing Emerald Mint)
        self.COLOR_GREEN = "#34d399"
        self.COLOR_GREEN_BG = "#062217"
        self.COLOR_GREEN_BORDER = "#0d553d"

        # Disconnected (Calming Rose Red)
        self.COLOR_ROSE = "#fb7185"
        self.COLOR_ROSE_BG = "#220e15"
        self.COLOR_ROSE_BORDER = "#5c182a"

        # Buttons
        self.COLOR_BTN_SLATE = "#1e293b"
        self.COLOR_BTN_SLATE_HOVER = "#334155"
        self.COLOR_BTN_RESET = "#334155"
        self.COLOR_BTN_RESET_HOVER = "#475569"

        self.root.configure(bg=self.COLOR_BG)

        # Build UI
        self.setup_ui()

        # Initial Network Check & 1-Second Poller
        self.update_network_status(initial_check=True)
        self.poll_network_status()

    def setup_ui(self):
        # Main Canvas + Scrollbar for smooth scrolling on all resolutions
        self.canvas = tk.Canvas(self.root, bg=self.COLOR_BG, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=self.canvas.yview)
        self.content_frame = tk.Frame(self.canvas, bg=self.COLOR_BG)

        self.content_frame.bind(
            "<Configure>",
            lambda e: self.update_scroll()
        )
        self.canvas_window = self.canvas.create_window((0, 0), window=self.content_frame, anchor="nw")

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        # Responsive canvas resize listener
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Enable mousewheel scrolling everywhere
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)

        pad_x = 20

        # Header Section
        header_frame = tk.Frame(self.content_frame, bg=self.COLOR_BG)
        header_frame.pack(fill="x", padx=pad_x, pady=(16, 8))

        # Header right side: Embossed Signature Box with shutter animation
        self.setup_embossed_signature(header_frame)

        # Header left side: Title
        title_lbl = tk.Label(
            header_frame,
            text="⚡ LocalShare",
            font=("Segoe UI", 24, "bold"),
            fg=self.COLOR_CYAN,
            bg=self.COLOR_BG
        )
        title_lbl.pack(side="left", anchor="w", pady=4)

        # -------------------------------------------------------------
        # TOP NETWORK STATUS BAR (Soothing border card)
        # -------------------------------------------------------------
        self.network_banner = tk.Frame(
            self.content_frame,
            bd=1,
            relief="solid",
            highlightthickness=1
        )
        self.network_banner.pack(fill="x", padx=pad_x, pady=10)

        # Pack buttons on RIGHT first to prevent being pushed off-screen
        self.net_btn_frame = tk.Frame(self.network_banner, bd=0)
        self.net_btn_frame.pack(side="right", padx=(8, 12), pady=10)

        self.btn_refresh_network = tk.Button(
            self.net_btn_frame,
            text="🔄 Refresh",
            font=("Segoe UI", 9),
            bg=self.COLOR_BTN_SLATE,
            fg=self.COLOR_TEXT_MAIN,
            activebackground=self.COLOR_BTN_SLATE_HOVER,
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
            command=lambda: self.update_network_status(force_check=True)
        )
        self.btn_refresh_network.pack(side="right", padx=(4, 0))

        self.btn_open_network = tk.Button(
            self.net_btn_frame,
            text="📶 Connect Wi-Fi",
            font=("Segoe UI", 9, "bold"),
            bg="#e11d48",
            fg="#ffffff",
            activebackground="#be123c",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=5,
            cursor="hand2",
            command=open_network_shortcut
        )

        # Icon on LEFT
        self.net_icon_lbl = tk.Label(self.network_banner, font=("Segoe UI", 16), bd=0)
        self.net_icon_lbl.pack(side="left", padx=(12, 6), pady=10)

        # Text in CENTER (fills and wraps dynamically)
        self.net_text_frame = tk.Frame(self.network_banner, bd=0)
        self.net_text_frame.pack(side="left", fill="both", expand=True, pady=10)

        self.net_status_title = tk.Label(
            self.net_text_frame,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
            justify="left"
        )
        self.net_status_title.pack(fill="x")

        self.net_status_sub = tk.Label(
            self.net_text_frame,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left"
        )
        self.net_status_sub.pack(fill="x")

        # -------------------------------------------------------------
        # BROWSE TO UPLOAD CONTAINER
        # -------------------------------------------------------------
        self.browse_container = tk.Frame(self.content_frame, bg=self.COLOR_BG)

        # Browse Card
        self.browse_card = tk.Frame(
            self.browse_container,
            bg=self.COLOR_CARD,
            highlightbackground=self.COLOR_CARD_BORDER,
            highlightthickness=1,
            bd=0
        )
        self.browse_card.pack(fill="x", pady=8)

        self.browse_header_lbl = tk.Label(
            self.browse_card,
            text="📁 Browse to Upload",
            font=("Segoe UI", 12, "bold"),
            fg=self.COLOR_TEXT_MAIN,
            bg=self.COLOR_CARD
        )
        self.browse_header_lbl.pack(anchor="w", padx=16, pady=(14, 2))

        self.browse_desc_lbl = tk.Label(
            self.browse_card,
            text="Select single or multiple files, or an entire folder to share",
            font=("Segoe UI", 9),
            fg=self.COLOR_TEXT_MUTED,
            bg=self.COLOR_CARD,
            justify="left"
        )
        self.browse_desc_lbl.pack(anchor="w", padx=16, pady=(0, 10))

        # Single responsive button for selecting either files or folders
        btn_pick_row = tk.Frame(self.browse_card, bg=self.COLOR_CARD)
        btn_pick_row.pack(fill="x", padx=16, pady=(0, 14))

        self.btn_pick_files = tk.Button(
            btn_pick_row,
            text="📄 Add File(s) or Folder",
            font=("Segoe UI", 11, "bold"),
            bg="#16253b",
            fg=self.COLOR_CYAN,
            activebackground="#1e3452",
            activeforeground=self.COLOR_CYAN,
            relief="flat",
            pady=11,
            cursor="hand2",
            command=self.choose_files_or_folders
        )
        self.btn_pick_files.pack(fill="x")

        # Selected Files Preview Container (Modern list with chip items)
        self.selection_box = tk.Frame(
            self.browse_card,
            bg="#0f1520",
            highlightbackground="#1c2536",
            highlightthickness=1,
            bd=0
        )

        sel_header_row = tk.Frame(self.selection_box, bg="#0f1520")
        sel_header_row.pack(fill="x", padx=12, pady=(10, 6))

        self.selection_summary_lbl = tk.Label(
            sel_header_row,
            text="",
            font=("Segoe UI", 9, "bold"),
            fg=self.COLOR_GREEN,
            bg="#0f1520",
            anchor="w"
        )
        self.selection_summary_lbl.pack(side="left")

        btn_clear_all = tk.Button(
            sel_header_row,
            text="✕ Clear All",
            font=("Segoe UI", 8),
            bg="#0f1520",
            fg=self.COLOR_TEXT_DIM,
            activebackground="#0f1520",
            activeforeground=self.COLOR_ROSE,
            relief="flat",
            bd=0,
            cursor="hand2",
            command=self.clear_all_selected
        )
        btn_clear_all.pack(side="right")

        # Scrollable container for selected items
        self.items_scroll_container = tk.Frame(self.selection_box, bg="#0f1520")
        self.items_scroll_container.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # -------------------------------------------------------------
        # ACTION BUTTONS BAR: Share & Reset
        # -------------------------------------------------------------
        self.actions_row = tk.Frame(self.browse_container, bg=self.COLOR_BG)
        self.actions_row.pack(fill="x", pady=(6, 12))
        self.actions_row.columnconfigure(0, weight=3)
        self.actions_row.columnconfigure(1, weight=2)

        self.btn_share = tk.Button(
            self.actions_row,
            text="🚀 Share Files",
            font=("Segoe UI", 11, "bold"),
            bg=self.COLOR_PRIMARY_BTN,
            fg="#ffffff",
            activebackground=self.COLOR_PRIMARY_HOVER,
            activeforeground="#ffffff",
            relief="flat",
            pady=10,
            cursor="hand2",
            state="disabled",
            command=self.start_sharing
        )
        self.btn_share.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_reset = tk.Button(
            self.actions_row,
            text="🔄 Reset",
            font=("Segoe UI", 11, "bold"),
            bg=self.COLOR_BTN_RESET,
            fg="#ffffff",
            activebackground=self.COLOR_BTN_RESET_HOVER,
            activeforeground="#ffffff",
            relief="flat",
            pady=10,
            cursor="hand2",
            command=self.reset_all
        )
        self.btn_reset.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        # -------------------------------------------------------------
        # QR CODE & CONNECTION SECTION (Responsive)
        # -------------------------------------------------------------
        self.share_active_frame = tk.Frame(
            self.browse_container,
            bg=self.COLOR_CARD,
            highlightbackground="#0369a1",
            highlightthickness=1,
            bd=0
        )

        share_head_lbl = tk.Label(
            self.share_active_frame,
            text="📲 Ready to Scan & Download",
            font=("Segoe UI", 13, "bold"),
            fg=self.COLOR_CYAN,
            bg=self.COLOR_CARD
        )
        share_head_lbl.pack(pady=(16, 4))

        self.share_sub_lbl = tk.Label(
            self.share_active_frame,
            text="Scan this QR code with any phone/device camera on the same Wi-Fi.\nNo internet data used • Transfers at maximum Wi-Fi speed!",
            font=("Segoe UI", 9),
            fg=self.COLOR_TEXT_MUTED,
            bg=self.COLOR_CARD,
            justify="center"
        )
        self.share_sub_lbl.pack(padx=16, pady=(0, 12))

        # White padded frame for QR Code display
        qr_border_frame = tk.Frame(self.share_active_frame, bg="#ffffff", padx=10, pady=10)
        qr_border_frame.pack()

        self.qr_label = tk.Label(qr_border_frame, bg="#ffffff")
        self.qr_label.pack()

        # URL Box & Copy Button
        url_box_frame = tk.Frame(self.share_active_frame, bg=self.COLOR_CARD)
        url_box_frame.pack(fill="x", padx=16, pady=(14, 16))

        self.btn_copy = tk.Button(
            url_box_frame,
            text="📋 Copy Link",
            font=("Segoe UI", 9, "bold"),
            bg=self.COLOR_BTN_SLATE,
            fg=self.COLOR_CYAN,
            activebackground=self.COLOR_BTN_SLATE_HOVER,
            activeforeground=self.COLOR_CYAN,
            relief="flat",
            padx=12,
            pady=6,
            cursor="hand2",
            command=self.copy_url_to_clipboard
        )
        self.btn_copy.pack(side="right", padx=(8, 0))

        self.btn_open_browser = tk.Button(
            url_box_frame,
            text="🌐 Open",
            font=("Segoe UI", 9),
            bg=self.COLOR_BTN_SLATE,
            fg=self.COLOR_TEXT_MAIN,
            activebackground=self.COLOR_BTN_SLATE_HOVER,
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=6,
            cursor="hand2",
            command=self.open_in_browser
        )
        self.btn_open_browser.pack(side="right", padx=(4, 0))

        self.url_label = tk.Label(
            url_box_frame,
            text="",
            font=("Consolas", 10, "bold"),
            fg="#f1f5f9",
            bg="#0f1520",
            padx=10,
            pady=6,
            relief="solid",
            bd=1,
            anchor="w"
        )
        self.url_label.pack(side="left", fill="x", expand=True)

    def setup_embossed_signature(self, parent_frame):
        """
        Creates a luxury embossed signature badge for BIBHANSHU
        with a smooth animated shutter that reveals the email on hover.
        """
        self.sig_w = 200
        self.sig_h = 66
        self.badge_w = 138
        self.badge_h = 36
        self.shutter_max_h = 24

        self.sig_canvas = tk.Canvas(
            parent_frame,
            width=self.sig_w,
            height=self.sig_h,
            bg=self.COLOR_BG,
            highlightthickness=0,
            bd=0,
            cursor="hand2"
        )
        self.sig_canvas.pack(side="right", padx=(8, 0), pady=(0, 0))

        self.draw_embossed_badge()

        self.sig_canvas.bind("<Enter>", lambda e: self.animate_shutter(True))
        self.sig_canvas.bind("<Leave>", lambda e: self.animate_shutter(False))
        self.sig_canvas.bind("<Button-1>", lambda e: self.copy_signature_email())

    def _get_rounded_points(self, x1, y1, x2, y2, r=8):
        """Helper to create coordinates for smooth rounded polygons."""
        return [
            x1 + r, y1,      x2 - r, y1,
            x2, y1,          x2, y1 + r,
            x2, y2 - r,      x2, y2,
            x2 - r, y2,      x1 + r, y2,
            x1, y2,          x1, y2 - r,
            x1, y1 + r,      x1, y1
        ]

    def draw_embossed_badge(self):
        """Draw embossed signature and smooth shutter drop flap."""
        self.sig_canvas.delete("all")
        cx = self.sig_w // 2
        bx1 = cx - self.badge_w // 2
        by1 = 2
        bx2 = cx + self.badge_w // 2
        by2 = by1 + self.badge_h

        # 1. Shutter flap: Extends DOWN from behind the badge
        cur_shutter_h = int(self.shutter_max_h * self.shutter_progress)
        if cur_shutter_h > 1:
            sx1 = 6
            sx2 = self.sig_w - 6
            sy1 = by2 - 6
            sy2 = sy1 + 6 + cur_shutter_h
            shutter_pts = self._get_rounded_points(sx1, sy1, sx2, sy2, r=6)
            self.sig_canvas.create_polygon(
                shutter_pts,
                fill="#0284c7", outline="#38bdf8", width=1, smooth=True
            )
            if self.shutter_progress >= 0.5:
                text_y = (by2 + sy2) // 2
                self.sig_canvas.create_text(
                    cx, text_y,
                    text="bibhanshuya@gmail.com",
                    font=("Segoe UI", 8, "bold"),
                    fill="#ffffff"
                )

        # 2. Main Embossed Badge (Drawn ON TOP of the shutter flap)
        badge_pts = self._get_rounded_points(bx1, by1, bx2, by2, r=10)

        if self.shutter_hovered:
            bg_fill = "#1b253b"
            shadow_stroke = "#0f172a"
            highlight_stroke = "#38bdf8"
            bevel_shadow = "#070b12"
            bevel_light = "#60a5fa"
            main_text_color = "#ffffff"
        else:
            bg_fill = "#111827"
            shadow_stroke = "#090d14"
            highlight_stroke = "#1e293b"
            bevel_shadow = "#070b12"
            bevel_light = "#2a374f"
            main_text_color = "#cbd5e1"

        # Outer bevel/shadow
        self.sig_canvas.create_polygon(
            [p + (1 if i % 2 == 1 else 0) for i, p in enumerate(badge_pts)],
            fill="", outline=shadow_stroke, width=2, smooth=True
        )

        # Badge body
        self.sig_canvas.create_polygon(
            badge_pts,
            fill=bg_fill, outline=highlight_stroke, width=1.5, smooth=True
        )

        # Embossed Text Effect: Lower-right shadow, Upper-left highlight, Centered face
        t_cy = (by1 + by2) // 2

        self.sig_canvas.create_text(
            cx + 1, t_cy + 1,
            text="BIBHANSHU",
            font=("Segoe UI", 10, "bold"),
            fill=bevel_shadow,
            anchor="center"
        )
        self.sig_canvas.create_text(
            cx - 1, t_cy - 1,
            text="BIBHANSHU",
            font=("Segoe UI", 10, "bold"),
            fill=bevel_light,
            anchor="center"
        )
        self.sig_canvas.create_text(
            cx, t_cy,
            text="BIBHANSHU",
            font=("Segoe UI", 10, "bold"),
            fill=main_text_color,
            anchor="center"
        )

    def animate_shutter(self, opening):
        """Smooth 60fps shutter animation sliding down/up behind badge."""
        self.shutter_hovered = opening
        if self.shutter_anim_id:
            self.root.after_cancel(self.shutter_anim_id)
            self.shutter_anim_id = None

        step = 0.16 if opening else -0.16

        def _step():
            self.shutter_progress += step
            if opening and self.shutter_progress >= 1.0:
                self.shutter_progress = 1.0
                self.draw_embossed_badge()
                self.shutter_anim_id = None
                return
            elif not opening and self.shutter_progress <= 0.0:
                self.shutter_progress = 0.0
                self.draw_embossed_badge()
                self.shutter_anim_id = None
                return

            self.draw_embossed_badge()
            self.shutter_anim_id = self.root.after(16, _step)

        _step()

    def copy_signature_email(self):
        """Copy creator email to clipboard when clicked."""
        self.root.clipboard_clear()
        self.root.clipboard_append("bibhanshuya@gmail.com")

    def _on_canvas_configure(self, event):
        """Responsive canvas resizer: adapts inner width and dynamic text wrapping."""
        self.canvas.itemconfig(self.canvas_window, width=event.width)

        content_w = max(180, event.width - 48)
        banner_w = max(130, event.width - 240)

        if hasattr(self, 'net_status_title'):
            self.net_status_title.configure(wraplength=banner_w)
        if hasattr(self, 'net_status_sub'):
            self.net_status_sub.configure(wraplength=banner_w)
        if hasattr(self, 'browse_desc_lbl'):
            self.browse_desc_lbl.configure(wraplength=content_w)
        if hasattr(self, 'share_sub_lbl'):
            self.share_sub_lbl.configure(wraplength=content_w)

        self.update_scroll()

    def update_scroll(self):
        """Recalculate scroll region dynamically so all elements are accessible without runaway scrolling."""
        self.content_frame.update_idletasks()
        bbox = self.canvas.bbox("all")
        if not bbox:
            return

        content_h = bbox[3] - bbox[1]
        canvas_h = self.canvas.winfo_height()

        if canvas_h > 1 and content_h <= canvas_h:
            # Everything fits on screen: lock scrollregion to canvas height and reset to top
            self.canvas.configure(scrollregion=(0, 0, bbox[2], canvas_h))
            self.canvas.yview_moveto(0.0)
            if self.scrollbar.winfo_ismapped():
                self.scrollbar.pack_forget()
        else:
            self.canvas.configure(scrollregion=bbox)
            if not self.scrollbar.winfo_ismapped():
                self.scrollbar.pack(side="right", fill="y")

    def _on_mousewheel(self, event):
        if not self.canvas.winfo_exists():
            return
        bbox = self.canvas.bbox("all")
        if not bbox:
            return
        content_h = bbox[3] - bbox[1]
        canvas_h = self.canvas.winfo_height()

        # Do NOT scroll if all content already fits on screen
        if content_h <= canvas_h:
            return

        # Bound scrolling so heading & content cannot scroll away into void
        top, bottom = self.canvas.yview()
        delta = event.delta
        if delta > 0 and top <= 0.001:
            return
        if delta < 0 and bottom >= 0.999:
            return

        self.canvas.yview_scroll(int(-1 * (delta / 120)), "units")

    # -------------------------------------------------------------
    # Network Status Handler
    # -------------------------------------------------------------

    def update_network_status(self, initial_check=False, force_check=False):
        is_conn, net_name, ip = detect_network_status()

        status_changed = (is_conn != self.is_connected or net_name != self.network_name or ip != self.local_ip)
        if not force_check and not status_changed:
            return

        was_connected = self.is_connected
        self.is_connected = is_conn
        self.network_name = net_name
        self.local_ip = ip

        if not self.is_connected:
            self.network_banner.configure(
                bg=self.COLOR_ROSE_BG,
                highlightbackground=self.COLOR_ROSE_BORDER,
                highlightthickness=1
            )
            self.net_icon_lbl.configure(
                text="⚠️",
                bg=self.COLOR_ROSE_BG,
                fg=self.COLOR_ROSE
            )
            self.net_text_frame.configure(bg=self.COLOR_ROSE_BG)
            self.net_status_title.configure(
                text="Not Connected to any Wi-Fi or Hotspot Network!",
                fg=self.COLOR_ROSE,
                bg=self.COLOR_ROSE_BG
            )
            self.net_status_sub.configure(
                text="Connect this PC to a Wi-Fi or Hotspot network to enable file sharing.",
                fg="#fca5a5",
                bg=self.COLOR_ROSE_BG
            )
            self.net_btn_frame.configure(bg=self.COLOR_ROSE_BG)
            self.btn_open_network.pack(side="right", padx=(0, 4))

            if self.browse_visible:
                self.browse_container.pack_forget()
                self.browse_visible = False
                self.update_scroll()

            if (was_connected and not is_conn) or (initial_check and not self.has_prompted_shortcut):
                self.has_prompted_shortcut = True
                self.root.after(200, open_network_shortcut)

            if self.is_sharing or self.selected_items:
                self.reset_all()

        else:
            self.network_banner.configure(
                bg=self.COLOR_GREEN_BG,
                highlightbackground=self.COLOR_GREEN_BORDER,
                highlightthickness=1
            )
            self.net_icon_lbl.configure(
                text="🟢",
                bg=self.COLOR_GREEN_BG,
                fg=self.COLOR_GREEN
            )
            self.net_text_frame.configure(bg=self.COLOR_GREEN_BG)
            self.net_status_title.configure(
                text=f"Connected: {self.network_name}",
                fg=self.COLOR_GREEN,
                bg=self.COLOR_GREEN_BG
            )
            self.net_status_sub.configure(
                text=f"Local IP: {self.local_ip}  •  Ready for high-speed offline sharing",
                fg="#a7f3d0",
                bg=self.COLOR_GREEN_BG
            )
            self.net_btn_frame.configure(bg=self.COLOR_GREEN_BG)
            self.btn_open_network.pack_forget()

            if not self.browse_visible:
                self.browse_container.pack(fill="x", padx=20, pady=10)
                self.browse_visible = True
                self.update_scroll()

    def poll_network_status(self):
        self.update_network_status()
        self.root.after(1000, self.poll_network_status)

    # -------------------------------------------------------------
    # Live Auto-Sync Server Helper
    # -------------------------------------------------------------

    def sync_active_server_files(self):
        """Instantly push updated files to the live running server."""
        if self.is_sharing and self.http_server:
            self.version_id += 1
            self.http_server.version_id = self.version_id
            self.http_server.shared_files = list(self.selected_items)
            self.http_server.server_info['network_name'] = self.network_name
            self.http_server.server_info['ip'] = self.local_ip

    # -------------------------------------------------------------
    # File Selection Handlers
    # -------------------------------------------------------------

    def choose_files_or_folders(self):
        """
        Directly open Windows File & Folder Open Dialog.
        Allows user to pick files, multiple files, or folders seamlessly in one click.
        Falls back smoothly to standard file/folder picker if needed.
        """
        hwnd = self.root.winfo_id()
        selected_paths = pick_files_or_folders_windows(hwnd=hwnd)

        # Fallback if COM dialog unavailable
        if selected_paths is None:
            selected_paths = filedialog.askopenfilenames(
                parent=self.root,
                title="Select Files to Share"
            )

        if not selected_paths:
            return

        for item_path in selected_paths:
            if os.path.isfile(item_path):
                if not any(item['path'] == item_path for item in self.selected_items):
                    self.selected_items.append({
                        'id': len(self.selected_items),
                        'name': os.path.basename(item_path),
                        'path': item_path,
                        'size': os.path.getsize(item_path)
                    })
            elif os.path.isdir(item_path):
                folder_name = os.path.basename(item_path.rstrip('\\/'))
                for root_dir, _, files in os.walk(item_path):
                    for file in files:
                        abs_path = os.path.join(root_dir, file)
                        rel_path = os.path.relpath(abs_path, item_path)
                        display_name = f"{folder_name}/{rel_path}".replace('\\', '/')
                        if not any(item['path'] == abs_path for item in self.selected_items):
                            self.selected_items.append({
                                'id': len(self.selected_items),
                                'name': display_name,
                                'path': abs_path,
                                'size': os.path.getsize(abs_path)
                            })

        self.update_selection_ui()
        self.sync_active_server_files()

    def choose_files(self):
        """Open Windows file explorer dialog to choose one or more files."""
        self.choose_files_or_folders()

    def choose_folder(self):
        """Open Windows file explorer dialog to choose an entire folder."""
        folderpath = filedialog.askdirectory(
            parent=self.root,
            title="Select Folder to Share"
        )
        if not folderpath or not os.path.isdir(folderpath):
            return

        folder_name = os.path.basename(folderpath.rstrip('\\/'))
        for root_dir, _, files in os.walk(folderpath):
            for file in files:
                abs_path = os.path.join(root_dir, file)
                rel_path = os.path.relpath(abs_path, folderpath)
                display_name = f"{folder_name}/{rel_path}".replace('\\', '/')
                if not any(item['path'] == abs_path for item in self.selected_items):
                    self.selected_items.append({
                        'id': len(self.selected_items),
                        'name': display_name,
                        'path': abs_path,
                        'size': os.path.getsize(abs_path)
                    })

        self.update_selection_ui()
        self.sync_active_server_files()

    def remove_single_file(self, file_id):
        """Remove a single file item by its ID."""
        self.selected_items = [f for f in self.selected_items if f['id'] != file_id]
        # Re-index remaining items
        for idx, item in enumerate(self.selected_items):
            item['id'] = idx
        self.update_selection_ui()
        self.sync_active_server_files()

    def clear_all_selected(self):
        """Clear all selected files."""
        self.selected_items.clear()
        self.update_selection_ui()
        self.sync_active_server_files()

    def update_selection_ui(self):
        """Update modern chip-based file preview and toggle Share button."""
        # Clear existing chips in container
        for widget in self.items_scroll_container.winfo_children():
            widget.destroy()

        if not self.selected_items:
            self.selection_box.pack_forget()
            if not self.is_sharing:
                self.btn_share.configure(
                    state="disabled",
                    bg="#334155",
                    text="🚀 Share Files"
                )
            self.update_scroll()
            return

        self.selection_box.pack(fill="x", padx=16, pady=(0, 14))

        total_size = sum(f['size'] for f in self.selected_items)
        count = len(self.selected_items)
        self.selection_summary_lbl.configure(
            text=f"✓ {count} item{'s' if count != 1 else ''} selected ({format_size(total_size)})"
        )

        # Show at most 6 items visibly, with a count label if more
        max_visible = 6
        display_items = self.selected_items[:max_visible]

        for f in display_items:
            chip_frame = tk.Frame(
                self.items_scroll_container,
                bg=self.COLOR_CARD_ITEM,
                highlightbackground="#222c3d",
                highlightthickness=1,
                bd=0
            )
            chip_frame.pack(fill="x", pady=2)

            icon = get_file_icon(f['name'])
            icon_lbl = tk.Label(
                chip_frame,
                text=icon,
                font=("Segoe UI", 11),
                bg=self.COLOR_CARD_ITEM
            )
            icon_lbl.pack(side="left", padx=(8, 4), pady=4)

            disp_name = truncate_name(f['name'], max_len=32)
            name_lbl = tk.Label(
                chip_frame,
                text=disp_name,
                font=("Segoe UI", 9, "bold"),
                fg=self.COLOR_TEXT_MAIN,
                bg=self.COLOR_CARD_ITEM,
                anchor="w"
            )
            name_lbl.pack(side="left", fill="x", expand=True, padx=(0, 6))

            size_lbl = tk.Label(
                chip_frame,
                text=format_size(f['size']),
                font=("Segoe UI", 8),
                fg=self.COLOR_TEXT_MUTED,
                bg=self.COLOR_CARD_ITEM
            )
            size_lbl.pack(side="right", padx=(0, 6))

            btn_del = tk.Button(
                chip_frame,
                text="✕",
                font=("Segoe UI", 8, "bold"),
                fg=self.COLOR_TEXT_DIM,
                bg=self.COLOR_CARD_ITEM,
                activebackground=self.COLOR_CARD_ITEM,
                activeforeground=self.COLOR_ROSE,
                relief="flat",
                bd=0,
                cursor="hand2",
                command=lambda fid=f['id']: self.remove_single_file(fid)
            )
            btn_del.pack(side="right", padx=(0, 6))

        if len(self.selected_items) > max_visible:
            more_count = len(self.selected_items) - max_visible
            more_lbl = tk.Label(
                self.items_scroll_container,
                text=f"+ {more_count} more files in selection",
                font=("Segoe UI", 8, "italic"),
                fg=self.COLOR_TEXT_DIM,
                bg="#0f1520"
            )
            more_lbl.pack(anchor="w", padx=6, pady=2)

        # Activate Share button
        if self.is_sharing:
            self.btn_share.configure(
                state="normal",
                bg="#059669",
                text="⚡ Sharing Active (Live Synced)"
            )
        else:
            self.btn_share.configure(
                state="normal",
                bg=self.COLOR_PRIMARY_BTN,
                text="🚀 Share Files"
            )

        self.update_scroll()

    # -------------------------------------------------------------
    # Sharing & QR Code Generation
    # -------------------------------------------------------------

    def start_sharing(self):
        """Launch embedded HTTP server and generate QR code."""
        if not self.selected_items:
            messagebox.showwarning("No Files", "Please select file(s) or a folder to share first.")
            return

        if not self.is_connected or not self.local_ip:
            messagebox.showerror("Not Connected", "Please connect to a Wi-Fi or Hotspot network first.")
            return

        # If already sharing, just update the shared files list in-place!
        # Keeps the EXACT SAME PORT and URL, so clients can simply refresh their browser!
        if self.is_sharing and self.http_server:
            self.sync_active_server_files()
            self.btn_share.configure(
                bg="#059669",
                text="⚡ Sharing Active (Live Synced)"
            )
            self.update_scroll()
            return

        # Find open port, preferring the previous server_port if available
        port = self.server_port or 8080
        port_found = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if s.connect_ex(('127.0.0.1', port)) != 0:
                    port_found = True
        except Exception:
            pass

        if not port_found:
            for p in range(8080, 8100):
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        if s.connect_ex(('127.0.0.1', p)) != 0:
                            port = p
                            break
                except Exception:
                    pass
        self.server_port = port

        # Start Threading HTTP Server
        try:
            self.http_server = ResettableThreadingHTTPServer(('0.0.0.0', self.server_port), LocalShareHTTPHandler)
            self.http_server.version_id = self.version_id
            self.http_server.shared_files = list(self.selected_items)
            self.http_server.server_info = {
                'network_name': self.network_name,
                'ip': self.local_ip
            }

            self.server_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
            self.server_thread.start()
            self.is_sharing = True
        except Exception as e:
            messagebox.showerror("Server Error", f"Failed to start local server:\n{e}")
            return

        # Generate QR code
        share_url = f"http://{self.local_ip}:{self.server_port}/"
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=2,
        )
        qr.add_data(share_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="#0b0f17", back_color="#ffffff")

        qr_img = qr_img.resize((210, 210), Image.Resampling.NEAREST)
        self.qr_photo = ImageTk.PhotoImage(qr_img)

        self.qr_label.configure(image=self.qr_photo)
        self.url_label.configure(text=share_url)

        # Show QR card
        self.share_active_frame.pack(fill="x", pady=10)

        # Update Share button state
        self.btn_share.configure(
            bg="#059669",
            text="⚡ Sharing Active (Live Synced)"
        )

        self.update_scroll()

        # Scroll to show QR code nicely
        self.root.after(100, lambda: self.canvas.yview_moveto(1.0))

    def stop_server(self):
        """Stop running HTTP server."""
        if self.http_server:
            try:
                self.http_server.shutdown()
                self.http_server.server_close()
            except Exception:
                pass
            self.http_server = None
        self.is_sharing = False

    def reset_all(self):
        """
        Stop sharing, remove QR code, clear selected files from Browse box,
        and reset state.
        """
        self.stop_server()

        self.share_active_frame.pack_forget()
        self.qr_photo = None
        self.qr_label.configure(image="")
        self.url_label.configure(text="")

        self.selected_items.clear()
        self.update_selection_ui()

        self.btn_share.configure(
            state="disabled",
            bg="#334155",
            text="🚀 Share Files"
        )

        self.update_scroll()
        self.canvas.yview_moveto(0.0)

    def copy_url_to_clipboard(self):
        """Copy the active share URL to clipboard with soothing visual feedback."""
        url = self.url_label.cget("text")
        if url:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self.btn_copy.configure(text="✓ Copied!", fg=self.COLOR_GREEN)
            self.root.after(1800, lambda: self.btn_copy.configure(text="📋 Copy Link", fg=self.COLOR_CYAN))

    def open_in_browser(self):
        """Open the active local share link in default browser for quick testing."""
        url = self.url_label.cget("text")
        if url:
            webbrowser.open(url)

    def on_closing(self):
        """Clean shutdown when closing window."""
        self.stop_server()
        self.root.destroy()


# ----------------------------------------------------------------------
# Application Entry Point
# ----------------------------------------------------------------------

def main():
    root = tk.Tk()
    app = LocalShareApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
