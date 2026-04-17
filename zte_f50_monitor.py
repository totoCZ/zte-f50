#!/usr/bin/env python3
"""
ZTE F50 Modem Tower Stats Monitor — iftop-style TUI
"""

import requests
import time
import sys
import hashlib
import os
import shutil
import string
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from dotenv import load_dotenv

# Load .env from the same directory as this script, regardless of CWD
load_dotenv(Path(__file__).parent / '.env')


# ── crypto helpers ────────────────────────────────────────────────────────────

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest().upper()

def _md5(s: str) -> str:
    return hashlib.md5(s.encode('utf-8')).hexdigest()

def _compute_password(plaintext: str, ld: str) -> str:
    return _sha256(_sha256(plaintext) + ld)

def _compute_ad(cr_version: str, wa_inner_version: str, rd: str) -> str:
    # AD = SHA256(SHA256(wa_inner_version + cr_version) + RD), all uppercase
    return _sha256(_sha256(wa_inner_version + cr_version) + rd)


# ── ANSI helpers ──────────────────────────────────────────────────────────────

RESET  = '\033[0m'
BOLD   = '\033[1m'
DIM    = '\033[2m'
RED    = '\033[31m'
YELLOW = '\033[33m'
GREEN  = '\033[32m'
CYAN   = '\033[36m'
WHITE  = '\033[37m'

def _clear():
    print('\033[2J\033[H', end='')

def _rsrp_color(rsrp: Optional[int]) -> str:
    if rsrp is None: return DIM
    if rsrp >= -80:  return GREEN
    if rsrp >= -90:  return YELLOW
    return RED

def _bars(rsrp: Optional[int]) -> str:
    if rsrp is None: return '░░░░░'
    if rsrp >= -70:  n = 5
    elif rsrp >= -80: n = 4
    elif rsrp >= -90: n = 3
    elif rsrp >= -100: n = 2
    else: n = 1
    c = _rsrp_color(rsrp)
    return c + '█' * n + DIM + '░' * (5 - n) + RESET

def _ago(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 60:  return f'{s}s'
    return f'{s//60}m{s%60:02d}s'

def _cols() -> int:
    return shutil.get_terminal_size((80, 24)).columns


# ── signal assessment ───────────────────────────────────────────────────────────

def _get_signal_assessment(rsrp: Optional[int], snr: Optional[int]) -> Tuple[str, str, str]:
    """
    Assesses signal quality based on RSRP (strength) and SNR (quality).
    Returns a tuple of (rating, description, color).
    """
    # Handle missing data
    if rsrp is None or snr is None:
        return ("No Data", "Waiting for signal data... (｡•́_•̀｡)", DIM)

    # Determine rating and description based on RSRP and SNR combinations
    if rsrp >= -80:  # Strong signal
        if snr > 20:
            return ("Excellent", "Super strong signal! (｡◕‿◕｡)", GREEN)
        elif snr > 10:
            return ("Good", "Strong signal, very clear! (◕‿◕)", GREEN)
        else:
            return ("Fair", "Great signal strength, but some noise. (｡•́︿•̀｡)", YELLOW)
    elif rsrp >= -90:  # Good signal
        if snr > 20:
            return ("Good", "Good signal, very clean! (◕‿◕)", GREEN)
        elif snr > 10:
            return ("Fair", "Decent connection. (๑•̀ㅂ•́)و✧", YELLOW)
        else:
            return ("Poor", "Signal is okay, but it's pretty noisy. (´•̥ ω •̥` )", RED)
    elif rsrp >= -100:  # Weak signal
        if snr > 10:
            return ("Fair", "Weak signal, but it's clean! Trying my best... (｡•́_•̀｡)", YELLOW)
        else:
            return ("Poor", "Signal is weak and noisy. (╥﹏╥)", RED)
    else:  # Very poor signal
        return ("Very Poor", "Signal is very weak and noisy. (╥﹏╥)", RED)


# ── modem client ──────────────────────────────────────────────────────────────

NETWORK_TYPES = {
    0: 'No Service', 1: 'GSM', 2: 'GPRS', 3: 'EDGE',
    4: 'WCDMA', 5: 'HSDPA', 6: 'HSUPA', 7: 'HSPA+',
    8: 'LTE', 9: 'LTE-A', 13: 'LTE', 41: 'NR5G-NSA', 43: 'NR5G-SA',
}

class ZTEF50Monitor:
    def __init__(self, host: Optional[str] = None, username: Optional[str] = None,
                 password: Optional[str] = None):
        self.host = host or os.getenv("ZTE_HOST", "10.50.0.1")
        self.base_url = f"http://{self.host}"
        self.username = username or os.getenv("ZTE_USER", "admin")
        self.password = password or os.getenv("ZTE_PASS", "")
        self._new_session()
        self.logged_in = False
        self.cr_version = self.wa_inner_version = self.ld = self.rd = ""

    def _new_session(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': f'http://{self.host}/index.html',
        })
        
    def _get(self, cmd: str, extra: Optional[dict] = None) -> Optional[dict]:
        params = {'isTest': 'false', 'cmd': cmd}
        if extra:
            params.update(extra)
        r = self.session.get(
            f'{self.base_url}/goform/goform_get_cmd_process',
            params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def _fetch_nonces(self) -> bool:
        ver = self._get('Language,cr_version,wa_inner_version', {'multi_data': '1'})
        self.cr_version = ver.get('cr_version', '')
        self.wa_inner_version = ver.get('wa_inner_version', '')
        self.ld = self._get('LD').get('LD', '')
        self.rd = self._get('RD').get('RD', '')
        return bool(self.ld and self.rd)

    def login(self) -> bool:
        try:
            if not self._fetch_nonces():
                return False
            r = self.session.post(
                f'{self.base_url}/goform/goform_set_cmd_process',
                data={
                    'isTest': 'false',
                    'goformId': 'LOGIN_MULTI_USER',
                    'user': self.username,
                    'password': _compute_password(self.password, self.ld),
                    'AD': _compute_ad(self.cr_version, self.wa_inner_version, self.rd),
                    }, timeout=10)
            resp = r.json()
            if resp.get('loginfo') == 'ok' or str(resp.get('result')) == '0':
                self.logged_in = True
                return True
            return False
        except Exception:
            self.logged_in = False
            return False

    def _logout(self):
        try:
            self.session.post(
                f'{self.base_url}/goform/goform_set_cmd_process',
                data={'isTest': 'false', 'goformId': 'LOGOFF',
                      'AD': _compute_ad(self.cr_version, self.wa_inner_version, self.rd)},
                timeout=10)
        except Exception:
            pass

    def fetch(self) -> Optional[Dict[str, Any]]:
        if not self.logged_in:
            if not self.login():
                return None
        try:
            ts = int(time.time() * 1000)
            merged: Dict[str, Any] = {}
            for cmd, extra in [
                ('network_type,rssi,rscp,lte_rsrp,lte_snr,ecio,lte_pci,cell_id,'
                 'Z5g_rsrp,Z5g_snr,Z5g_SINR,Z5g_CELL_ID,lte_rsrq,lte_rssi',
                 {'multi_data': '1', '_': str(ts)}),
                ('network_information,Lte_ca_status',
                 {'multi_data': '1', '_': str(ts + 1)}),
                ('neighbor_cell_info', {'_': str(ts + 2)}),
            ]:
                d = self._get(cmd, extra)
                if d:
                    merged.update(d)
            return merged or None
        except requests.exceptions.ConnectionError:
            self.logged_in = False
        except Exception:
            pass
        return None


# ── TUI renderer ──────────────────────────────────────────────────────────────

def _divider(width: int, label: str = '') -> str:
    if label:
        pad = width - len(label) - 4
        return f'{DIM}── {RESET}{BOLD}{label}{RESET}{DIM} ' + '─' * max(0, pad) + RESET
    return DIM + '─' * width + RESET

def _kv(label: str, value: Any, width: int = 22) -> str:
    return f'  {DIM}{label:<{width}}{RESET}{value}'

def _val(v: Any) -> str:
    """Blank-safe value display."""
    s = str(v).strip()
    return s if s and s not in ('null', '') else '—'


def render(stats: Dict[str, Any], seen: 'OrderedDict[str, dict]',
           last_ok: float, error: Optional[str]) -> str:
    W = _cols()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines: List[str] = []

    # ── header ────────────────────────────────────────────────────────────────
    title = f'{BOLD}{CYAN}ZTE F50{RESET}  {DIM}{now}{RESET}'
    hint  = f'{DIM}Ctrl+C to quit{RESET}'
    gap   = W - 16 - 14  # approx
    lines.append(f'{title}{"":>{gap}}{hint}')
    lines.append(_divider(W))

    if error:
        lines.append(f'  {RED}{BOLD}ERROR:{RESET} {error}  '
                     f'{DIM}(last data {_ago(last_ok)} ago){RESET}')
        lines.append('')
    else:
        # ── serving cell ──────────────────────────────────────────────────────
        nt_raw = stats.get('network_type')
        nt     = NETWORK_TYPES.get(nt_raw, str(nt_raw)) if isinstance(nt_raw, int) else _val(nt_raw)

        rsrp   = stats.get('lte_rsrp')
        rsrp_i = int(rsrp) if rsrp not in (None, '', 'null') else None
        snr    = stats.get('Lte_snr')
        snr_i  = int(snr) if snr not in (None, '', 'null') else None

        # New assessment based on RSRP and SNR
        rating, description, rating_color = _get_signal_assessment(rsrp_i, snr_i)

        band     = _val(stats.get('Lte_bands'))
        earfcn   = _val(stats.get('Lte_fcn'))
        bw_khz   = stats.get('Lte_bands_widths')
        bw_str   = f'{int(bw_khz)//1000} MHz' if bw_khz else '—'
        pci      = _val(stats.get('Lte_pci'))
        cell_id  = _val(stats.get('Lte_cell_id'))
        rsrq     = _val(stats.get('lte_rsrq'))
        rssi_dbm = _val(stats.get('lte_rssi'))
        ca       = _val(stats.get('Lte_ca_status'))

        bar_str  = _bars(rsrp_i)
        rating_c = f'{rating_color}{BOLD}{rating}{RESET}'
        description_c = f'{DIM}{description}{RESET}'

        lines.append(_divider(W, 'SERVING CELL'))
        lines.append(f'  {BOLD}Network{RESET}  {nt}   '
                     f'{bar_str}  {rating_c}')
        lines.append(f'  {description_c}')
        lines.append('')
        lines.append(_kv('RSRP', f'{_rsrp_color(rsrp_i)}{rsrp_i if rsrp_i is not None else "—"} dBm{RESET}'))
        lines.append(_kv('RSRQ', f'{rsrq} dB'))
        lines.append(_kv('SNR',  f'{snr_i if snr_i is not None else "—"} dB'))
        lines.append(_kv('RSSI', f'{rssi_dbm} dBm'))
        lines.append(_kv('Band / EARFCN', f'B{band}  /  {earfcn}  ({bw_str})'))
        lines.append(_kv('Cell ID / PCI', f'{cell_id}  /  {pci}'))
        lines.append(_kv('CA', ca))
        lines.append('')

    # ── neighbor table ────────────────────────────────────────────────────────
    lines.append(_divider(W, f'NEIGHBORS  (last 60s — {len(seen)} cells)'))

    # column widths
    lines.append(
        f'  {BOLD}'
        f'{"Band":<6}{"EARFCN":<9}{"PCI":<6}'
        f'{"RSRP":>7}{"RSRQ":>7}{"SINR":>7}'
        f'  {"Seen":>8}'
        f'{RESET}'
    )
    lines.append(f'  {DIM}{"─"*6}{"─"*9}{"─"*6}{"─"*7}{"─"*7}{"─"*7}  {"─"*8}{RESET}')

    if not seen:
        lines.append(f'  {DIM}no neighbors yet{RESET}')
    else:
        for key, cell in seen.items():
            rsrp_n = int(cell['rsrp']) if cell.get('rsrp', '') not in ('', None) else None
            c = _rsrp_color(rsrp_n)
            ago = _ago(cell['_ts'])
            lines.append(
                f'  '
                f'{_val(cell.get("band")):<6}'
                f'{_val(cell.get("earfcn")):<9}'
                f'{_val(cell.get("pci")):<6}'
                f'{c}{_val(cell.get("rsrp")):>7}{RESET}'
                f'{_val(cell.get("rsrq")):>7}'
                f'{_val(cell.get("sinr")):>7}'
                f'  {DIM}{ago:>8}{RESET}'
            )

    lines.append('')
    lines.append(f'  {DIM}Updated {_ago(last_ok)} ago · interval 5s · fw {stats.get("wa_inner_version","")}{RESET}')
    return '\n'.join(lines)


# ── main loop ─────────────────────────────────────────────────────────────────

NEIGHBOR_TTL = 60  # seconds

def run(host: Optional[str] = None, username: Optional[str] = None,
        password: Optional[str] = None, interval: int = 5):
    mon = ZTEF50Monitor(host, username, password)
    seen: OrderedDict[str, dict] = OrderedDict()
    last_ok = time.time()
    last_stats: Dict[str, Any] = {}
    error: Optional[str] = None
    failures = 0
    backoff = interval

    try:
        while True:
            data = mon.fetch()
            now_ts = time.time()

            if data is None:
                failures += 1
                error = f'fetch failed ({failures}× in a row)'
                # exponential backoff: 5s → 10s → 20s → … → 60s max
                # avoids hammering login endpoint while device reboots
                backoff = min(interval * (2 ** (failures - 1)), 60)
                mon._new_session()
                mon.logged_in = False
            else:
                failures = 0
                backoff = interval
                error = None
                last_ok = now_ts
                last_stats = data

                # merge neighbors into seen-dict keyed by (band,pci)
                nb = data.get('neighbor_cell_info') or []
                if isinstance(nb, list):
                    for cell in nb:
                        key = f'B{cell.get("band","?")} PCI{cell.get("pci","?")}'
                        seen[key] = {**cell, '_ts': now_ts}

            # evict stale entries
            cutoff = now_ts - NEIGHBOR_TTL
            stale = [k for k, v in seen.items() if v['_ts'] < cutoff]
            for k in stale:
                del seen[k]

            # sort by rsrp desc
            sorted_seen: OrderedDict = OrderedDict(
                sorted(seen.items(),
                       key=lambda kv: int(kv[1].get('rsrp') or -999),
                       reverse=True))

            _clear()
            print(render(last_stats, sorted_seen, last_ok, error))
            sys.stdout.flush()

            time.sleep(backoff)

    except KeyboardInterrupt:
        _clear()
        mon._logout()
        print('Bye.')


def main():
    run()

if __name__ == '__main__':
    main()
